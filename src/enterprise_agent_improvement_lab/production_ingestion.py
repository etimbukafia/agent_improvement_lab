"""Import-only boundary for safe production execution evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from enterprise_agent_improvement_lab.contracts.failures import SamplingEvent, SamplingReason
from enterprise_agent_improvement_lab.contracts.governance import (
    RedactionPolicy,
    RetentionPolicy,
    TenantBoundary,
)
from enterprise_agent_improvement_lab.contracts.ingestion import (
    ProductionIngestionResult,
    ProductionSignalKind,
    ProductionTraceEvidence,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ApprovalDecision,
    ApprovalDecisionEvent,
    ExecutionEventStatus,
    ExecutionTrace,
    ToolCallEvent,
)

if TYPE_CHECKING:
    from enterprise_agent_improvement_lab.storage.ports import LabStore

_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "credential",
    "password",
    "api_key",
    "apikey",
    "authorization",
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|password|secret|token|credential|authorization)\s*[:=]\s*[^\s,;]+"
)
_SIGNAL_REASONS = {
    ProductionSignalKind.MANUAL_OVERRIDE: SamplingReason.MANUAL_OVERRIDE,
    ProductionSignalKind.APPROVAL_REJECTION: SamplingReason.APPROVAL_REJECTION,
    ProductionSignalKind.PERMISSION_DENIAL: SamplingReason.PERMISSION_DENIAL,
    ProductionSignalKind.UNEXPECTED_STATE_MUTATION: SamplingReason.UNEXPECTED_STATE_MUTATION,
    ProductionSignalKind.ROLLBACK: SamplingReason.ROLLBACK,
    ProductionSignalKind.SLA_BREACH: SamplingReason.SLA_BREACH,
    ProductionSignalKind.POLICY_VIOLATION: SamplingReason.POLICY_VIOLATION,
    ProductionSignalKind.OPERATOR_ESCALATION: SamplingReason.OPERATOR_ESCALATION,
    ProductionSignalKind.REPEATED_EXECUTION: SamplingReason.REPEATED_EXECUTION,
    ProductionSignalKind.COMPENSATION_EVENT: SamplingReason.COMPENSATION_EVENT,
    ProductionSignalKind.BUSINESS_KPI_DEGRADATION: SamplingReason.BUSINESS_KPI_DEGRADATION,
    ProductionSignalKind.DATA_INTEGRITY_FAILURE: SamplingReason.DATA_INTEGRITY_FAILURE,
}


def ingest_production_trace(
    store: LabStore,
    evidence: ProductionTraceEvidence,
    *,
    redaction_policy: RedactionPolicy | None = None,
    retention_policy: RetentionPolicy | None = None,
    tenant_boundary: TenantBoundary | None = None,
) -> ProductionIngestionResult:
    """Persist safe evidence and deterministic sampling results.

    The function only writes Lab evidence repositories. It does not call a
    runtime, modify a candidate, or make a promotion decision.
    """

    safe_trace = _safe_trace(evidence.trace)
    context = {
        **{
            f"production_operational_{key}": value
            for key, value in evidence.operational_metadata.items()
        },
        **{
            f"production_lifecycle_{key}": value
            for key, value in evidence.promotion_or_rollback_context.items()
        },
    }
    safe_trace = safe_trace.model_copy(
        update={
            "metadata": {**safe_trace.metadata, **_safe_mapping(context)},
            "evidence_refs": tuple(
                dict.fromkeys(
                    (
                        *safe_trace.evidence_refs,
                        *(
                            reference
                            for signal in (
                                *evidence.human_review_signals,
                                *evidence.incident_signals,
                            )
                            for reference in signal.evidence_refs
                        ),
                    )
                )
            ),
        }
    )
    if tenant_boundary is not None:
        tenant_boundary.validate_value(safe_trace)
    if retention_policy is not None:
        retention_policy.assert_persistable(safe_trace.started_at)
    if redaction_policy is not None:
        safe_trace = redaction_policy.redact_model(safe_trace)  # type: ignore[assignment]
    score_ids = tuple(score.score_id for score in evidence.evaluator_results)
    safe_summary = (
        evidence.summary.model_copy(update={"evaluation_score_ids": score_ids})
        if evidence.summary is not None
        else safe_trace.to_summary(score_ids)
    )
    existing = store.execution_traces.get(safe_trace.execution_id)
    if existing is not None and existing != safe_trace:
        raise ValueError("Production execution ID already has different evidence")
    duplicate = existing is not None
    store.execution_traces.save(safe_trace)
    store.execution_trace_summaries.save(safe_summary)
    for score in evidence.evaluator_results:
        safe_score = score
        if tenant_boundary is not None:
            tenant_boundary.validate_value(safe_score)
        if retention_policy is not None:
            retention_policy.assert_persistable(safe_score.created_at)
        if redaction_policy is not None:
            safe_score = redaction_policy.redact_model(safe_score)  # type: ignore[assignment]
        store.scores.save(safe_score)

    reasons = _sampling_reasons(evidence, safe_trace)
    session_id = safe_trace.session_id or f"production:{safe_trace.execution_id}"
    sampling_event_ids: list[str] = []
    for reason in reasons:
        event = SamplingEvent(
            event_id=f"sampling:{safe_trace.execution_id}:{reason.value}",
            session_id=session_id,
            reason=reason,
            trace_ids=(safe_trace.execution_id,),
            created_at=evidence.received_at,
            metadata={"source_id": evidence.source_id, "source": "production_ingestion"},
        )
        store.sampling_events.save(event)
        sampling_event_ids.append(event.event_id)
    return ProductionIngestionResult(
        source_id=evidence.source_id,
        execution_id=safe_trace.execution_id,
        candidate_id=safe_trace.candidate_id,
        duplicate=duplicate,
        sampling_event_ids=tuple(sampling_event_ids),
    )


def _sampling_reasons(
    evidence: ProductionTraceEvidence, trace: ExecutionTrace
) -> tuple[SamplingReason, ...]:
    reasons = {
        _SIGNAL_REASONS[signal.kind]
        for signal in (*evidence.human_review_signals, *evidence.incident_signals)
    }
    if any(
        isinstance(event, ToolCallEvent)
        and (event.authorization_granted is False or event.status == ExecutionEventStatus.DENIED)
        for event in trace.events
    ):
        reasons.add(SamplingReason.PERMISSION_DENIAL)
    if any(
        isinstance(event, ApprovalDecisionEvent) and event.decision == ApprovalDecision.REJECTED
        for event in trace.events
    ):
        reasons.add(SamplingReason.APPROVAL_REJECTION)
    return tuple(sorted(reasons, key=lambda reason: reason.value))


def _safe_trace(trace: ExecutionTrace) -> ExecutionTrace:
    """Remove credentials and raw tool arguments before storage."""

    payload = trace.model_dump(mode="python")
    payload["metadata"] = _safe_mapping(payload.get("metadata"))
    for event in payload["events"]:
        event["metadata"] = _safe_mapping(event.get("metadata"))
        if event.get("event_type") == "tool_call":
            event["arguments"] = {}
        for key in (
            "input_summary",
            "output_summary",
            "result_summary",
            "message_summary",
            "payload_summary",
            "action_summary",
            "reason_summary",
        ):
            if isinstance(event.get(key), str):
                event[key] = _safe_text(event[key])
    return ExecutionTrace.model_validate(payload)


def _safe_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        name = str(key)
        if any(part in name.casefold() for part in _SENSITIVE_KEY_PARTS):
            continue
        if isinstance(item, (str, int, float, bool)):
            result[name] = _safe_text(str(item))
    return result


def _safe_text(value: str) -> str:
    return _SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", value)
