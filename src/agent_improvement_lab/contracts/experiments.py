"""Experiment, comparison, and promotion contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from agent_improvement_lab.contracts.cases import DatasetSplit
from agent_improvement_lab.contracts.common import ContractModel, VersionString, require_aware_utc


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


class RunManifest(ContractModel):
    """Inputs that make an experiment run reproducible."""

    run_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: VersionString
    candidate_id: str = Field(min_length=1)
    prompt_artifact_ids: tuple[str, ...] = ()
    toolset: tuple[str, ...] = ()
    runtime_name: str = Field(min_length=1)
    runtime_version: VersionString
    provider: str | None = None
    model: str | None = None
    seed: int | None = None
    configuration_artifact_ids: tuple[str, ...] = ()
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "RunManifest":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


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

    @model_validator(mode="after")
    def validate_comparison(self) -> "BaselineComparison":
        if self.baseline_run_id == self.candidate_run_id:
            raise ValueError("Baseline and candidate runs must differ")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        if self.verdict == ComparisonVerdict.IMPROVED and self.regressions:
            raise ValueError("An improved comparison cannot contain regressions")
        if self.holdout_checked and (
            not self.holdout_baseline_run_id or not self.holdout_candidate_run_id
        ):
            raise ValueError("Holdout run IDs are required when holdout_checked is true")
        if not self.holdout_checked and (
            self.holdout_baseline_run_id or self.holdout_candidate_run_id
        ):
            raise ValueError("Holdout run IDs require holdout_checked")
        return self


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

    @model_validator(mode="after")
    def validate_policy(self) -> "ComparisonPolicy":
        if len(self.numerical_evaluator_ids) != len(set(self.numerical_evaluator_ids)):
            raise ValueError("numerical_evaluator_ids must be unique")
        if len(self.hard_evaluator_ids) != len(set(self.hard_evaluator_ids)):
            raise ValueError("hard_evaluator_ids must be unique")
        if self.holdout_split in self.development_splits:
            raise ValueError("holdout_split cannot be a development split")
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
