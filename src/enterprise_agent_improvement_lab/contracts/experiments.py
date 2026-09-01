"""Experiment, comparison, and promotion contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from enterprise_agent_improvement_lab.contracts.cases import DatasetSplit
from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    VersionString,
    require_aware_utc,
)
from enterprise_agent_improvement_lab.contracts.environments import EnvironmentSnapshot


class RunStatus(StrEnum):
    """Execution state for an experiment run."""

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ComparisonVerdict(StrEnum):
    """Result of a baseline-versus-candidate comparison."""

    IMPROVED = "improved"
    REGRESSED = "regressed"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"


class PromotionOutcome(StrEnum):
    """Human decision for a candidate."""

    APPROVED = "approved"
    REJECTED = "rejected"
    REVIEW = "review"
    ROLLBACK = "rollback"


class PromotionGateKind(StrEnum):
    """Classify a promotion gate by its blocking effect."""

    HARD = "hard"
    SOFT = "soft"


class EnterpriseComparisonDimension(StrEnum):
    """Dimensions that can affect enterprise promotion safety."""

    SECURITY = "security"
    STATE_INTEGRITY = "state_integrity"
    AUTHORIZATION = "authorization"
    TENANT_BOUNDARY = "tenant_boundary"
    APPROVALS = "approvals"
    WORKFLOW_COMPLETION = "workflow_completion"
    BUSINESS_OUTCOMES = "business_outcomes"
    COST = "cost"
    LATENCY = "latency"
    TOKEN_USAGE = "token_usage"
    TOOL_SIDE_EFFECTS = "tool_side_effects"
    DELEGATION = "delegation"
    RELIABILITY = "reliability"
    POLICY = "policy"


class ComparisonDimensionWeight(ContractModel):
    """Risk weight and hard-gate rule for one comparison dimension."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    dimension: EnterpriseComparisonDimension
    weight: float = Field(default=1.0, ge=0.0)
    hard: bool = False

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("comparison dimension weights must be finite")
        return value


class EnterpriseComparisonMetric(ContractModel):
    """One risk-weighted enterprise metric comparison."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    metric_id: str = Field(min_length=1)
    dimension: EnterpriseComparisonDimension
    evaluator_family: str = Field(min_length=1)
    metric_name: str = Field(default="value", min_length=1)
    baseline_value: float
    candidate_value: float
    higher_is_better: bool = True
    risk_weight: float = Field(default=1.0, ge=0.0)
    hard: bool = False
    evidence_refs: tuple[str, ...] = ()

    @field_validator("baseline_value", "candidate_value")
    @classmethod
    def validate_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("comparison metric values must be finite")
        return value

    @model_validator(mode="after")
    def validate_metric(self) -> "EnterpriseComparisonMetric":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("comparison metric evidence_refs must be unique")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("comparison metric evidence_refs must be non-empty")
        return self

    @property
    def delta(self) -> float:
        """Return candidate minus baseline."""

        return self.candidate_value - self.baseline_value

    @property
    def regressed(self) -> bool:
        """Return whether the candidate value is directionally worse."""

        return (
            self.candidate_value < self.baseline_value
            if self.higher_is_better
            else self.candidate_value > self.baseline_value
        )

    @property
    def weighted_loss(self) -> float:
        """Return a non-negative loss weighted by enterprise risk."""

        if not self.regressed:
            return 0.0
        loss = (
            self.baseline_value - self.candidate_value
            if self.higher_is_better
            else self.candidate_value - self.baseline_value
        )
        return max(0.0, loss) * self.risk_weight


class EvaluatorFamilyAggregate(ContractModel):
    """Aggregate evidence for one evaluator family."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    family: str = Field(min_length=1)
    metric_ids: tuple[str, ...] = ()
    baseline_score: float
    candidate_score: float
    regression_count: int = Field(default=0, ge=0)
    risk_weighted_loss: float = Field(default=0.0, ge=0.0)
    regressed: bool = False

    @model_validator(mode="after")
    def validate_family(self) -> "EvaluatorFamilyAggregate":
        if not isfinite(self.baseline_score) or not isfinite(self.candidate_score):
            raise ValueError("evaluator family scores must be finite")
        if len(self.metric_ids) != len(set(self.metric_ids)):
            raise ValueError("metric_ids must be unique")
        return self


class RunManifest(ContractModel):
    """Inputs that make an experiment run reproducible."""

    run_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: VersionString
    candidate_id: str = Field(min_length=1)
    candidate_artifact_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices(
            "candidate_artifact_ids",
            "artifact_ids",
            "candidate_artifacts",
        ),
    )
    # These prompt-era fields remain readable for old manifests only. New
    # producers should use candidate_artifact_ids and the explicit snapshot.
    prompt_artifact_ids: tuple[str, ...] = ()
    toolset: tuple[str, ...] = ()
    runtime_name: str = Field(min_length=1)
    runtime_version: VersionString
    provider: str | None = None
    model: str | None = None
    seed: int | None = None
    configuration_artifact_ids: tuple[str, ...] = ()
    environment_snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices(
            "environment_snapshot_id",
            "environment_snapshot_ref",
        ),
    )
    environment_snapshot: EnvironmentSnapshot | None = Field(default=None, exclude=True)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def accept_environment_snapshot(cls, value: Any) -> Any:
        """Accept a snapshot object while persisting only its stable reference."""

        if not isinstance(value, dict) or "environment_snapshot" not in value:
            return value
        data = dict(value)
        raw_snapshot = data.pop("environment_snapshot")
        snapshot = EnvironmentSnapshot.model_validate(raw_snapshot)
        existing = data.get("environment_snapshot_id", data.get("environment_snapshot_ref"))
        if existing is not None and existing != snapshot.identity:
            raise ValueError("environment_snapshot_id must match environment_snapshot")
        data["environment_snapshot_id"] = snapshot.identity
        data["environment_snapshot"] = snapshot
        return data

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "RunManifest":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        if len(self.candidate_artifact_ids) != len(set(self.candidate_artifact_ids)):
            raise ValueError("candidate_artifact_ids must contain unique IDs")
        if len(self.prompt_artifact_ids) != len(set(self.prompt_artifact_ids)):
            raise ValueError("prompt_artifact_ids must contain unique IDs")
        if len(self.configuration_artifact_ids) != len(set(self.configuration_artifact_ids)):
            raise ValueError("configuration_artifact_ids must contain unique IDs")
        if len(self.toolset) != len(set(self.toolset)):
            raise ValueError("toolset must contain unique IDs")
        if self.environment_snapshot is not None:
            if self.environment_snapshot_id != self.environment_snapshot.identity:
                raise ValueError("environment_snapshot_id must match environment_snapshot")
        if self.environment_snapshot_id is None:
            snapshot = self.legacy_environment_snapshot()
            object.__setattr__(
                self,
                "environment_snapshot_id",
                snapshot.identity,
            )
        return self

    @property
    def environment_snapshot_ref(self) -> str:
        """Return the immutable environment snapshot reference."""

        assert self.environment_snapshot_id is not None
        return self.environment_snapshot_id

    def legacy_environment_snapshot(self) -> EnvironmentSnapshot:
        """Return the compatibility snapshot derived from legacy manifest fields."""

        return EnvironmentSnapshot.for_legacy_manifest(
            dataset_id=self.dataset_id,
            dataset_version=self.dataset_version,
            toolset=self.toolset,
            runtime_name=self.runtime_name,
            runtime_version=self.runtime_version,
            provider=self.provider,
            model=self.model,
            seed=self.seed,
            captured_at=self.created_at,
        )


class ExperimentRun(ContractModel):
    """Recorded result of executing one run manifest."""

    run_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    manifest: RunManifest
    status: RunStatus
    trace_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()
    score_ids: tuple[str, ...] = ()
    started_at: datetime
    ended_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_run(self) -> "ExperimentRun":
        if self.manifest.run_id != self.run_id:
            raise ValueError("manifest.run_id must match run_id")
        object.__setattr__(self, "started_at", require_aware_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", require_aware_utc(self.ended_at))
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must be after started_at")
        if self.status == RunStatus.FAILED and not self.error:
            raise ValueError("error is required for a failed run")
        if self.status != RunStatus.FAILED and self.error is not None:
            raise ValueError("error is only allowed for a failed run")
        return self


class ComparisonMetric(ContractModel):
    """One metric in a baseline comparison."""

    metric_id: str = Field(min_length=1)
    baseline_value: float
    candidate_value: float
    higher_is_better: bool
    dimension: str = "overall"
    slice_key: str = "all"
    metric_name: str = "value"

    @property
    def delta(self) -> float:
        """Return candidate minus baseline."""

        return self.candidate_value - self.baseline_value


class BaselineComparison(ContractModel):
    """Saved comparison between two completed runs."""

    comparison_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    baseline_run_id: str = Field(min_length=1)
    candidate_run_id: str = Field(min_length=1)
    metrics: tuple[ComparisonMetric, ...] = ()
    regressions: tuple[str, ...] = ()
    targeted_failure_ids: tuple[str, ...] = ()
    target_cluster_id: str | None = Field(default=None, min_length=1)
    target_improved: bool = False
    pass_to_fail_transitions: tuple[str, ...] = ()
    numerical_regressions: tuple[str, ...] = ()
    hard_regressions: tuple[str, ...] = ()
    holdout_checked: bool = False
    holdout_baseline_run_id: str | None = None
    holdout_candidate_run_id: str | None = None
    verdict: ComparisonVerdict
    created_at: datetime
    notes: str = ""
    enterprise_metrics: tuple[EnterpriseComparisonMetric, ...] = ()
    enterprise_regressions: tuple[str, ...] = ()
    business_regressions: tuple[str, ...] = ()
    security_regressions: tuple[str, ...] = ()
    authorization_regressions: tuple[str, ...] = ()
    approval_regressions: tuple[str, ...] = ()
    state_integrity_regressions: tuple[str, ...] = ()
    workflow_completion_regressions: tuple[str, ...] = ()
    cost_regressions: tuple[str, ...] = ()
    latency_regressions: tuple[str, ...] = ()
    token_usage_regressions: tuple[str, ...] = ()
    tool_side_effect_regressions: tuple[str, ...] = ()
    delegation_regressions: tuple[str, ...] = ()
    reliability_regressions: tuple[str, ...] = ()
    policy_regressions: tuple[str, ...] = ()
    tenant_boundary_regressions: tuple[str, ...] = ()
    risk_weighted_regression_score: float = Field(default=0.0, ge=0.0)
    evaluator_family_aggregates: tuple[EvaluatorFamilyAggregate, ...] = ()
    environment_compatible: bool = True

    @field_validator("risk_weighted_regression_score")
    @classmethod
    def validate_risk_score(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("risk-weighted regression score must be finite")
        return value

    @model_validator(mode="after")
    def validate_comparison(self) -> "BaselineComparison":
        if self.baseline_run_id == self.candidate_run_id:
            raise ValueError("Baseline and candidate runs must differ")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        any_regression = any(
            (
                self.regressions,
                self.enterprise_regressions,
                self.hard_regressions,
                self.pass_to_fail_transitions,
                self.numerical_regressions,
                self.business_regressions,
                self.security_regressions,
                self.authorization_regressions,
                self.approval_regressions,
                self.state_integrity_regressions,
                self.workflow_completion_regressions,
                self.cost_regressions,
                self.latency_regressions,
                self.token_usage_regressions,
                self.tool_side_effect_regressions,
                self.delegation_regressions,
                self.reliability_regressions,
                self.policy_regressions,
                self.tenant_boundary_regressions,
            )
        )
        if self.verdict == ComparisonVerdict.IMPROVED and any_regression:
            raise ValueError("An improved comparison cannot contain regressions")
        if self.holdout_checked and (
            not self.holdout_baseline_run_id or not self.holdout_candidate_run_id
        ):
            raise ValueError("Holdout run IDs are required when holdout_checked is true")
        if not self.holdout_checked and (
            self.holdout_baseline_run_id or self.holdout_candidate_run_id
        ):
            raise ValueError("Holdout run IDs require holdout_checked")
        enterprise_ids = [metric.metric_id for metric in self.enterprise_metrics]
        if len(enterprise_ids) != len(set(enterprise_ids)):
            raise ValueError("Enterprise comparison metric IDs must be unique")
        if not self.environment_compatible and self.verdict == ComparisonVerdict.IMPROVED:
            raise ValueError("An incompatible environment cannot be improved")
        return self

    @property
    def risk_weighted_regressions(self) -> float:
        """Return the risk-weighted regression score using the plural name."""

        return self.risk_weighted_regression_score

    @property
    def risk_weighted_regression(self) -> float:
        """Return the risk-weighted regression score."""

        return self.risk_weighted_regression_score


class ComparisonPolicy(ContractModel):
    """Deterministic rules for a baseline-versus-candidate comparison."""

    policy_id: str = Field(min_length=1)
    metric_tolerance: float = Field(default=0.0, ge=0.0, le=1.0)
    numerical_evaluator_ids: tuple[str, ...] = ("session.cross_turn_numerical_consistency",)
    hard_evaluator_ids: tuple[str, ...] = (
        "session.cross_turn_numerical_consistency",
        "safety.instruction_override_resistance",
        "safety.protected_argument_integrity",
        "safety.authorization_boundary_preserved",
    )
    development_splits: tuple[DatasetSplit, ...] = (
        DatasetSplit.SMOKE,
        DatasetSplit.DEVELOPMENT,
        DatasetSplit.REGRESSION,
        DatasetSplit.SECURITY,
    )
    holdout_split: DatasetSplit = DatasetSplit.HOLDOUT
    require_target_improvement: bool = True
    require_holdout: bool = False
    enterprise_metric_tolerance: float = Field(default=0.0, ge=0.0, le=1.0)
    enterprise_hard_dimensions: tuple[EnterpriseComparisonDimension, ...] = (
        EnterpriseComparisonDimension.SECURITY,
        EnterpriseComparisonDimension.STATE_INTEGRITY,
        EnterpriseComparisonDimension.AUTHORIZATION,
        EnterpriseComparisonDimension.TENANT_BOUNDARY,
        EnterpriseComparisonDimension.APPROVALS,
        EnterpriseComparisonDimension.TOOL_SIDE_EFFECTS,
        EnterpriseComparisonDimension.POLICY,
    )
    enterprise_dimension_weights: tuple[ComparisonDimensionWeight, ...] = ()

    @model_validator(mode="after")
    def validate_policy(self) -> "ComparisonPolicy":
        if len(self.numerical_evaluator_ids) != len(set(self.numerical_evaluator_ids)):
            raise ValueError("numerical_evaluator_ids must be unique")
        if len(self.hard_evaluator_ids) != len(set(self.hard_evaluator_ids)):
            raise ValueError("hard_evaluator_ids must be unique")
        if self.holdout_split in self.development_splits:
            raise ValueError("holdout_split cannot be a development split")
        if len(self.enterprise_hard_dimensions) != len(set(self.enterprise_hard_dimensions)):
            raise ValueError("enterprise_hard_dimensions must be unique")
        weight_dimensions = [item.dimension for item in self.enterprise_dimension_weights]
        if len(weight_dimensions) != len(set(weight_dimensions)):
            raise ValueError("enterprise dimension weights must be unique")
        return self


class EnterpriseComparisonPolicy(ContractModel):
    """Standalone policy for direct enterprise metric comparisons."""

    policy_id: str = Field(min_length=1)
    metric_tolerance: float = Field(default=0.0, ge=0.0, le=1.0)
    hard_dimensions: tuple[EnterpriseComparisonDimension, ...] = (
        EnterpriseComparisonDimension.SECURITY,
        EnterpriseComparisonDimension.STATE_INTEGRITY,
        EnterpriseComparisonDimension.AUTHORIZATION,
        EnterpriseComparisonDimension.TENANT_BOUNDARY,
        EnterpriseComparisonDimension.APPROVALS,
        EnterpriseComparisonDimension.TOOL_SIDE_EFFECTS,
        EnterpriseComparisonDimension.POLICY,
    )
    dimension_weights: tuple[ComparisonDimensionWeight, ...] = ()
    require_environment_compatibility: bool = True

    @model_validator(mode="after")
    def validate_enterprise_policy(self) -> "EnterpriseComparisonPolicy":
        if len(self.hard_dimensions) != len(set(self.hard_dimensions)):
            raise ValueError("hard_dimensions must be unique")
        dimensions = [item.dimension for item in self.dimension_weights]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("dimension_weights must be unique")
        return self


class PromotionPolicy(ContractModel):
    """Rules used to decide if a candidate can be promoted."""

    policy_id: str = Field(min_length=1)
    version: VersionString
    hard_gates: tuple[str, ...] = (
        "no_security_regression",
        "no_protected_argument_regression",
        "no_numerical_consistency_regression",
        "target_improvement",
        "holdout_non_declining",
    )
    soft_gates: tuple[str, ...] = ("overall_improvement",)
    metric_tolerance: float = Field(default=0.0, ge=0.0, le=1.0)
    holdout_tolerance: float = Field(default=0.0, ge=0.0, le=1.0)
    require_target_improvement: bool = True
    require_holdout_check: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "PromotionPolicy":
        for name, gates in (("hard_gates", self.hard_gates), ("soft_gates", self.soft_gates)):
            if len(gates) != len(set(gates)):
                raise ValueError(f"{name} must contain unique gate IDs")
        overlap = set(self.hard_gates) & set(self.soft_gates)
        if overlap:
            raise ValueError("A promotion gate cannot be both hard and soft")
        return self


class PromotionGateResult(ContractModel):
    """Evidence for one evaluated promotion gate."""

    gate_id: str = Field(min_length=1)
    kind: PromotionGateKind
    passed: bool
    reason: str = Field(min_length=1)
    observed: float | int | str | bool | None = None
    required: float | int | str | bool | None = None


class PromotionEvaluation(ContractModel):
    """Gate results that a human uses to decide on promotion."""

    candidate_id: str = Field(min_length=1)
    comparison_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    hard_gates: tuple[PromotionGateResult, ...] = ()
    soft_gates: tuple[PromotionGateResult, ...] = ()
    eligible: bool
    created_at: datetime

    @model_validator(mode="after")
    def validate_evaluation(self) -> "PromotionEvaluation":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        all_ids = [gate.gate_id for gate in (*self.hard_gates, *self.soft_gates)]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Promotion gate IDs must be unique")
        expected = all(gate.passed for gate in self.hard_gates)
        if self.eligible != expected:
            raise ValueError("eligible must match the hard gate results")
        return self


class PromotionDecision(ContractModel):
    """Immutable human decision for a candidate."""

    decision_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    comparison_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    outcome: PromotionOutcome
    reviewer: str = Field(min_length=1)
    decided_at: datetime
    reason: str = Field(min_length=1)
    previous_active_candidate_id: str | None = None
    rollback_of_decision_id: str | None = None
    restored_candidate_id: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "PromotionDecision":
        object.__setattr__(self, "decided_at", require_aware_utc(self.decided_at))
        if self.outcome == PromotionOutcome.ROLLBACK and not self.rollback_of_decision_id:
            raise ValueError("rollback_of_decision_id is required for a rollback")
        if self.outcome == PromotionOutcome.ROLLBACK and not self.restored_candidate_id:
            raise ValueError("restored_candidate_id is required for a rollback")
        if self.outcome != PromotionOutcome.ROLLBACK and self.rollback_of_decision_id is not None:
            raise ValueError("rollback_of_decision_id is only allowed for a rollback")
        if self.outcome != PromotionOutcome.ROLLBACK and self.restored_candidate_id is not None:
            raise ValueError("restored_candidate_id is only allowed for a rollback")
        return self


class ActiveCandidatePointer(ContractModel):
    """The current candidate selected by the latest human decision."""

    pointer_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    updated_at: datetime

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "ActiveCandidatePointer":
        object.__setattr__(self, "updated_at", require_aware_utc(self.updated_at))
        return self
