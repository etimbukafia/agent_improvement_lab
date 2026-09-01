"""Session and session-evaluation contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from enterprise_agent_improvement_lab.contracts.common import ContractModel, require_aware_utc
from enterprise_agent_improvement_lab.contracts.failures import EvaluationScore


class SessionSummary(ContractModel):
    """Safe session metadata without raw prompts or tool results."""

    session_id: str = Field(min_length=1)
    trace_ids: tuple[str, ...] = ()
    started_at: datetime
    ended_at: datetime | None = None
    total_latency_ms: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    trace_count: int = Field(default=0, ge=0)
    evaluation_score_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_summary(self) -> "SessionSummary":
        object.__setattr__(self, "started_at", require_aware_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", require_aware_utc(self.ended_at))
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must be after started_at")
        if self.trace_count and self.trace_count != len(self.trace_ids):
            raise ValueError("trace_count must match trace_ids")
        if not self.trace_count:
            object.__setattr__(self, "trace_count", len(self.trace_ids))
        return self


class SessionEvaluationResult(ContractModel):
    """Evaluation scores and failure references for one session."""

    session_id: str = Field(min_length=1)
    scores: tuple[EvaluationScore, ...] = ()
    failure_ids: tuple[str, ...] = ()
    passed: bool
    evaluated_at: datetime

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "SessionEvaluationResult":
        object.__setattr__(self, "evaluated_at", require_aware_utc(self.evaluated_at))
        return self
