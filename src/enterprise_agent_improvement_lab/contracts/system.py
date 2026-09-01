"""Typed multi-agent candidate, case, and report contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, ConfigDict, Field, model_validator

from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateStatus,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.cases import EnterpriseEvaluationCase
from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    VersionString,
    require_aware_utc,
    utc_now,
)
from enterprise_agent_improvement_lab.contracts.evaluation import EnterpriseCaseEvaluationResult
from enterprise_agent_improvement_lab.contracts.failures import EvaluationFailure, Severity
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTrace


class SystemInteractionConstraint(ContractModel):
    """Explicit boundary for communication between system agents."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    constraint_id: str = Field(min_length=1)
    source_agent_id: str | None = Field(default=None, min_length=1)
    target_agent_id: str | None = Field(default=None, min_length=1)
    allowed_target_agent_ids: tuple[str, ...] = ()
    allowed_tool_ids: tuple[str, ...] = ()
    allowed_permission_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("allowed_permission_ids", "allowed_permissions"),
    )
    forbidden_context_fields: tuple[str, ...] = ()
    max_delegation_depth: int | None = Field(default=None, ge=0)
    require_result_validation: bool = True
    require_consistent_decisions: bool = False

    @model_validator(mode="after")
    def validate_constraint(self) -> "SystemInteractionConstraint":
        for name, values in (
            ("allowed_target_agent_ids", self.allowed_target_agent_ids),
            ("allowed_tool_ids", self.allowed_tool_ids),
            ("allowed_permission_ids", self.allowed_permission_ids),
            ("forbidden_context_fields", self.forbidden_context_fields),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty values")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique values")
        return self


class SystemCandidateLineage(ContractModel):
    """Immutable lineage for a system candidate version."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    parent_system_candidate_id: str | None = None
    source_failure_ids: tuple[str, ...] = ()
    improvement_scope_id: str | None = Field(default=None, min_length=1)
    generator_id: str | None = Field(default=None, min_length=1)
    reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_lineage(self) -> "SystemCandidateLineage":
        if len(self.source_failure_ids) != len(set(self.source_failure_ids)):
            raise ValueError("system candidate source failure IDs must be unique")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class SystemCandidate(ContractModel):
    """A versioned group of agent candidates evaluated as one system."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    system_candidate_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("system_candidate_id", "candidate_id", "system_id"),
    )
    name: str = Field(default="system", min_length=1)
    version: VersionString
    agent_candidates: tuple[EnterpriseAgentCandidate, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices("agent_candidates", "candidates", "agents"),
    )
    shared_tool_ids: tuple[str, ...] = ()
    shared_policy_ids: tuple[str, ...] = ()
    shared_capability_ids: tuple[str, ...] = ()
    interaction_constraints: tuple[SystemInteractionConstraint, ...] = ()
    parent_system_candidate_id: str | None = None
    lineage: SystemCandidateLineage = Field(default_factory=SystemCandidateLineage)
    status: CandidateStatus = CandidateStatus.DRAFT
    rationale: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_candidate(self) -> "SystemCandidate":
        if self.parent_system_candidate_id == self.system_candidate_id:
            raise ValueError("parent_system_candidate_id must differ from system candidate ID")
        if self.lineage.parent_system_candidate_id != self.parent_system_candidate_id:
            raise ValueError("System candidate lineage parent must match candidate parent")
        candidate_ids = [candidate.candidate_id for candidate in self.agent_candidates]
        agent_ids = [candidate.agent_id for candidate in self.agent_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("System agent candidate IDs must be unique")
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("System agent IDs must be unique")
        for name, values in (
            ("shared_tool_ids", self.shared_tool_ids),
            ("shared_policy_ids", self.shared_policy_ids),
            ("shared_capability_ids", self.shared_capability_ids),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty IDs")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique IDs")
        constraint_ids = [constraint.constraint_id for constraint in self.interaction_constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("System interaction constraint IDs must be unique")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self

    @property
    def agent_ids(self) -> tuple[str, ...]:
        """Return agent IDs in deterministic declaration order."""

        return tuple(candidate.agent_id for candidate in self.agent_candidates)

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        """Return member candidate IDs in deterministic declaration order."""

        return tuple(candidate.candidate_id for candidate in self.agent_candidates)

    @property
    def lifecycle_status(self) -> CandidateStatus:
        """Return the system lifecycle status using the common name."""

        return self.status


class SystemEvaluationCase(EnterpriseEvaluationCase):
    """Enterprise evaluation case with explicit multi-agent constraints."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    agent_ids: tuple[str, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices("agent_ids", "agents", "participating_agents"),
    )
    agent_candidate_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices(
            "agent_candidate_ids", "candidate_ids", "agent_candidate_refs"
        ),
    )
    shared_policy_ids: tuple[str, ...] = ()
    shared_tool_ids: tuple[str, ...] = ()
    interaction_constraints: tuple[SystemInteractionConstraint, ...] = ()

    @model_validator(mode="after")
    def validate_system_case(self) -> "SystemEvaluationCase":
        for name, values in (
            ("agent_ids", self.agent_ids),
            ("agent_candidate_ids", self.agent_candidate_ids),
            ("shared_policy_ids", self.shared_policy_ids),
            ("shared_tool_ids", self.shared_tool_ids),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty IDs")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique IDs")
        if self.agent_candidate_ids and len(self.agent_candidate_ids) != len(self.agent_ids):
            raise ValueError("agent_candidate_ids must align with agent_ids")
        return self

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        """Return candidate IDs aligned to the declared agents."""

        return self.agent_candidate_ids


class SystemCheckResult(ContractModel):
    """One system-level safety or coordination check."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    check_id: str = Field(min_length=1)
    check_type: str = Field(min_length=1)
    passed: bool
    explanation: str = Field(min_length=1)
    severity: Severity = Severity.HIGH
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_check(self) -> "SystemCheckResult":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("System check evidence references must be unique")
        return self


class DelegationEdge(ContractModel):
    """Safe graph edge extracted from a delegation event."""

    source_agent_id: str = Field(min_length=1)
    target_agent_id: str = Field(min_length=1)
    delegation_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    child_execution_id: str | None = None


class SystemEvaluationReport(ContractModel):
    """Individual, inter-agent, and whole-system evaluation evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    report_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    system_candidate_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    agent_traces: tuple[ExecutionTrace, ...] = Field(min_length=1)
    individual_results: tuple[EnterpriseCaseEvaluationResult, ...] = ()
    system_checks: tuple[SystemCheckResult, ...] = ()
    delegation_edges: tuple[DelegationEdge, ...] = ()
    failures: tuple[EvaluationFailure, ...] = ()
    overall_passed: bool
    business_outcome_passed: bool | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_report(self) -> "SystemEvaluationReport":
        trace_ids = [trace.trace_id for trace in self.agent_traces]
        if len(trace_ids) != len(set(trace_ids)):
            raise ValueError("System report trace IDs must be unique")
        result_keys = [(result.case_id, result.repeat_index) for result in self.individual_results]
        if len(result_keys) != len(set(result_keys)):
            raise ValueError("System individual result identities must be unique")
        if any(result.case_id != self.case_id for result in self.individual_results):
            raise ValueError("System individual results must target the system case")
        known_trace_ids = set(trace_ids)
        result_trace_ids = {
            result.trace_id for result in self.individual_results if result.trace_id is not None
        }
        if not result_trace_ids.issubset(known_trace_ids):
            raise ValueError("System individual results reference unknown trace IDs")
        check_ids = [check.check_id for check in self.system_checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("System report check IDs must be unique")
        failure_ids = [failure.failure_id for failure in self.failures]
        if len(failure_ids) != len(set(failure_ids)):
            raise ValueError("System report failure IDs must be unique")
        if self.overall_passed and any(not check.passed for check in self.system_checks):
            raise ValueError("A passing system report cannot contain a failed system check")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


InteractionConstraint = SystemInteractionConstraint


__all__ = [
    "DelegationEdge",
    "InteractionConstraint",
    "SystemCandidate",
    "SystemCandidateLineage",
    "SystemCheckResult",
    "SystemEvaluationCase",
    "SystemEvaluationReport",
    "SystemInteractionConstraint",
]
