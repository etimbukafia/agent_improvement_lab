"""Typed contracts for evidence-backed enterprise improvement decisions.

These contracts stop the improvement workflow at a reviewable decision.  They
do not contain model prompts, hidden reasoning, or executable candidate code.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from enterprise_agent_improvement_lab.contracts.candidates import (
    ArtifactRiskClassification,
    ChangeKind,
)
from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    require_aware_utc,
    utc_now,
)


class RootCauseReviewerStatus(StrEnum):
    """Review state for a causal hypothesis."""

    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class ImprovementDecision(StrEnum):
    """Intervention classes selected before a candidate is built."""

    NO_CHANGE = "no_change"
    PROMPT_CHANGE = ChangeKind.PROMPT_CHANGE.value
    TOOL_ADDITION = ChangeKind.TOOL_ADDITION.value
    TOOL_REMOVAL = ChangeKind.TOOL_REMOVAL.value
    TOOL_CONFIGURATION_CHANGE = ChangeKind.TOOL_CONFIGURATION_CHANGE.value
    POLICY_CHANGE = ChangeKind.POLICY_CHANGE.value
    PERMISSION_CHANGE = ChangeKind.PERMISSION_CHANGE.value
    ROUTING_CHANGE = ChangeKind.ROUTING_CHANGE.value
    MODEL_CHANGE = ChangeKind.MODEL_CHANGE.value
    RETRIEVAL_CHANGE = ChangeKind.RETRIEVAL_CHANGE.value
    MEMORY_CHANGE = ChangeKind.MEMORY_CHANGE.value
    THRESHOLD_CHANGE = ChangeKind.THRESHOLD_CHANGE.value
    WORKFLOW_CHANGE = ChangeKind.WORKFLOW_CHANGE.value
    SKILL_ADDITION = ChangeKind.SKILL_ADDITION.value
    SKILL_REMOVAL = ChangeKind.SKILL_REMOVAL.value
    APPROVAL_RULE_CHANGE = ChangeKind.APPROVAL_RULE_CHANGE.value
    HUMAN_REVIEW_REQUIRED = "human_review_required"

    @classmethod
    def from_change_kind(cls, kind: ChangeKind) -> "ImprovementDecision":
        """Map a typed candidate change to a planner decision."""

        return cls(kind.value)


class RootCauseHypothesis(ContractModel):
    """A concise, evidence-backed explanation for a failure cluster."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    hypothesis_id: str = Field(min_length=1)
    source_cluster_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("source_cluster_id", "cluster_id", "source_cluster"),
    )
    affected_agent_id: str | None = Field(default=None, min_length=1)
    affected_component: str | None = Field(default=None, min_length=1)
    affected_skill: str | None = Field(default=None, min_length=1)
    affected_tool: str | None = Field(default=None, min_length=1)
    affected_policy: str | None = Field(default=None, min_length=1)
    affected_workflow: str | None = Field(default=None, min_length=1)
    suspected_cause: str = Field(min_length=1)
    supporting_evidence: tuple[str, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "supporting_evidence", "supporting_evidence_refs", "evidence_refs"
        ),
    )
    conflicting_evidence: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("conflicting_evidence", "conflicting_evidence_refs"),
    )
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_intervention_classes: tuple[ChangeKind, ...] = Field(
        default=(),
        validation_alias=AliasChoices(
            "suggested_intervention_classes", "suggested_interventions", "intervention_classes"
        ),
    )
    reviewer_status: RootCauseReviewerStatus = Field(
        default=RootCauseReviewerStatus.UNREVIEWED,
        validation_alias=AliasChoices("reviewer_status", "review_status", "status"),
    )
    reviewer: str | None = None
    reviewer_notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("supporting_evidence", "conflicting_evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            values = (value,)
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = tuple(value)
        else:
            raise ValueError("evidence must be a string or sequence of references")
        normalized = tuple(str(item).strip() for item in values)
        if any(not item for item in normalized):
            raise ValueError("evidence references must be non-empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("evidence references must be unique")
        return normalized

    @field_validator("suggested_intervention_classes", mode="before")
    @classmethod
    def normalize_interventions(cls, value: object) -> tuple[ChangeKind, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, ChangeKind)):
            value = (value,)
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("intervention classes must be a string or sequence")
        return tuple(ChangeKind(item) for item in value)

    @model_validator(mode="after")
    def validate_hypothesis(self) -> "RootCauseHypothesis":
        if set(self.supporting_evidence) & set(self.conflicting_evidence):
            raise ValueError("supporting and conflicting evidence cannot overlap")
        if len(self.suggested_intervention_classes) != len(
            set(self.suggested_intervention_classes)
        ):
            raise ValueError("suggested intervention classes must be unique")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self

    @property
    def supporting_evidence_refs(self) -> tuple[str, ...]:
        """Return safe evidence IDs using the explicit reference name."""

        return self.supporting_evidence

    @property
    def conflicting_evidence_refs(self) -> tuple[str, ...]:
        """Return safe conflicting evidence IDs."""

        return self.conflicting_evidence

    @property
    def effective_confidence(self) -> float:
        """Apply a small deterministic penalty for conflicting evidence."""

        penalty = min(0.25, 0.05 * len(self.conflicting_evidence))
        return max(0.0, self.confidence - penalty)


class PriorExperimentEvidence(ContractModel):
    """Safe evidence from an earlier candidate experiment."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    experiment_id: str = Field(min_length=1)
    comparison_id: str | None = Field(default=None, min_length=1)
    verdict: str = Field(min_length=1)
    metric_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    summary: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_evidence(self) -> "PriorExperimentEvidence":
        for name, values in (
            ("metric_ids", self.metric_ids),
            ("evidence_refs", self.evidence_refs),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty values")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique values")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class ImprovementPlan(ContractModel):
    """A deterministic, bounded intervention decision."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    plan_id: str = Field(min_length=1)
    source_cluster_id: str = Field(min_length=1)
    root_cause_hypothesis_ids: tuple[str, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices("root_cause_hypothesis_ids", "hypothesis_ids"),
    )
    decision: ImprovementDecision = Field(
        validation_alias=AliasChoices("decision", "selected_intervention", "intervention")
    )
    rationale: str = Field(min_length=1)
    expected_affected_metrics: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("expected_affected_metrics", "expected_metrics"),
    )
    risk: ArtifactRiskClassification = Field(
        default=ArtifactRiskClassification.MEDIUM,
        validation_alias=AliasChoices("risk", "risk_classification"),
    )
    required_approvals: tuple[str, ...] = ()
    candidate_builder_type: str = Field(
        min_length=1,
        validation_alias=AliasChoices("candidate_builder_type", "builder_type"),
    )
    source_failure_ids: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    current_candidate_id: str = Field(min_length=1)
    registry_snapshot_id: str | None = Field(default=None, min_length=1)
    prior_experiment_ids: tuple[str, ...] = ()
    requires_human_review: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "root_cause_hypothesis_ids",
        "expected_affected_metrics",
        "required_approvals",
        "source_failure_ids",
        "evidence_refs",
        "prior_experiment_ids",
        mode="before",
    )
    @classmethod
    def normalize_id_sequences(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            values = (value,)
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = tuple(value)
        else:
            raise ValueError("references must be a string or sequence")
        normalized = tuple(str(item).strip() for item in values)
        if any(not item for item in normalized):
            raise ValueError("references must contain non-empty values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("references must contain unique values")
        return normalized

    @model_validator(mode="after")
    def validate_plan(self) -> "ImprovementPlan":
        is_review = self.decision == ImprovementDecision.HUMAN_REVIEW_REQUIRED
        if is_review and not self.requires_human_review:
            object.__setattr__(self, "requires_human_review", True)
        if self.decision in {
            ImprovementDecision.NO_CHANGE,
            ImprovementDecision.HUMAN_REVIEW_REQUIRED,
        }:
            if self.candidate_builder_type not in {"none", "human_review"}:
                raise ValueError("non-building decisions need a non-building builder type")
        elif self.candidate_builder_type in {"none", "human_review"}:
            raise ValueError("change decisions need a specialized candidate builder")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self

    @property
    def selected_intervention(self) -> ImprovementDecision:
        """Return the selected decision using the plan vocabulary."""

        return self.decision


# Compatibility names make the phase boundary discoverable without creating
# another competing contract hierarchy.
ReviewerStatus = RootCauseReviewerStatus
PlannerDecision = ImprovementDecision
InterventionKind = ImprovementDecision


__all__ = [
    "ImprovementDecision",
    "ImprovementPlan",
    "InterventionKind",
    "PlannerDecision",
    "PriorExperimentEvidence",
    "ReviewerStatus",
    "RootCauseHypothesis",
    "RootCauseReviewerStatus",
]
