"""Candidate lifecycle and controlled shadow or canary contracts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import AliasChoices, ConfigDict, Field, model_validator

from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateStatus,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    require_aware_utc,
    utc_now,
)

if TYPE_CHECKING:
    from enterprise_agent_improvement_lab.storage.ports import LifecycleStore


class CandidateStage(StrEnum):
    """Supported enterprise candidate stages."""

    DRAFT = "draft"
    OFFLINE_EVALUATED = "offline_evaluated"
    SHADOW = "shadow"
    CANARY = "canary"
    APPROVED = "approved"
    ACTIVE = "active"
    RETIRED = "retired"

    # This value lets older records be read while they move to the new name.
    EVALUATED = "evaluated"


class StageGateKind(StrEnum):
    """Class of a stage gate."""

    REQUIRED = "required"
    ADVISORY = "advisory"


class StageGate(ContractModel):
    """One stage-specific gate computed from saved evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    gate_id: str = Field(min_length=1)
    stage: CandidateStage
    kind: StageGateKind = StageGateKind.REQUIRED
    passed: bool
    summary: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_gate(self) -> "StageGate":
        _unique_non_empty("evidence_refs", self.evidence_refs)
        return self


class StageEvidence(ContractModel):
    """Evidence that supports one candidate lifecycle stage."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    evidence_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    stage: CandidateStage
    environment_snapshot_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "environment_snapshot_id",
            "environment_snapshot_ref",
            "snapshot_id",
        ),
    )
    run_ids: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()
    comparison_id: str | None = Field(default=None, min_length=1)
    evidence_refs: tuple[str, ...] = ()
    safe_summary: str = Field(min_length=1)
    passed: bool
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence(self) -> "StageEvidence":
        for name, values in (
            ("run_ids", self.run_ids),
            ("trace_ids", self.trace_ids),
            ("evidence_refs", self.evidence_refs),
        ):
            _unique_non_empty(name, values)
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class ShadowEvaluation(ContractModel):
    """A non-mutating evaluation record for the shadow stage."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    evaluation_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    stage: Literal[CandidateStage.SHADOW] = CandidateStage.SHADOW
    run_id: str = Field(min_length=1)
    environment_snapshot_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "environment_snapshot_id",
            "environment_snapshot_ref",
            "snapshot_id",
        ),
    )
    case_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    side_effects_observed: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "side_effects_observed",
            "production_side_effects",
            "production_side_effects_observed",
        ),
    )
    passed: bool
    summary: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_shadow(self) -> "ShadowEvaluation":
        _unique_non_empty("case_ids", self.case_ids)
        _unique_non_empty("evidence_refs", self.evidence_refs)
        if self.side_effects_observed:
            raise ValueError("Shadow evaluation cannot record production side effects")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class CanaryEvaluation(ContractModel):
    """A bounded canary evidence record without traffic-routing behavior."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    evaluation_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    stage: Literal[CandidateStage.CANARY] = CandidateStage.CANARY
    run_id: str = Field(min_length=1)
    environment_snapshot_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "environment_snapshot_id",
            "environment_snapshot_ref",
            "snapshot_id",
        ),
    )
    scope_id: str = Field(min_length=1)
    max_executions: int = Field(gt=0)
    executed_count: int = Field(default=0, ge=0)
    case_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    passed: bool
    summary: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_canary(self) -> "CanaryEvaluation":
        _unique_non_empty("case_ids", self.case_ids)
        _unique_non_empty("evidence_refs", self.evidence_refs)
        if self.executed_count > self.max_executions:
            raise ValueError("Canary executed_count cannot exceed max_executions")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class PromotionReadiness(ContractModel):
    """Computed readiness that remains separate from human approval."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    readiness_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    target_stage: CandidateStage
    gates: tuple[StageGate, ...] = ()
    missing_gate_ids: tuple[str, ...] = ()
    eligible: bool
    human_approval_required: bool = True
    evidence_refs: tuple[str, ...] = ()
    computed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_readiness(self) -> "PromotionReadiness":
        gate_ids = tuple(gate.gate_id for gate in self.gates)
        _unique_non_empty("gate IDs", gate_ids)
        _unique_non_empty("missing_gate_ids", self.missing_gate_ids)
        _unique_non_empty("evidence_refs", self.evidence_refs)
        if any(gate.stage != self.target_stage for gate in self.gates):
            raise ValueError("Stage gate stage must match promotion target stage")
        failed = {
            gate.gate_id
            for gate in self.gates
            if gate.kind == StageGateKind.REQUIRED and not gate.passed
        }
        if self.eligible and (failed or self.missing_gate_ids):
            raise ValueError("Eligible readiness cannot have failed or missing required gates")
        if self.target_stage in {CandidateStage.ACTIVE, CandidateStage.APPROVED}:
            if not self.human_approval_required:
                raise ValueError("Approval readiness always requires separate human approval")
        object.__setattr__(self, "computed_at", require_aware_utc(self.computed_at))
        return self


class RollbackEvidence(ContractModel):
    """Explicit evidence for a rollback or retirement action."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    rollback_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    from_stage: CandidateStage
    to_stage: CandidateStage = CandidateStage.RETIRED
    restored_candidate_id: str | None = Field(default=None, min_length=1)
    reason: str = Field(min_length=1)
    environment_snapshot_id: str | None = Field(default=None, min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_rollback(self) -> "RollbackEvidence":
        _unique_non_empty("evidence_refs", self.evidence_refs)
        if self.from_stage == self.to_stage:
            raise ValueError("Rollback stages must differ")
        if self.to_stage not in {CandidateStage.RETIRED, CandidateStage.APPROVED}:
            raise ValueError("Rollback must retire or restore an approved stage")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class CandidateStageTransition(ContractModel):
    """One validated append-only transition in a candidate lifecycle."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    transition_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    from_stage: CandidateStage = Field(
        validation_alias=AliasChoices("from_stage", "current_stage", "from_status")
    )
    to_stage: CandidateStage = Field(
        validation_alias=AliasChoices("to_stage", "target_stage", "to_status")
    )
    stage_evidence_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)
    computed_eligible: bool = True
    human_approval_required: bool = False
    human_approved: bool = False
    reviewer: str | None = Field(default=None, min_length=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_transition(self) -> "CandidateStageTransition":
        validate_lifecycle_transition(self.from_stage, self.to_stage)
        _unique_non_empty("stage_evidence_ids", self.stage_evidence_ids)
        _unique_non_empty("evidence_refs", self.evidence_refs)
        if not self.stage_evidence_ids and not self.evidence_refs:
            raise ValueError("A stage transition needs explicit evidence")
        if self.human_approval_required and not self.human_approved:
            raise ValueError("Human approval is required for this stage transition")
        if self.human_approved and not self.reviewer:
            raise ValueError("A human-approved transition needs a reviewer")
        if not self.computed_eligible:
            raise ValueError("An ineligible candidate cannot transition stages")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


_ALLOWED_TRANSITIONS: dict[CandidateStage, frozenset[CandidateStage]] = {
    CandidateStage.DRAFT: frozenset({CandidateStage.OFFLINE_EVALUATED}),
    CandidateStage.EVALUATED: frozenset({CandidateStage.OFFLINE_EVALUATED}),
    CandidateStage.OFFLINE_EVALUATED: frozenset({CandidateStage.SHADOW}),
    CandidateStage.SHADOW: frozenset({CandidateStage.CANARY}),
    CandidateStage.CANARY: frozenset({CandidateStage.APPROVED}),
    CandidateStage.APPROVED: frozenset({CandidateStage.ACTIVE, CandidateStage.RETIRED}),
    CandidateStage.ACTIVE: frozenset({CandidateStage.RETIRED}),
    CandidateStage.RETIRED: frozenset(),
}


class LifecycleTransitionError(ValueError):
    """Raised when a candidate skips or repeats a lifecycle stage."""


def normalize_candidate_stage(value: CandidateStage | CandidateStatus | str) -> CandidateStage:
    """Normalize status values, including the old ``evaluated`` value."""

    raw = value.value if isinstance(value, (CandidateStage, CandidateStatus)) else value
    if raw == CandidateStatus.EVALUATED.value:
        return CandidateStage.EVALUATED
    try:
        return CandidateStage(raw)
    except ValueError as exc:
        raise LifecycleTransitionError(f"Unknown candidate lifecycle stage: {raw}") from exc


def validate_lifecycle_transition(
    current: CandidateStage | CandidateStatus | str,
    target: CandidateStage | CandidateStatus | str,
) -> None:
    """Raise when a stage transition is not allowed."""

    current_stage = normalize_candidate_stage(current)
    target_stage = normalize_candidate_stage(target)
    if target_stage not in _ALLOWED_TRANSITIONS.get(current_stage, frozenset()):
        raise LifecycleTransitionError(
            f"Cannot move candidate from {current_stage.value} to {target_stage.value}"
        )


def transition_candidate_status(
    candidate: EnterpriseAgentCandidate,
    target: CandidateStage | CandidateStatus | str,
) -> EnterpriseAgentCandidate:
    """Return a candidate copy with one validated next lifecycle status."""

    current_stage = normalize_candidate_stage(candidate.status)
    target_stage = normalize_candidate_stage(target)
    validate_lifecycle_transition(current_stage, target_stage)
    if target_stage == CandidateStage.EVALUATED:
        target_status = CandidateStatus.EVALUATED
    else:
        target_status = CandidateStatus(target_stage.value)
    return candidate.model_copy(update={"status": target_status})


class CandidateLifecycleService:
    """Record controlled lifecycle evidence without deploying a candidate."""

    def __init__(self, transition_store: "LifecycleStore | object | None" = None) -> None:
        """Create a lifecycle service over a transition repository or store."""

        self.transition_store = transition_store

    def _repository(self, name: str) -> Any | None:
        if self.transition_store is None:
            return None
        repository = getattr(self.transition_store, name, None)
        return repository if repository is not None else self.transition_store

    def record_stage_evidence(self, evidence: StageEvidence) -> StageEvidence:
        """Persist stage evidence when a lifecycle store was supplied."""

        repository = self._repository("stage_evidence")
        if repository is not None:
            repository.save(evidence)
        return evidence

    def record_shadow_evaluation(self, evaluation: ShadowEvaluation) -> ShadowEvaluation:
        """Persist a shadow evaluation when a lifecycle store was supplied."""

        if evaluation.side_effects_observed:
            raise ValueError("Shadow evaluation cannot create production side effects")
        repository = self._repository("shadow_evaluations")
        if repository is not None:
            repository.save(evaluation)
        return evaluation

    def record_canary_evaluation(self, evaluation: CanaryEvaluation) -> CanaryEvaluation:
        """Persist a bounded canary evaluation."""

        repository = self._repository("canary_evaluations")
        if repository is not None:
            repository.save(evaluation)
        return evaluation

    def record_readiness(self, readiness: PromotionReadiness) -> PromotionReadiness:
        """Persist computed readiness without making a human decision."""

        repository = self._repository("promotion_readiness")
        if repository is not None:
            repository.save(readiness)
        return readiness

    def transition(
        self,
        candidate: EnterpriseAgentCandidate,
        *,
        transition_id: str,
        to_stage: CandidateStage | CandidateStatus | str,
        evidence_refs: Iterable[str],
        rationale: str,
        stage_evidence_ids: Iterable[str] = (),
        computed_eligible: bool = True,
        human_approval_required: bool | None = None,
        human_approved: bool = False,
        reviewer: str | None = None,
        created_at: datetime | None = None,
    ) -> CandidateStageTransition:
        """Validate and optionally persist one stage transition."""

        current = normalize_candidate_stage(candidate.status)
        target = normalize_candidate_stage(to_stage)
        validate_lifecycle_transition(current, target)
        requires_approval = (
            human_approval_required
            if human_approval_required is not None
            else target in {CandidateStage.APPROVED, CandidateStage.ACTIVE}
        )
        result = CandidateStageTransition(
            transition_id=transition_id,
            candidate_id=candidate.candidate_id,
            from_stage=current,
            to_stage=target,
            stage_evidence_ids=tuple(stage_evidence_ids),
            evidence_refs=tuple(evidence_refs),
            rationale=rationale,
            computed_eligible=computed_eligible,
            human_approval_required=requires_approval,
            human_approved=human_approved,
            reviewer=reviewer,
            created_at=created_at or utc_now(),
        )
        repository = self._repository("stage_transitions")
        if repository is not None:
            repository.save(result)
        return result

    def advanced_candidate(
        self,
        candidate: EnterpriseAgentCandidate,
        target: CandidateStage | CandidateStatus | str,
    ) -> EnterpriseAgentCandidate:
        """Return a validated candidate copy without persisting a deployment."""

        return transition_candidate_status(candidate, target)

    def advance_candidate(
        self,
        candidate: EnterpriseAgentCandidate,
        target: CandidateStage | CandidateStatus | str,
    ) -> EnterpriseAgentCandidate:
        """Return a validated candidate copy with the next lifecycle status."""

        return self.advanced_candidate(candidate, target)

    def readiness(
        self,
        *,
        candidate_id: str,
        target_stage: CandidateStage | CandidateStatus | str,
        gates: Iterable[StageGate],
        evidence_refs: Iterable[str] = (),
        readiness_id: str | None = None,
        computed_at: datetime | None = None,
    ) -> PromotionReadiness:
        """Compute stage readiness from explicit gates."""

        target = normalize_candidate_stage(target_stage)
        gate_values = tuple(gates)
        missing = tuple(
            gate.gate_id
            for gate in gate_values
            if gate.kind == StageGateKind.REQUIRED and not gate.passed
        )
        return PromotionReadiness(
            readiness_id=readiness_id or f"readiness:{candidate_id}:{target.value}",
            candidate_id=candidate_id,
            target_stage=target,
            gates=gate_values,
            missing_gate_ids=missing,
            eligible=not missing,
            evidence_refs=tuple(evidence_refs),
            computed_at=computed_at or utc_now(),
        )

    def rollback(
        self,
        candidate: EnterpriseAgentCandidate,
        *,
        rollback_id: str,
        reason: str,
        evidence_refs: Iterable[str],
        restored_candidate_id: str | None = None,
        environment_snapshot_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RollbackEvidence:
        """Record explicit rollback evidence for an active or approved candidate."""

        current = normalize_candidate_stage(candidate.status)
        if current not in {CandidateStage.ACTIVE, CandidateStage.APPROVED}:
            raise LifecycleTransitionError("Only approved or active candidates can be rolled back")
        result = RollbackEvidence(
            rollback_id=rollback_id,
            candidate_id=candidate.candidate_id,
            from_stage=current,
            restored_candidate_id=restored_candidate_id,
            reason=reason,
            environment_snapshot_id=environment_snapshot_id,
            evidence_refs=tuple(evidence_refs),
            created_at=created_at or utc_now(),
        )
        repository = self._repository("rollback_evidence")
        if repository is not None:
            repository.save(result)
        return result


def _unique_non_empty(name: str, values: Iterable[str]) -> None:
    normalized = tuple(values)
    if any(not value or not str(value).strip() for value in normalized):
        raise ValueError(f"{name} must contain non-empty values")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} must contain unique values")


# Useful aliases for callers that use different plan vocabulary.
LifecycleStage = CandidateStage
CandidateLifecycleStage = CandidateStage
StageTransition = CandidateStageTransition


__all__ = [
    "CandidateLifecycleService",
    "CandidateLifecycleStage",
    "CandidateStage",
    "CandidateStageTransition",
    "CanaryEvaluation",
    "LifecycleStage",
    "LifecycleTransitionError",
    "PromotionReadiness",
    "RollbackEvidence",
    "ShadowEvaluation",
    "StageEvidence",
    "StageGate",
    "StageGateKind",
    "StageTransition",
    "normalize_candidate_stage",
    "transition_candidate_status",
    "validate_lifecycle_transition",
]
