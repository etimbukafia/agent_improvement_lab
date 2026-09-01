"""Contracts for human-labelled judge calibration."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    VersionString,
    require_aware_utc,
)


class JudgeReviewReason(StrEnum):
    """Reason to send a judge result to human review."""

    LOW_CONFIDENCE = "low_confidence"
    DISPUTED = "disputed"


class CalibrationLabel(StrEnum):
    """Human ground-truth label for a judge result."""

    PASS = "pass"
    FAIL = "fail"


class JudgeCalibrationVerdict(StrEnum):
    """Result of comparing two rubric evaluations."""

    IMPROVED = "improved"
    REGRESSED = "regressed"
    INCONCLUSIVE = "inconclusive"


class JudgeReviewTarget(ContractModel):
    """A judge result that needs a human label."""

    target_id: str = Field(min_length=1)
    score_id: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    judge_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasons: tuple[JudgeReviewReason, ...] = Field(min_length=1)
    annotation_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_target(self) -> "JudgeReviewTarget":
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("Judge review reasons must be unique")
        if len(self.annotation_ids) != len(set(self.annotation_ids)):
            raise ValueError("Judge review annotation IDs must be unique")
        return self


class JudgeRubric(ContractModel):
    """Versioned judge instructions that are separate from agent artifacts."""

    record_id: str = ""
    rubric_id: str = Field(min_length=1)
    version: VersionString
    evaluator_id: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    criteria: tuple[str, ...] = Field(min_length=1)
    parent_rubric_id: str | None = None
    created_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_rubric(self) -> "JudgeRubric":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        object.__setattr__(self, "record_id", f"{self.rubric_id}:{self.version}")
        if len(self.criteria) != len(set(self.criteria)):
            raise ValueError("Rubric criteria must be unique")
        if self.parent_rubric_id == self.rubric_id:
            raise ValueError("parent_rubric_id must differ from rubric_id")
        return self


class JudgeCalibrationCase(ContractModel):
    """One judge result paired with a human label."""

    case_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    judge_score: float = Field(ge=0.0, le=1.0)
    judge_passed: bool
    judge_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    human_label: CalibrationLabel
    annotation_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_annotations(self) -> "JudgeCalibrationCase":
        if len(self.annotation_ids) != len(set(self.annotation_ids)):
            raise ValueError("Calibration annotation IDs must be unique")
        return self


class JudgeCalibrationDataset(ContractModel):
    """A versioned dataset of judge results with human labels."""

    record_id: str = ""
    dataset_id: str = Field(min_length=1)
    version: VersionString
    rubric_id: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    agent_behavior_fingerprint: str = Field(default="unspecified", min_length=1)
    cases: tuple[JudgeCalibrationCase, ...] = Field(min_length=1)
    excluded_disputed_target_ids: tuple[str, ...] = ()
    source_annotation_ids: tuple[str, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def validate_dataset(self) -> "JudgeCalibrationDataset":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        object.__setattr__(
            self,
            "record_id",
            f"{self.dataset_id}:{self.version}:{self.rubric_id}",
        )
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Calibration case IDs must be unique")
        target_ids = [case.target_id for case in self.cases]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("Calibration target IDs must be unique")
        if len(self.excluded_disputed_target_ids) != len(set(self.excluded_disputed_target_ids)):
            raise ValueError("Excluded disputed target IDs must be unique")
        if len(self.source_annotation_ids) != len(set(self.source_annotation_ids)):
            raise ValueError("Source annotation IDs must be unique")
        if any(case.evaluator_id != self.evaluator_id for case in self.cases):
            raise ValueError("Calibration cases must use the dataset evaluator")
        return self


class JudgeCalibrationMetrics(ContractModel):
    """Agreement and error counts for one calibrated rubric."""

    metrics_id: str = ""
    dataset_id: str = Field(min_length=1)
    dataset_version: VersionString
    rubric_id: str = Field(min_length=1)
    rubric_version: VersionString
    evaluator_id: str = Field(min_length=1)
    agent_behavior_fingerprint: str = Field(default="unspecified", min_length=1)
    case_count: int = Field(ge=1)
    agreement_count: int = Field(ge=0)
    false_positive_count: int = Field(ge=0)
    false_negative_count: int = Field(ge=0)
    agreement_rate: float = Field(ge=0.0, le=1.0)
    created_at: datetime

    @model_validator(mode="after")
    def validate_metrics(self) -> "JudgeCalibrationMetrics":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        object.__setattr__(
            self,
            "metrics_id",
            f"{self.dataset_id}:{self.rubric_id}:{self.rubric_version}",
        )
        if self.agreement_count + self.false_positive_count + self.false_negative_count != (
            self.case_count
        ):
            raise ValueError("Calibration counts must add up to case_count")
        expected_rate = self.agreement_count / self.case_count
        if abs(self.agreement_rate - expected_rate) > 1e-9:
            raise ValueError("agreement_rate must match agreement_count / case_count")
        return self


class JudgeRubricComparison(ContractModel):
    """Compare two rubric versions on the same human-labelled cases."""

    comparison_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    baseline_rubric_id: str = Field(min_length=1)
    baseline_rubric_version: VersionString
    candidate_rubric_id: str = Field(min_length=1)
    candidate_rubric_version: VersionString
    agent_behavior_fingerprint: str = Field(default="unspecified", min_length=1)
    agent_behavior_unchanged: bool
    baseline_agreement_rate: float = Field(ge=0.0, le=1.0)
    candidate_agreement_rate: float = Field(ge=0.0, le=1.0)
    baseline_false_positive_count: int = Field(ge=0)
    candidate_false_positive_count: int = Field(ge=0)
    baseline_false_negative_count: int = Field(ge=0)
    candidate_false_negative_count: int = Field(ge=0)
    verdict: JudgeCalibrationVerdict
    created_at: datetime
    notes: str = ""

    @model_validator(mode="after")
    def validate_comparison(self) -> "JudgeRubricComparison":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        if self.baseline_rubric_id == self.candidate_rubric_id and (
            self.baseline_rubric_version == self.candidate_rubric_version
        ):
            raise ValueError("Rubric comparison needs two versions")
        return self
