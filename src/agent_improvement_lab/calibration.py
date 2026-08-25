"""Judge review, calibration, and rubric comparison workflow."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from hashlib import sha256

from agent_improvement_lab.contracts.calibration import (
    CalibrationLabel,
    JudgeCalibrationCase,
    JudgeCalibrationDataset,
    JudgeCalibrationMetrics,
    JudgeCalibrationVerdict,
    JudgeReviewReason,
    JudgeReviewTarget,
    JudgeRubric,
    JudgeRubricComparison,
)
from agent_improvement_lab.contracts.common import utc_now
from agent_improvement_lab.contracts.failures import (
    AnnotationStatus,
    EvaluationFailure,
    EvaluationScore,
    HumanAnnotation,
)


class CalibrationError(ValueError):
    """Raised when calibration evidence cannot be compared safely."""


_FAILURE_LABELS = frozenset(
    {
        AnnotationStatus.CONFIRMED,
        AnnotationStatus.REGRESSION_CANDIDATE,
        AnnotationStatus.GOLDEN,
    }
)


def identify_judge_review_targets(
    scores: Sequence[EvaluationScore],
    annotations: Sequence[HumanAnnotation],
    *,
    judge_evaluator_ids: Sequence[str] = (),
    low_confidence_threshold: float = 0.6,
    failures: Sequence[EvaluationFailure] = (),
) -> tuple[JudgeReviewTarget, ...]:
    """Find low-confidence and disputed judge results."""

    if not 0.0 <= low_confidence_threshold <= 1.0:
        raise ValueError("low_confidence_threshold must be between 0 and 1")
    allowed = set(judge_evaluator_ids)
    grouped: dict[str, list[HumanAnnotation]] = defaultdict(list)
    for annotation in annotations:
        grouped[annotation.target_id].append(annotation)
    failure_score_ids = {
        failure.failure_id: failure.score_id for failure in failures if failure.score_id is not None
    }
    for annotation in annotations:
        score_id = failure_score_ids.get(annotation.target_id)
        if score_id is not None:
            grouped[score_id].append(annotation)
    targets: list[JudgeReviewTarget] = []
    for score in sorted(scores, key=lambda item: item.score_id):
        if allowed and score.evaluator_id not in allowed:
            continue
        if not allowed and not score.evaluator_id.casefold().startswith("judge."):
            continue
        target_annotations = _unique_annotations(grouped.get(score.score_id, ()))
        reasons: list[JudgeReviewReason] = []
        if score.confidence is None or score.confidence < low_confidence_threshold:
            reasons.append(JudgeReviewReason.LOW_CONFIDENCE)
        if _is_disputed(target_annotations):
            reasons.append(JudgeReviewReason.DISPUTED)
        if reasons:
            targets.append(
                JudgeReviewTarget(
                    target_id=score.score_id,
                    score_id=score.score_id,
                    evaluator_id=score.evaluator_id,
                    judge_confidence=score.confidence,
                    reasons=tuple(reasons),
                    annotation_ids=tuple(item.annotation_id for item in target_annotations),
                )
            )
    return tuple(targets)


def create_judge_calibration_dataset(
    scores: Sequence[EvaluationScore],
    annotations: Sequence[HumanAnnotation],
    rubric: JudgeRubric,
    *,
    dataset_id: str,
    version: str,
    agent_behavior_fingerprint: str = "unspecified",
    failures: Sequence[EvaluationFailure] = (),
    created_at: datetime | None = None,
) -> JudgeCalibrationDataset:
    """Create a calibration dataset from resolved human labels."""

    grouped: dict[str, list[HumanAnnotation]] = defaultdict(list)
    for annotation in annotations:
        grouped[annotation.target_id].append(annotation)
    failure_score_ids = {
        failure.failure_id: failure.score_id for failure in failures if failure.score_id is not None
    }
    for annotation in annotations:
        score_id = failure_score_ids.get(annotation.target_id)
        if score_id is not None:
            grouped[score_id].append(annotation)
    cases: list[JudgeCalibrationCase] = []
    excluded: list[str] = []
    source_annotation_ids: list[str] = []
    seen_score_ids: set[str] = set()
    for score in sorted(scores, key=lambda item: item.score_id):
        if score.evaluator_id != rubric.evaluator_id:
            continue
        if score.score_id in seen_score_ids:
            raise CalibrationError(f"Duplicate judge score ID: {score.score_id}")
        seen_score_ids.add(score.score_id)
        target_annotations = tuple(
            annotation
            for annotation in _unique_annotations(grouped.get(score.score_id, ()))
            if annotation.status != AnnotationStatus.UNREVIEWED
        )
        label = _human_label(target_annotations)
        if label is None:
            if _is_disputed(target_annotations):
                excluded.append(score.score_id)
            continue
        annotation_ids = tuple(item.annotation_id for item in target_annotations)
        source_annotation_ids.extend(annotation_ids)
        cases.append(
            JudgeCalibrationCase(
                case_id=f"{dataset_id}:{version}:{score.score_id}",
                target_id=score.score_id,
                evaluator_id=score.evaluator_id,
                judge_score=score.score,
                judge_passed=score.passed,
                judge_confidence=score.confidence,
                human_label=label,
                annotation_ids=annotation_ids,
            )
        )
    if not cases:
        raise CalibrationError("No resolved human labels match the judge rubric")
    return JudgeCalibrationDataset(
        dataset_id=dataset_id,
        version=version,
        rubric_id=rubric.rubric_id,
        evaluator_id=rubric.evaluator_id,
        agent_behavior_fingerprint=agent_behavior_fingerprint,
        cases=tuple(cases),
        excluded_disputed_target_ids=tuple(excluded),
        source_annotation_ids=tuple(dict.fromkeys(source_annotation_ids)),
        created_at=created_at or utc_now(),
    )


def calculate_judge_metrics(
    dataset: JudgeCalibrationDataset,
    rubric: JudgeRubric,
    *,
    created_at: datetime | None = None,
) -> JudgeCalibrationMetrics:
    """Calculate deterministic agreement and error counts."""

    if dataset.rubric_id != rubric.rubric_id or dataset.evaluator_id != rubric.evaluator_id:
        raise CalibrationError("Calibration dataset and rubric do not match")
    agreement = 0
    false_positive = 0
    false_negative = 0
    for case in dataset.cases:
        human_passed = case.human_label == CalibrationLabel.PASS
        if case.judge_passed == human_passed:
            agreement += 1
        elif case.judge_passed and not human_passed:
            false_positive += 1
        else:
            false_negative += 1
    return JudgeCalibrationMetrics(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.version,
        evaluator_id=rubric.evaluator_id,
        agent_behavior_fingerprint=dataset.agent_behavior_fingerprint,
        case_count=len(dataset.cases),
        agreement_count=agreement,
        false_positive_count=false_positive,
        false_negative_count=false_negative,
        agreement_rate=agreement / len(dataset.cases),
        created_at=created_at or utc_now(),
    )


def compare_judge_calibrations(
    baseline: JudgeCalibrationMetrics,
    candidate: JudgeCalibrationMetrics,
    *,
    agent_behavior_unchanged: bool,
    created_at: datetime | None = None,
) -> JudgeRubricComparison:
    """Compare two rubric versions without changing the agent under test."""

    if baseline.dataset_id != candidate.dataset_id:
        raise CalibrationError("Judge calibrations must use the same dataset")
    if baseline.dataset_version != candidate.dataset_version:
        raise CalibrationError("Judge calibrations must use the same dataset version")
    if baseline.evaluator_id != candidate.evaluator_id:
        raise CalibrationError("Judge calibrations must use the same evaluator")
    if baseline.agent_behavior_fingerprint != candidate.agent_behavior_fingerprint:
        raise CalibrationError("Judge calibrations must use the same agent behavior fingerprint")
    if baseline.case_count != candidate.case_count:
        raise CalibrationError("Judge calibrations must use the same case count")
    better = (
        candidate.agreement_rate >= baseline.agreement_rate
        and candidate.false_positive_count <= baseline.false_positive_count
        and candidate.false_negative_count <= baseline.false_negative_count
        and (
            candidate.agreement_rate > baseline.agreement_rate
            or candidate.false_positive_count < baseline.false_positive_count
            or candidate.false_negative_count < baseline.false_negative_count
        )
    )
    worse = (
        candidate.agreement_rate < baseline.agreement_rate
        or candidate.false_positive_count > baseline.false_positive_count
        or candidate.false_negative_count > baseline.false_negative_count
    )
    if not agent_behavior_unchanged or baseline.agent_behavior_fingerprint == "unspecified":
        verdict = JudgeCalibrationVerdict.INCONCLUSIVE
        notes = "A stable unchanged-agent fingerprint is required to isolate rubric effects."
    elif better:
        verdict = JudgeCalibrationVerdict.IMPROVED
        notes = "The candidate rubric improves calibration on unchanged agent behavior."
    elif worse:
        verdict = JudgeCalibrationVerdict.REGRESSED
        notes = "The candidate rubric has worse calibration errors."
    else:
        verdict = JudgeCalibrationVerdict.INCONCLUSIVE
        notes = "Calibration metrics did not show a strict improvement."
    digest = sha256(
        f"{baseline.rubric_id}:{baseline.rubric_version}:{candidate.rubric_id}:"
        f"{candidate.rubric_version}:{baseline.dataset_id}".encode("utf-8")
    ).hexdigest()[:16]
    return JudgeRubricComparison(
        comparison_id=f"rubric-comparison-{digest}",
        dataset_id=baseline.dataset_id,
        baseline_rubric_id=baseline.rubric_id,
        baseline_rubric_version=baseline.rubric_version,
        candidate_rubric_id=candidate.rubric_id,
        candidate_rubric_version=candidate.rubric_version,
        agent_behavior_fingerprint=baseline.agent_behavior_fingerprint,
        agent_behavior_unchanged=agent_behavior_unchanged,
        baseline_agreement_rate=baseline.agreement_rate,
        candidate_agreement_rate=candidate.agreement_rate,
        baseline_false_positive_count=baseline.false_positive_count,
        candidate_false_positive_count=candidate.false_positive_count,
        baseline_false_negative_count=baseline.false_negative_count,
        candidate_false_negative_count=candidate.false_negative_count,
        verdict=verdict,
        created_at=created_at or utc_now(),
        notes=notes,
    )


def _is_disputed(annotations: Sequence[HumanAnnotation]) -> bool:
    labels = {
        "fail" if annotation.status in _FAILURE_LABELS else "pass"
        for annotation in annotations
        if annotation.status == AnnotationStatus.REJECTED or annotation.status in _FAILURE_LABELS
    }
    return len(labels) > 1


def _unique_annotations(annotations: Sequence[HumanAnnotation]) -> tuple[HumanAnnotation, ...]:
    """Return one stable record for each annotation ID."""

    by_id = {annotation.annotation_id: annotation for annotation in annotations}
    return tuple(sorted(by_id.values(), key=lambda item: (item.reviewed_at, item.annotation_id)))


def _human_label(annotations: Sequence[HumanAnnotation]) -> CalibrationLabel | None:
    if _is_disputed(annotations):
        return None
    if any(annotation.status in _FAILURE_LABELS for annotation in annotations):
        return CalibrationLabel.FAIL
    if any(annotation.status == AnnotationStatus.REJECTED for annotation in annotations):
        return CalibrationLabel.PASS
    return None
