"""Trace contracts that contain observed agent behavior and safe summaries."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator

from enterprise_agent_improvement_lab.contracts.common import ContractModel, require_aware_utc


class ToolCallOutcome(StrEnum):
    """Result state for a tool call."""

    SUCCESS = "success"
    ERROR = "error"


class TokenUsage(ContractModel):
    """Token counts recorded by a runtime."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> "TokenUsage":
        expected = self.input_tokens + self.output_tokens
        if self.total_tokens not in (0, expected):
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        if self.total_tokens == 0 and expected:
            object.__setattr__(self, "total_tokens", expected)
        return self


class SessionTrace(ContractModel):
    """Link between a session and an ordered trace."""

    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)


class ExecutionEventType(StrEnum):
    """Supported enterprise execution event types."""

    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    MESSAGE = "message"
    STATE_READ = "state_read"
    STATE_MUTATION = "state_mutation"
    RETRIEVAL = "retrieval"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_DECISION = "approval_decision"
    DELEGATION = "delegation"
    WORKFLOW_TRANSITION = "workflow_transition"
    EXTERNAL_EVENT = "external_event"
    HUMAN_ACTION = "human_action"
    ERROR = "error"


class ExecutionEventStatus(StrEnum):
    """Status values shared by enterprise execution events."""

    SUCCESS = "success"
    ERROR = "error"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    DENIED = "denied"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ApprovalDecision(StrEnum):
    """Decision values recorded for an approval request."""

    APPROVED = "approved"
    REJECTED = "rejected"
    REQUEST_CHANGES = "request_changes"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ExternalEventDirection(StrEnum):
    """Direction of an event exchanged with an external system."""

    RECEIVED = "received"
    EMITTED = "emitted"


class TriggerInfo(ContractModel):
    """Safe information about what started an execution."""

    kind: str = Field(
        min_length=1,
        validation_alias=AliasChoices("kind", "type", "trigger_type"),
    )
    source: str | None = None
    name: str | None = None
    event_id: str | None = None
    summary: str | None = None

    @property
    def type(self) -> str:
        """Return the trigger kind using a common alternate name."""

        return self.kind


class ExecutionEvent(ContractModel):
    """Common safe envelope for one ordered enterprise execution event."""

    event_type: ExecutionEventType
    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    timestamp: datetime
    duration_ms: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("duration_ms", "duration"),
    )
    status: ExecutionEventStatus = ExecutionEventStatus.SUCCESS
    input_summary: str | None = None
    output_summary: str | None = None
    evidence_refs: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("evidence_refs", "evidence_references"),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "ExecutionEvent":
        object.__setattr__(self, "timestamp", require_aware_utc(self.timestamp))
        return self

    @property
    def duration(self) -> int:
        """Return the event duration in milliseconds."""

        return self.duration_ms

    @property
    def evidence_references(self) -> tuple[str, ...]:
        """Return evidence references using a descriptive alternate name."""

        return self.evidence_refs


class ModelCallEvent(ExecutionEvent):
    """A model or language-model provider call."""

    event_type: Literal[ExecutionEventType.MODEL_CALL] = ExecutionEventType.MODEL_CALL
    model: str = Field(min_length=1)
    provider: str | None = None
    usage: TokenUsage = Field(
        default_factory=TokenUsage,
        validation_alias=AliasChoices("usage", "token_usage"),
    )
    cost: float | None = Field(default=None, ge=0)
    finish_reason: str | None = None
    error_type: str | None = None

    @field_validator("cost")
    @classmethod
    def validate_cost(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("cost must be finite")
        return value


class ToolCallEvent(ExecutionEvent):
    """A tool call with explicit arguments and safe result evidence."""

    event_type: Literal[ExecutionEventType.TOOL_CALL] = ExecutionEventType.TOOL_CALL
    call_id: str = Field(min_length=1)
    name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("name", "tool_name"),
    )
    arguments: dict[str, Any] = Field(default_factory=dict)
    outcome: ToolCallOutcome
    result_summary: str | None = None
    error_type: str | None = None
    resource_id: str | None = None
    tenant_id: str | None = None
    principal_id: str | None = None
    authorization_granted: bool | None = None
    idempotency_key_digest: str | None = None
    retry_count: int = Field(default=0, ge=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    compensation_for_call_id: str | None = None
    side_effect_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_call(self) -> "ToolCallEvent":
        if self.outcome == ToolCallOutcome.ERROR and not self.error_type:
            raise ValueError("error_type is required when outcome is error")
        if self.outcome == ToolCallOutcome.SUCCESS and self.error_type is not None:
            raise ValueError("error_type is not allowed when outcome is success")
        if self.outcome == ToolCallOutcome.ERROR and self.status == ExecutionEventStatus.SUCCESS:
            object.__setattr__(self, "status", ExecutionEventStatus.ERROR)
        if self.outcome == ToolCallOutcome.SUCCESS and self.status == ExecutionEventStatus.ERROR:
            raise ValueError("Successful tool calls cannot have error status")
        return self

    @property
    def tool_name(self) -> str:
        """Return the tool name using a descriptive alternate name."""

        return self.name


class MessageEvent(ExecutionEvent):
    """A message emitted or received during execution."""

    event_type: Literal[ExecutionEventType.MESSAGE] = ExecutionEventType.MESSAGE
    message_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    channel: str | None = None
    message_summary: str | None = Field(
        default=None,
        validation_alias=AliasChoices("message_summary", "content_summary"),
    )


class StateReadEvent(ExecutionEvent):
    """A read from an execution state resource."""

    event_type: Literal[ExecutionEventType.STATE_READ] = ExecutionEventType.STATE_READ
    read_id: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    result_summary: str | None = None


class StateMutationEvent(ExecutionEvent):
    """An explicit mutation of an execution state resource."""

    event_type: Literal[ExecutionEventType.STATE_MUTATION] = ExecutionEventType.STATE_MUTATION
    mutation_id: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    changed_paths: tuple[str, ...] = ()
    before_state_ref: str | None = None
    after_state_ref: str | None = None
    transaction_id: str | None = None


class RetrievalEvent(ExecutionEvent):
    """A retrieval operation against a declared source."""

    event_type: Literal[ExecutionEventType.RETRIEVAL] = ExecutionEventType.RETRIEVAL
    retrieval_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    result_count: int = Field(default=0, ge=0)
    document_refs: tuple[str, ...] = ()
    tenant_id: str | None = None
    principal_id: str | None = None
    authorized: bool | None = None
    source_version: str | None = None
    retrieved_at: datetime | None = None

    @model_validator(mode="after")
    def normalize_retrieval_timestamp(self) -> "RetrievalEvent":
        if self.retrieved_at is not None:
            object.__setattr__(self, "retrieved_at", require_aware_utc(self.retrieved_at))
        return self


class ApprovalRequestEvent(ExecutionEvent):
    """A request for human or policy approval before an action."""

    event_type: Literal[ExecutionEventType.APPROVAL_REQUEST] = ExecutionEventType.APPROVAL_REQUEST
    approval_id: str = Field(min_length=1)
    action: str = Field(
        min_length=1,
        validation_alias=AliasChoices("action", "requested_action"),
    )
    requester: str | None = Field(
        default=None,
        validation_alias=AliasChoices("requester", "requested_by"),
    )
    approver: str | None = Field(
        default=None,
        validation_alias=AliasChoices("approver", "approver_role"),
    )
    reason_summary: str | None = None
    expires_at: datetime | None = None
    status: ExecutionEventStatus = ExecutionEventStatus.REQUESTED

    @model_validator(mode="after")
    def normalize_expiry(self) -> "ApprovalRequestEvent":
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", require_aware_utc(self.expires_at))
        return self


class ApprovalDecisionEvent(ExecutionEvent):
    """A decision for a prior approval request."""

    event_type: Literal[ExecutionEventType.APPROVAL_DECISION] = ExecutionEventType.APPROVAL_DECISION
    approval_id: str = Field(min_length=1)
    decision: ApprovalDecision
    reviewer: str | None = None
    reviewer_role: str | None = None
    reason_summary: str | None = None


class DelegationEvent(ExecutionEvent):
    """A task delegated to another agent or execution."""

    event_type: Literal[ExecutionEventType.DELEGATION] = ExecutionEventType.DELEGATION
    delegation_id: str = Field(min_length=1)
    target_agent_id: str = Field(min_length=1)
    task_summary: str | None = None
    source_agent_id: str | None = None
    child_execution_id: str | None = None
    authorized_tool_ids: tuple[str, ...] = ()
    granted_permissions: tuple[str, ...] = ()
    context_checksum: str | None = None
    result_validated: bool | None = None


class WorkflowTransitionEvent(ExecutionEvent):
    """A transition between workflow states."""

    event_type: Literal[ExecutionEventType.WORKFLOW_TRANSITION] = (
        ExecutionEventType.WORKFLOW_TRANSITION
    )
    workflow_id: str = Field(min_length=1)
    from_state: str | None = None
    to_state: str = Field(min_length=1)
    transition: str | None = None


class ExternalEvent(ExecutionEvent):
    """An event received from or sent to an external system."""

    event_type: Literal[ExecutionEventType.EXTERNAL_EVENT] = ExecutionEventType.EXTERNAL_EVENT
    external_event_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    name: str = Field(min_length=1)
    direction: ExternalEventDirection
    payload_summary: str | None = None


class HumanActionEvent(ExecutionEvent):
    """An action taken by a human during an execution."""

    event_type: Literal[ExecutionEventType.HUMAN_ACTION] = ExecutionEventType.HUMAN_ACTION
    action_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target: str | None = None
    action_summary: str | None = None


class ErrorEvent(ExecutionEvent):
    """An execution error with a safe message summary."""

    event_type: Literal[ExecutionEventType.ERROR] = ExecutionEventType.ERROR
    error_type: str = Field(min_length=1)
    message_summary: str | None = None
    recoverable: bool = False
    source_event_id: str | None = None
    status: ExecutionEventStatus = ExecutionEventStatus.ERROR


ExecutionEventRecord = Annotated[
    ModelCallEvent
    | ToolCallEvent
    | MessageEvent
    | StateReadEvent
    | StateMutationEvent
    | RetrievalEvent
    | ApprovalRequestEvent
    | ApprovalDecisionEvent
    | DelegationEvent
    | WorkflowTransitionEvent
    | ExternalEvent
    | HumanActionEvent
    | ErrorEvent,
    Field(discriminator="event_type"),
]


class ExecutionTrace(ContractModel):
    """Generic ordered execution evidence for any enterprise agent."""

    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    case_id: str | None = None
    session_id: str | None = None
    principal_id: str | None = None
    tenant_id: str | None = None
    principal_roles: tuple[str, ...] = ()
    # Safe exact provenance references.  The Lab stores identities only; it
    # does not copy prompt text or runtime registry records into a trace.
    manifest_id: str | None = Field(default=None, min_length=1)
    manifest_digest: str | None = Field(default=None, min_length=1)
    registry_snapshot_id: str | None = Field(default=None, min_length=1)
    prompt_ref: str | None = Field(default=None, min_length=1)
    skill_refs: tuple[str, ...] = ()
    trigger: TriggerInfo = Field(default_factory=lambda: TriggerInfo(kind="unknown"))
    started_at: datetime
    ended_at: datetime | None = None
    events: tuple[ExecutionEventRecord, ...] = Field(min_length=1)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: float | None = Field(default=None, ge=0)
    evidence_refs: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("evidence_refs", "evidence_references"),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("cost")
    @classmethod
    def validate_cost(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("cost must be finite")
        return value

    @model_validator(mode="after")
    def validate_trace(self) -> "ExecutionTrace":
        object.__setattr__(self, "started_at", require_aware_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", require_aware_utc(self.ended_at))
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must be after started_at")

        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Event IDs must be unique within an execution trace")
        sequences = [event.sequence for event in self.events]
        if len(sequences) != len(set(sequences)):
            raise ValueError("Event sequence values must be unique within an execution trace")
        if len(self.skill_refs) != len(set(self.skill_refs)):
            raise ValueError("skill_refs must be unique within an execution trace")

        ordered = tuple(sorted(self.events, key=lambda event: event.sequence))
        if ordered != self.events:
            object.__setattr__(self, "events", ordered)
        return self

    @property
    def trace_id(self) -> str:
        """Return the execution ID using the legacy trace name."""

        return self.execution_id

    def ordered_events(self) -> tuple[ExecutionEventRecord, ...]:
        """Return events in deterministic sequence order."""

        return self.events

    def to_summary(self, score_ids: tuple[str, ...] = ()) -> "ExecutionTraceSummary":
        """Create a safe aggregate summary without event payloads."""

        return summarize_execution_trace(self, score_ids)


class ExecutionTraceSummary(ContractModel):
    """Safe aggregate fields for an enterprise execution trace."""

    execution_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    case_id: str | None = None
    session_id: str | None = None
    trigger_kind: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime | None = None
    total_latency_ms: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    total_cost: float | None = Field(default=None, ge=0)
    event_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    write_count: int = Field(default=0, ge=0)
    approval_count: int = Field(default=0, ge=0)
    delegation_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    evidence_refs: tuple[str, ...] = ()
    manifest_id: str | None = Field(default=None, min_length=1)
    manifest_digest: str | None = Field(default=None, min_length=1)
    registry_snapshot_id: str | None = Field(default=None, min_length=1)
    prompt_ref: str | None = Field(default=None, min_length=1)
    skill_refs: tuple[str, ...] = ()
    evaluation_score_ids: tuple[str, ...] = ()

    @field_validator("total_cost")
    @classmethod
    def validate_cost(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("total_cost must be finite")
        return value

    @model_validator(mode="after")
    def normalize_timestamps(self) -> "ExecutionTraceSummary":
        object.__setattr__(self, "started_at", require_aware_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", require_aware_utc(self.ended_at))
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must be after started_at")
        return self

    @property
    def trace_id(self) -> str:
        """Return the execution ID using the legacy trace name."""

        return self.execution_id


def summarize_execution_trace(
    trace: ExecutionTrace,
    score_ids: tuple[str, ...] = (),
) -> ExecutionTraceSummary:
    """Create a safe summary from one complete enterprise execution trace."""

    events = trace.ordered_events()
    total_tokens = trace.usage.total_tokens
    if total_tokens == 0:
        total_tokens = sum(
            event.usage.total_tokens for event in events if isinstance(event, ModelCallEvent)
        )

    total_cost = trace.cost
    if total_cost is None:
        event_costs: list[float] = []
        for event in events:
            if isinstance(event, ModelCallEvent) and event.cost is not None:
                event_costs.append(event.cost)
        total_cost = sum(event_costs) if event_costs else None

    if trace.ended_at is not None:
        total_latency_ms = max(
            0,
            int((trace.ended_at - trace.started_at).total_seconds() * 1000),
        )
    else:
        total_latency_ms = sum(event.duration_ms for event in events)

    return ExecutionTraceSummary(
        execution_id=trace.execution_id,
        agent_id=trace.agent_id,
        agent_version=trace.agent_version,
        candidate_id=trace.candidate_id,
        case_id=trace.case_id,
        session_id=trace.session_id,
        trigger_kind=trace.trigger.kind,
        started_at=trace.started_at,
        ended_at=trace.ended_at,
        total_latency_ms=total_latency_ms,
        total_tokens=total_tokens,
        total_cost=total_cost,
        event_count=len(events),
        tool_call_count=sum(isinstance(event, ToolCallEvent) for event in events),
        write_count=sum(isinstance(event, StateMutationEvent) for event in events),
        approval_count=sum(
            isinstance(event, (ApprovalRequestEvent, ApprovalDecisionEvent)) for event in events
        ),
        delegation_count=sum(isinstance(event, DelegationEvent) for event in events),
        error_count=sum(
            event.status in (ExecutionEventStatus.ERROR, ExecutionEventStatus.FAILED)
            for event in events
        ),
        evidence_refs=trace.evidence_refs,
        evaluation_score_ids=score_ids,
        manifest_id=trace.manifest_id,
        manifest_digest=trace.manifest_digest,
        registry_snapshot_id=trace.registry_snapshot_id,
        prompt_ref=trace.prompt_ref,
        skill_refs=trace.skill_refs,
    )


__all__ = [
    "ApprovalDecision",
    "ApprovalDecisionEvent",
    "ApprovalRequestEvent",
    "DelegationEvent",
    "ErrorEvent",
    "ExecutionEvent",
    "ExecutionEventRecord",
    "ExecutionEventStatus",
    "ExecutionEventType",
    "ExecutionTrace",
    "ExecutionTraceSummary",
    "ExternalEvent",
    "ExternalEventDirection",
    "HumanActionEvent",
    "MessageEvent",
    "ModelCallEvent",
    "RetrievalEvent",
    "SessionTrace",
    "StateMutationEvent",
    "StateReadEvent",
    "TokenUsage",
    "ToolCallEvent",
    "ToolCallOutcome",
    "TriggerInfo",
    "WorkflowTransitionEvent",
    "summarize_execution_trace",
]
