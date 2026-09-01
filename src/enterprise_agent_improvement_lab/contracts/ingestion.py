"""Contracts for safe import of production execution evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from enterprise_agent_improvement_lab.contracts.common import ContractModel, require_aware_utc
from enterprise_agent_improvement_lab.contracts.failures import EvaluationScore
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTrace, ExecutionTraceSummary


class ProductionSignalKind(StrEnum):
    """Structured operational signals that can select a trace for review."""

    MANUAL_OVERRIDE = "manual_override"
    APPROVAL_REJECTION = "approval_rejection"
    PERMISSION_DENIAL = "permission_denial"
    UNEXPECTED_STATE_MUTATION = "unexpected_state_mutation"
    ROLLBACK = "rollback"
    SLA_BREACH = "sla_breach"
    POLICY_VIOLATION = "policy_violation"
    OPERATOR_ESCALATION = "operator_escalation"
    REPEATED_EXECUTION = "repeated_execution"
    COMPENSATION_EVENT = "compensation_event"
    BUSINESS_KPI_DEGRADATION = "business_kpi_degradation"
    DATA_INTEGRITY_FAILURE = "data_integrity_failure"


class ProductionSignal(ContractModel):
    """A safe, source-provided production signal with evidence references."""

    kind: ProductionSignalKind
    evidence_refs: tuple[str, ...] = ()
    summary: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> "ProductionSignal":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("Production signal evidence_refs must be unique")
        return self


class ProductionTraceEvidence(ContractModel):
    """One provider-neutral production evidence bundle for Lab ingestion.

    This contract is import-only. It cannot control a production execution or
    make a promotion decision.
    """

    source_id: str = Field(min_length=1)
    trace: ExecutionTrace
    summary: ExecutionTraceSummary | None = None
    evaluator_results: tuple[EvaluationScore, ...] = ()
    operational_metadata: dict[str, str] = Field(default_factory=dict)
    promotion_or_rollback_context: dict[str, str] = Field(default_factory=dict)
    human_review_signals: tuple[ProductionSignal, ...] = ()
    incident_signals: tuple[ProductionSignal, ...] = ()
    received_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> "ProductionTraceEvidence":
        object.__setattr__(self, "received_at", require_aware_utc(self.received_at))
        if self.summary is not None:
            for name in ("execution_id", "agent_id", "agent_version", "candidate_id"):
                if getattr(self.summary, name) != getattr(self.trace, name):
                    raise ValueError(f"summary {name} must match trace {name}")
        score_ids = [result.score_id for result in self.evaluator_results]
        if len(score_ids) != len(set(score_ids)):
            raise ValueError("Evaluator result IDs must be unique")
        return self


class ProductionIngestionResult(ContractModel):
    """The immutable result of one evidence import attempt."""

    source_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    duplicate: bool
    sampling_event_ids: tuple[str, ...] = ()
