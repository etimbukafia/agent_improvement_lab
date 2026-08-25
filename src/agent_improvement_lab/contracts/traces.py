"""Trace contracts that contain observed agent behavior and safe summaries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from agent_improvement_lab.contracts.common import ContractModel, require_aware_utc


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


class ObservedToolCall(ContractModel):
    """One tool call observed in a trace."""

    call_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    outcome: ToolCallOutcome
    result_summary: str | None = None
    error_type: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_call(self) -> "ObservedToolCall":
        object.__setattr__(self, "started_at", require_aware_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", require_aware_utc(self.ended_at))
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must be after started_at")
        if self.outcome == ToolCallOutcome.ERROR and not self.error_type:
            raise ValueError("error_type is required when outcome is error")
        if self.outcome == ToolCallOutcome.SUCCESS and self.error_type is not None:
            raise ValueError("error_type is not allowed when outcome is success")
        return self


class ObservedTurn(ContractModel):
    """One user and assistant turn in a trace."""

    turn_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    input_text: str = Field(min_length=1)
    output_text: str | None = None
    tool_calls: tuple[ObservedToolCall, ...] = ()
    started_at: datetime
    ended_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)

    @model_validator(mode="after")
    def validate_turn(self) -> "ObservedTurn":
        object.__setattr__(self, "started_at", require_aware_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", require_aware_utc(self.ended_at))
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must be after started_at")
        sequences = [call.sequence for call in self.tool_calls]
        if len(sequences) != len(set(sequences)):
            raise ValueError("Tool call sequence values must be unique within a turn")
        return self


class AgentTrace(ContractModel):
    """Observed execution data for one evaluation case."""

    trace_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    session_id: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    turns: tuple[ObservedTurn, ...] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_trace(self) -> "AgentTrace":
        object.__setattr__(self, "started_at", require_aware_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", require_aware_utc(self.ended_at))
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must be after started_at")
        sequences = [turn.sequence for turn in self.turns]
        if len(sequences) != len(set(sequences)):
            raise ValueError("Turn sequence values must be unique within a trace")
        return self


class TraceSummary(ContractModel):
    """Safe trace metadata without raw prompts or tool results."""

    trace_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    session_id: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    total_latency_ms: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    turn_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    tool_error_count: int = Field(default=0, ge=0)
    evaluation_score_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def normalize_timestamps(self) -> "TraceSummary":
        object.__setattr__(self, "started_at", require_aware_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", require_aware_utc(self.ended_at))
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must be after started_at")
        return self


class SessionTrace(ContractModel):
    """Link between a session and an ordered trace."""

    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
