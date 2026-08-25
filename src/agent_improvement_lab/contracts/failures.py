"""Evaluation score, failure, annotation, and cluster contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from agent_improvement_lab.contracts.common import ContractModel, require_aware_utc


class FailureCategory(StrEnum):
    """Shared failure categories."""

    PLANNING = "planning"
    TOOL_SELECTION = "tool_selection"
    ARGUMENTS = "arguments"
    TRAJECTORY = "trajectory"
    GROUNDING = "grounding"
    CONTEXT = "context"
    SAFETY = "safety"
    QUALITY = "quality"
    EFFICIENCY = "efficiency"


class Severity(StrEnum):
    """Severity used by evaluators and reviewers."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnnotationStatus(StrEnum):
    """Human-review lifecycle state."""

    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    REGRESSION_CANDIDATE = "regression_candidate"
    GOLDEN = "golden"


class SamplingReason(StrEnum):
    """Reason that a completed session enters human review sampling."""

    THUMBS_DOWN = "thumbs_down"
    DETERMINISTIC_VERIFICATION_FAILURE = "deterministic_verification_failure"
    LOW_JUDGE_CONFIDENCE = "low_judge_confidence"
    CRITIC_REJECTION = "critic_rejection"
    TOOL_ERROR = "tool_error"
    REPEATED_CLARIFICATION = "repeated_clarification"
    EXCESSIVE_LATENCY = "excessive_latency"
    EXCESSIVE_TOKENS = "excessive_tokens"
    UNRECOGNIZED_INTENT = "unrecognized_intent"


class EvaluationScore(ContractModel):
    """One evaluator result normalized to a zero-to-one score."""

    score_id: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    explanation: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = ()
    failure_category: FailureCategory | None = None
    created_at: datetime

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "EvaluationScore":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class EvaluationFailure(ContractModel):
    """A normalized evaluator failure."""

    failure_id: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    category: FailureCategory
    severity: Severity
    case_id: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    score_id: str | None = None
    summary: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    observed_behavior: str = Field(min_length=1)
    created_at: datetime
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    runtime_component: str = Field(default="unknown", min_length=1)
    tags: tuple[str, ...] = ()
    intent: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope(self) -> "EvaluationFailure":
        if not any((self.case_id, self.trace_id, self.session_id)):
            raise ValueError("Failure must reference a case, trace, or session")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class FailureCluster(ContractModel):
    """Deterministic group of related failures."""

    cluster_id: str = Field(min_length=1)
    cluster_key: str = Field(min_length=1)
    failure_ids: tuple[str, ...] = Field(min_length=1)
    category: FailureCategory
    title: str = Field(min_length=1)
    created_at: datetime
    evaluator_id: str | None = None
    runtime_component: str | None = None
    tags: tuple[str, ...] = ()
    intent: str | None = None
    title_source: str = Field(default="deterministic", min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "FailureCluster":
        if len(self.failure_ids) != len(set(self.failure_ids)):
            raise ValueError("failure_ids must be unique")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class HumanAnnotation(ContractModel):
    """Human label attached to a failure or failure cluster."""

    annotation_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    status: AnnotationStatus
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    severity: Severity | None = None
    expected_behavior: str = Field(min_length=1)
    notes: str = ""
    label_confidence: float = Field(ge=0.0, le=1.0)
    previous_annotation_id: str | None = None

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "HumanAnnotation":
        object.__setattr__(self, "reviewed_at", require_aware_utc(self.reviewed_at))
        return self


class SamplingEvent(ContractModel):
    """One persisted reason for sampling a completed session."""

    event_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    reason: SamplingReason
    trace_ids: tuple[str, ...] = ()
    created_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_event(self) -> "SamplingEvent":
        if len(self.trace_ids) != len(set(self.trace_ids)):
            raise ValueError("trace_ids must be unique")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self
