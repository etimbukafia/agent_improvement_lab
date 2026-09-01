from datetime import UTC, datetime

import pytest

from enterprise_agent_improvement_lab.contracts.ingestion import (
    ProductionSignal,
    ProductionSignalKind,
    ProductionTraceEvidence,
)
from enterprise_agent_improvement_lab.contracts.sessions import (
    SessionEvaluationResult,
    SessionSummary,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ApprovalDecision,
    ApprovalDecisionEvent,
    ExecutionTrace,
    ToolCallEvent,
    ToolCallOutcome,
)
from enterprise_agent_improvement_lab.production_ingestion import ingest_production_trace
from enterprise_agent_improvement_lab.sampling import sample_completed_session
from enterprise_agent_improvement_lab.storage import SQLiteStore

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _trace(*, event=None):
    return ExecutionTrace(
        execution_id="production-run-1",
        agent_id="orders-agent",
        agent_version="2.0.0",
        candidate_id="candidate-orders-2",
        tenant_id="tenant-1",
        principal_id="operator-1",
        session_id="session-1",
        started_at=NOW,
        ended_at=NOW,
        events=(
            event
            or ToolCallEvent(
                event_id="tool-1",
                sequence=0,
                timestamp=NOW,
                call_id="tool-1",
                name="orders.read",
                outcome=ToolCallOutcome.SUCCESS,
            ),
        ),
    )


def _evidence(trace, *signals):
    return ProductionTraceEvidence(
        source_id="production-source-1",
        trace=trace,
        operational_metadata={"region": "eu-west"},
        human_review_signals=signals,
        received_at=NOW,
    )


def test_production_trace_ingestion_preserves_identity_and_deduplicates(tmp_path):
    evidence = _evidence(_trace())
    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        first = ingest_production_trace(store, evidence)
        second = ingest_production_trace(store, evidence)

        stored = store.execution_traces.get("production-run-1")
        assert first.duplicate is False
        assert second.duplicate is True
        assert store.execution_traces.count() == 1
        assert stored is not None
        assert (stored.execution_id, stored.candidate_id, stored.tenant_id) == (
            "production-run-1",
            "candidate-orders-2",
            "tenant-1",
        )


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (ProductionSignalKind.UNEXPECTED_STATE_MUTATION, "unexpected_state_mutation"),
        (ProductionSignalKind.ROLLBACK, "rollback"),
        (ProductionSignalKind.SLA_BREACH, "sla_breach"),
        (ProductionSignalKind.DATA_INTEGRITY_FAILURE, "data_integrity_failure"),
    ],
)
def test_structured_production_signal_selects_trace_for_review(tmp_path, kind, reason):
    signal = ProductionSignal(kind=kind, evidence_refs=(f"incident:{kind.value}",))
    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        result = ingest_production_trace(store, _evidence(_trace(), signal))
        event = store.sampling_events.get(result.sampling_event_ids[0])

        assert event is not None
        assert event.reason.value == reason
        assert event.trace_ids == ("production-run-1",)
        assert (
            f"incident:{kind.value}" in store.execution_traces.get("production-run-1").evidence_refs
        )


def test_permission_denial_and_approval_rejection_select_trace_for_review(tmp_path):
    denied = ToolCallEvent(
        event_id="tool-1",
        sequence=0,
        timestamp=NOW,
        call_id="tool-1",
        name="orders.write",
        outcome=ToolCallOutcome.ERROR,
        error_type="permission_denied",
        authorization_granted=False,
    )
    rejected = ApprovalDecisionEvent(
        event_id="approval-1",
        sequence=1,
        timestamp=NOW,
        approval_id="approval-1",
        decision=ApprovalDecision.REJECTED,
    )
    trace = _trace(event=denied).model_copy(update={"events": (denied, rejected)})
    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        result = ingest_production_trace(store, _evidence(trace))
        reasons = {
            store.sampling_events.get(event_id).reason.value
            for event_id in result.sampling_event_ids
        }

    assert reasons == {"permission_denial", "approval_rejection"}


def test_ingestion_removes_credentials_and_raw_tool_arguments(tmp_path):
    trace = _trace(
        event=ToolCallEvent(
            event_id="tool-1",
            sequence=0,
            timestamp=NOW,
            call_id="tool-1",
            name="orders.write",
            outcome=ToolCallOutcome.SUCCESS,
            arguments={"api_key": "top-secret"},
            metadata={"password": "top-secret", "region": "eu-west"},
        )
    )
    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        ingest_production_trace(store, _evidence(trace))
        stored = store.execution_traces.get("production-run-1")

    assert stored.events[0].arguments == {}
    assert "password" not in stored.events[0].metadata
    assert "top-secret" not in stored.model_dump_json()


def test_existing_completed_session_sampling_still_selects_negative_feedback():
    summary = SessionSummary(session_id="session-1", started_at=NOW, ended_at=NOW)
    evaluation = SessionEvaluationResult(
        session_id="session-1",
        passed=True,
        evaluated_at=NOW,
    )

    events = sample_completed_session(summary, evaluation, feedback="thumbs down")

    assert [event.reason.value for event in events] == ["thumbs_down"]
