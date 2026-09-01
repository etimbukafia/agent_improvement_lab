from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from enterprise_agent_improvement_lab.contracts.traces import (
    ApprovalDecision,
    ApprovalDecisionEvent,
    ApprovalRequestEvent,
    DelegationEvent,
    ErrorEvent,
    ExecutionEventStatus,
    ExecutionTrace,
    ExecutionTraceSummary,
    ExternalEvent,
    ExternalEventDirection,
    HumanActionEvent,
    MessageEvent,
    ModelCallEvent,
    RetrievalEvent,
    StateMutationEvent,
    StateReadEvent,
    TokenUsage,
    ToolCallEvent,
    ToolCallOutcome,
    TriggerInfo,
    WorkflowTransitionEvent,
    summarize_execution_trace,
)
from enterprise_agent_improvement_lab.dashboard import DashboardQueryService
from enterprise_agent_improvement_lab.storage import SQLiteStore

UTC = timezone.utc


def _timestamp(second: int = 0) -> datetime:
    return datetime(2026, 8, 23, 12, 0, second, tzinfo=UTC)


def _trace(*events, ended_at: datetime | None = None, **kwargs) -> ExecutionTrace:
    return ExecutionTrace(
        execution_id="execution-1",
        agent_id="agent-1",
        agent_version="1.0.0",
        candidate_id="candidate-1",
        started_at=_timestamp(),
        ended_at=ended_at,
        events=tuple(events),
        **kwargs,
    )


def test_conversational_execution_trace_preserves_tool_evidence_and_order() -> None:
    trace = _trace(
        MessageEvent(
            event_id="input",
            sequence=0,
            timestamp=_timestamp(),
            message_id="input",
            role="user",
            message_summary="Find my order.",
        ),
        ToolCallEvent(
            event_id="call-1",
            sequence=1,
            timestamp=_timestamp(1),
            duration_ms=1000,
            call_id="call-1",
            name="get_order",
            arguments={"order_id": "order-1"},
            outcome=ToolCallOutcome.ERROR,
            error_type="TimeoutError",
            result_summary="The order service timed out.",
        ),
        MessageEvent(
            event_id="output",
            sequence=2,
            timestamp=_timestamp(3),
            message_id="output",
            role="assistant",
            message_summary="I could not retrieve the order.",
        ),
        trigger=TriggerInfo(kind="message", source="api"),
        usage=TokenUsage(input_tokens=4, output_tokens=6),
        ended_at=_timestamp(3),
    )

    assert trace.execution_id == "execution-1"
    assert trace.trigger.kind == "message"
    assert [event.sequence for event in trace.events] == [0, 1, 2]
    assert isinstance(trace.events[0], MessageEvent)
    assert isinstance(trace.events[1], ToolCallEvent)
    assert isinstance(trace.events[2], MessageEvent)
    call = trace.events[1]
    assert isinstance(call, ToolCallEvent)
    assert call.arguments == {"order_id": "order-1"}
    assert call.outcome == ToolCallOutcome.ERROR
    assert call.status == ExecutionEventStatus.ERROR
    assert call.error_type == "TimeoutError"
    assert call.duration_ms == 1000
    assert trace.usage.total_tokens == 10


def test_background_execution_supports_typed_non_conversational_events() -> None:
    trace = _trace(
        ExternalEvent(
            event_id="external-1",
            sequence=2,
            timestamp=_timestamp(2),
            external_event_id="shipment-created",
            source="shipping-service",
            name="shipment.created",
            direction=ExternalEventDirection.RECEIVED,
            payload_summary="one shipment event",
        ),
        ModelCallEvent(
            event_id="model-1",
            sequence=1,
            timestamp=_timestamp(1),
            duration_ms=120,
            model="model-1",
            provider="provider-1",
            usage=TokenUsage(input_tokens=8, output_tokens=4),
            cost=0.02,
        ),
        WorkflowTransitionEvent(
            event_id="workflow-1",
            sequence=3,
            timestamp=_timestamp(3),
            workflow_id="shipment-workflow",
            from_state="queued",
            to_state="processing",
            transition="start_processing",
        ),
        trigger=TriggerInfo(kind="event", source="shipping-service", name="shipment.created"),
    )

    assert trace.trigger.kind == "event"
    assert [event.event_id for event in trace.ordered_events()] == [
        "model-1",
        "external-1",
        "workflow-1",
    ]
    assert isinstance(trace.ordered_events()[0], ModelCallEvent)
    assert isinstance(trace.ordered_events()[1], ExternalEvent)
    assert isinstance(trace.ordered_events()[2], WorkflowTransitionEvent)
    assert trace.ordered_events()[0].usage.total_tokens == 12


def test_all_enterprise_event_contracts_round_trip_through_execution_trace() -> None:
    events = (
        ModelCallEvent(event_id="model", sequence=0, timestamp=_timestamp(), model="model-1"),
        ToolCallEvent(
            event_id="tool",
            sequence=1,
            timestamp=_timestamp(),
            call_id="call-1",
            name="get_order",
            outcome=ToolCallOutcome.SUCCESS,
        ),
        MessageEvent(
            event_id="message",
            sequence=2,
            timestamp=_timestamp(),
            message_id="message-1",
            role="assistant",
        ),
        StateReadEvent(
            event_id="read",
            sequence=3,
            timestamp=_timestamp(),
            read_id="read-1",
            resource="orders/order-1",
        ),
        StateMutationEvent(
            event_id="mutation",
            sequence=4,
            timestamp=_timestamp(),
            mutation_id="mutation-1",
            resource="orders/order-1",
            operation="update",
        ),
        RetrievalEvent(
            event_id="retrieval",
            sequence=5,
            timestamp=_timestamp(),
            retrieval_id="retrieval-1",
            source="policy-index",
        ),
        ApprovalRequestEvent(
            event_id="approval-request",
            sequence=6,
            timestamp=_timestamp(),
            approval_id="approval-1",
            action="issue_refund",
        ),
        ApprovalDecisionEvent(
            event_id="approval-decision",
            sequence=7,
            timestamp=_timestamp(),
            approval_id="approval-1",
            decision=ApprovalDecision.APPROVED,
        ),
        DelegationEvent(
            event_id="delegation",
            sequence=8,
            timestamp=_timestamp(),
            delegation_id="delegation-1",
            target_agent_id="billing-agent",
        ),
        WorkflowTransitionEvent(
            event_id="transition",
            sequence=9,
            timestamp=_timestamp(),
            workflow_id="refund-workflow",
            to_state="approved",
        ),
        ExternalEvent(
            event_id="external",
            sequence=10,
            timestamp=_timestamp(),
            external_event_id="external-1",
            source="billing-service",
            name="refund.created",
            direction=ExternalEventDirection.EMITTED,
        ),
        HumanActionEvent(
            event_id="human",
            sequence=11,
            timestamp=_timestamp(),
            action_id="action-1",
            actor_id="reviewer-1",
            action="confirm_refund",
        ),
        ErrorEvent(
            event_id="error",
            sequence=12,
            timestamp=_timestamp(),
            error_type="DownstreamTimeout",
        ),
    )
    trace = _trace(*events)

    restored = ExecutionTrace.model_validate(trace.model_dump(mode="json"))

    assert [type(event) for event in restored.events] == [type(event) for event in events]
    assert restored.events[4].operation == "update"
    assert restored.events[6].approval_id == restored.events[7].approval_id


def test_approval_and_state_mutation_events_are_explicit_and_summarized() -> None:
    trace = _trace(
        ApprovalRequestEvent(
            event_id="approval-request",
            sequence=0,
            timestamp=_timestamp(),
            approval_id="approval-1",
            action="issue_refund",
        ),
        StateMutationEvent(
            event_id="mutation",
            sequence=1,
            timestamp=_timestamp(1),
            duration_ms=25,
            mutation_id="mutation-1",
            resource="orders/order-1",
            operation="update_status",
            changed_paths=("status",),
        ),
        ApprovalDecisionEvent(
            event_id="approval-decision",
            sequence=2,
            timestamp=_timestamp(2),
            approval_id="approval-1",
            decision=ApprovalDecision.APPROVED,
        ),
    )

    summary = summarize_execution_trace(trace)

    assert isinstance(trace.events[1], StateMutationEvent)
    assert trace.events[1].operation == "update_status"
    assert summary.write_count == 1
    assert summary.approval_count == 2


def test_duplicate_event_sequences_fail_and_out_of_order_events_are_sorted() -> None:
    with pytest.raises(ValidationError, match="Event sequence values must be unique"):
        _trace(
            MessageEvent(
                event_id="message-1",
                sequence=0,
                timestamp=_timestamp(),
                message_id="message-1",
                role="user",
            ),
            MessageEvent(
                event_id="message-2",
                sequence=0,
                timestamp=_timestamp(),
                message_id="message-2",
                role="assistant",
            ),
        )

    trace = _trace(
        MessageEvent(
            event_id="message-2",
            sequence=1,
            timestamp=_timestamp(1),
            message_id="message-2",
            role="assistant",
        ),
        MessageEvent(
            event_id="message-1",
            sequence=0,
            timestamp=_timestamp(),
            message_id="message-1",
            role="user",
        ),
    )
    assert [event.sequence for event in trace.events] == [0, 1]
    assert [event.event_id for event in trace.ordered_events()] == ["message-1", "message-2"]


def test_event_timestamps_require_aware_utc_values() -> None:
    with pytest.raises(ValidationError, match="UTC offset"):
        MessageEvent(
            event_id="message-1",
            sequence=0,
            timestamp=datetime(2026, 8, 23, 12, 0),
            message_id="message-1",
            role="user",
        )


def test_execution_summary_excludes_raw_event_payloads_and_counts_safe_metrics() -> None:
    trace = _trace(
        ModelCallEvent(
            event_id="model",
            sequence=0,
            timestamp=_timestamp(),
            duration_ms=100,
            model="model-1",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            cost=0.03,
        ),
        ToolCallEvent(
            event_id="tool",
            sequence=1,
            timestamp=_timestamp(1),
            call_id="call-1",
            name="get_secret",
            arguments={"token": "raw-secret-payload"},
            outcome=ToolCallOutcome.ERROR,
            error_type="PermissionError",
            input_summary="raw-secret-payload",
        ),
        DelegationEvent(
            event_id="delegation",
            sequence=2,
            timestamp=_timestamp(2),
            delegation_id="delegation-1",
            target_agent_id="review-agent",
        ),
        ErrorEvent(
            event_id="error",
            sequence=3,
            timestamp=_timestamp(3),
            error_type="PermissionError",
            message_summary="raw-secret-payload",
        ),
        ended_at=_timestamp(4),
    )

    summary = trace.to_summary()
    payload = summary.model_dump_json()

    assert isinstance(summary, ExecutionTraceSummary)
    assert summary.total_latency_ms == 4000
    assert summary.total_tokens == 15
    assert summary.total_cost == 0.03
    assert summary.event_count == 4
    assert summary.tool_call_count == 1
    assert summary.delegation_count == 1
    assert summary.error_count == 2
    assert "events" not in summary.model_dump()
    assert "raw-secret-payload" not in payload


def test_execution_trace_and_summary_use_parallel_storage_records(tmp_path) -> None:
    trace = _trace(
        MessageEvent(
            event_id="message-1",
            sequence=0,
            timestamp=_timestamp(),
            message_id="message-1",
            role="user",
        )
    )
    summary = trace.to_summary()

    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.execution_traces.save(trace)
        store.execution_trace_summaries.save(summary)

        assert store.execution_traces.get(trace.execution_id) == trace
        assert store.execution_trace_summaries.get(trace.execution_id) == summary
        view = DashboardQueryService(store).execution_trace(trace.execution_id)
        assert view.trace == trace
        assert view.summary == summary
