"""Human annotation workflow and regression-case generation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agent_improvement_lab.contracts.cases import (
    CaseProvenance,
    DatasetSplit,
    DatasetVersion,
    EvaluationCaseRef,
    RiskLevel,
)
from agent_improvement_lab.contracts.common import require_aware_utc
from agent_improvement_lab.contracts.failures import (
    AnnotationStatus,
    EvaluationFailure,
    HumanAnnotation,
    Severity,
)
from agent_improvement_lab.storage import AnnotationRepository


class AnnotationTransitionError(ValueError):
    """Raised when an annotation skips or repeats a lifecycle state."""


class AnnotationConflictError(ValueError):
    """Raised when human labels for one target disagree."""


_ALLOWED_TRANSITIONS: dict[AnnotationStatus, frozenset[AnnotationStatus]] = {
    AnnotationStatus.UNREVIEWED: frozenset({AnnotationStatus.CONFIRMED, AnnotationStatus.REJECTED}),
    AnnotationStatus.CONFIRMED: frozenset({AnnotationStatus.REGRESSION_CANDIDATE}),
    AnnotationStatus.REGRESSION_CANDIDATE: frozenset({AnnotationStatus.GOLDEN}),
    AnnotationStatus.REJECTED: frozenset(),
    AnnotationStatus.GOLDEN: frozenset(),
}


def transition_annotation(
    current: HumanAnnotation,
    *,
    annotation_id: str,
    status: AnnotationStatus,
    reviewer: str,
    reviewed_at: datetime,
    expected_behavior: str | None = None,
    severity: Severity | None = None,
    notes: str | None = None,
    label_confidence: float | None = None,
) -> HumanAnnotation:
    """Create the next append-only annotation in a valid lifecycle."""

    allowed = _ALLOWED_TRANSITIONS[current.status]
    if status not in allowed:
        raise AnnotationTransitionError(
            f"Cannot move annotation from {current.status.value} to {status.value}"
        )
    return HumanAnnotation(
        annotation_id=annotation_id,
        target_id=current.target_id,
        target_type=current.target_type,
        status=status,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        severity=severity if severity is not None else current.severity,
        expected_behavior=expected_behavior or current.expected_behavior,
        notes=notes if notes is not None else current.notes,
        label_confidence=(
            label_confidence if label_confidence is not None else current.label_confidence
        ),
        previous_annotation_id=current.annotation_id,
    )


def validate_annotation_conflict(
    annotations: Sequence[HumanAnnotation], candidate: HumanAnnotation
) -> None:
    """Reject confirmed and rejected labels for the same target."""

    same_target = [
        item
        for item in annotations
        if item.target_id == candidate.target_id and item.target_type == candidate.target_type
    ]
    positive = {
        AnnotationStatus.CONFIRMED,
        AnnotationStatus.REGRESSION_CANDIDATE,
        AnnotationStatus.GOLDEN,
    }
    has_positive = candidate.status in positive or any(
        item.status in positive for item in same_target
    )
    has_rejected = candidate.status == AnnotationStatus.REJECTED or any(
        item.status == AnnotationStatus.REJECTED for item in same_target
    )
    if has_positive and has_rejected:
        raise AnnotationConflictError(
            f"Conflicting labels exist for {candidate.target_type}/{candidate.target_id}"
        )


class AnnotationService:
    """Persist append-only human annotations and enforce their lifecycle."""

    def __init__(self, repository: AnnotationRepository) -> None:
        self.repository = repository

    def create_unreviewed(
        self,
        *,
        annotation_id: str,
        target_id: str,
        target_type: str,
        reviewer: str,
        reviewed_at: datetime,
        expected_behavior: str,
        label_confidence: float,
        severity: Severity | None = None,
        notes: str | None = None,
    ) -> HumanAnnotation:
        """Create and persist the first annotation for a target."""

        annotation = HumanAnnotation(
            annotation_id=annotation_id,
            target_id=target_id,
            target_type=target_type,
            status=AnnotationStatus.UNREVIEWED,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            severity=severity,
            expected_behavior=expected_behavior,
            notes=notes or "",
            label_confidence=label_confidence,
        )
        validate_annotation_conflict(self.repository.list(), annotation)
        self.repository.save(annotation)
        return annotation

    def transition(
        self,
        current: HumanAnnotation | str,
        *,
        annotation_id: str,
        status: AnnotationStatus,
        reviewer: str,
        reviewed_at: datetime,
        expected_behavior: str | None = None,
        severity: Severity | None = None,
        notes: str | None = None,
        label_confidence: float | None = None,
    ) -> HumanAnnotation:
        """Persist a valid next annotation state."""

        current_record = self.repository.get(current) if isinstance(current, str) else current
        if current_record is None:
            raise KeyError(f"Annotation {current!r} was not found")
        annotation = transition_annotation(
            current_record,
            annotation_id=annotation_id,
            status=status,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            expected_behavior=expected_behavior,
            severity=severity,
            notes=notes,
            label_confidence=label_confidence,
        )
        validate_annotation_conflict(self.repository.list(), annotation)
        self.repository.save(annotation)
        return annotation


class RegressionCaseGenerator:
    """Create versioned regression cases from confirmed human annotations."""

    _ELIGIBLE_STATUSES = frozenset(
        {
            AnnotationStatus.CONFIRMED,
            AnnotationStatus.REGRESSION_CANDIDATE,
            AnnotationStatus.GOLDEN,
        }
    )

    def case_from_annotation(
        self,
        annotation: HumanAnnotation,
        source_case: EvaluationCaseRef,
        *,
        dataset_id: str,
        dataset_version: str,
        case_id: str | None = None,
    ) -> EvaluationCaseRef:
        """Create one regression case from one eligible annotation."""

        self._require_eligible(annotation)
        new_case_id = case_id or f"{source_case.case_id}:regression:{annotation.annotation_id}"
        metadata = dict(source_case.metadata)
        metadata.update(
            {
                "regression_annotation_id": annotation.annotation_id,
                "source_case_id": source_case.case_id,
                "expected_behavior": annotation.expected_behavior,
            }
        )
        expected = dict(source_case.expected)
        expected["human_expected_behavior"] = annotation.expected_behavior
        tags = tuple(sorted(set(source_case.tags) | {"regression"}))
        reviewed_at = require_aware_utc(annotation.reviewed_at)
        return EvaluationCaseRef(
            case_id=new_case_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            split=DatasetSplit.REGRESSION,
            risk=_risk_for(annotation.severity),
            tags=tags,
            input=dict(source_case.input),
            expected=expected,
            tool_expectations=source_case.tool_expectations,
            provenance=CaseProvenance(
                source="human_annotation",
                source_ref=annotation.annotation_id,
                collected_at=reviewed_at,
                reviewer=annotation.reviewer,
                notes=annotation.notes or None,
            ),
            metadata=metadata,
        )

    def dataset_from_annotations(
        self,
        source_dataset: DatasetVersion,
        annotations: Sequence[HumanAnnotation],
        *,
        version: str,
        failures: Sequence[EvaluationFailure] = (),
        created_at: datetime | None = None,
        description: str | None = None,
    ) -> DatasetVersion:
        """Create a new regression dataset from eligible annotations."""

        eligible = sorted(
            (
                annotation
                for annotation in annotations
                if annotation.status in self._ELIGIBLE_STATUSES
            ),
            key=lambda annotation: annotation.annotation_id,
        )
        if not eligible:
            raise ValueError("At least one confirmed annotation is required")
        source_cases = {case.case_id: case for case in source_dataset.cases}
        failure_cases = {failure.failure_id: failure.case_id for failure in failures}
        cases: list[EvaluationCaseRef] = []
        for annotation in eligible:
            source_case_id = annotation.target_id
            if annotation.target_type == "failure":
                source_case_id = failure_cases.get(annotation.target_id) or ""
            source_case = source_cases.get(source_case_id)
            if source_case is None:
                raise ValueError(
                    f"Annotation {annotation.annotation_id} does not reference a source case"
                )
            cases.append(
                self.case_from_annotation(
                    annotation,
                    source_case,
                    dataset_id=source_dataset.dataset_id,
                    dataset_version=version,
                )
            )
        timestamp = require_aware_utc(created_at or max(item.reviewed_at for item in eligible))
        return DatasetVersion(
            dataset_id=source_dataset.dataset_id,
            version=version,
            description=description
            or f"Regression cases generated from {len(eligible)} human annotations.",
            cases=tuple(cases),
            provenance=CaseProvenance(
                source="human_annotation",
                source_ref=source_dataset.dataset_id,
                collected_at=timestamp,
                notes="Generated from confirmed SME annotations.",
            ),
            parent_version=source_dataset.version,
            created_at=timestamp,
            metadata={
                "source_dataset_version": source_dataset.version,
                "annotation_ids": ",".join(annotation.annotation_id for annotation in eligible),
            },
        )

    @classmethod
    def _require_eligible(cls, annotation: HumanAnnotation) -> None:
        if annotation.status not in cls._ELIGIBLE_STATUSES:
            raise ValueError(
                f"Annotation {annotation.annotation_id} is not eligible: {annotation.status.value}"
            )


def _risk_for(severity: Severity | None) -> RiskLevel:
    if severity in {Severity.CRITICAL}:
        return RiskLevel.CRITICAL
    if severity == Severity.HIGH:
        return RiskLevel.HIGH
    if severity == Severity.LOW:
        return RiskLevel.LOW
    return RiskLevel.MEDIUM
