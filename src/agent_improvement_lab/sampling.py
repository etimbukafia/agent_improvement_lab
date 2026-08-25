"""Rules for sampling completed sessions for human review."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from agent_improvement_lab.contracts.common import require_aware_utc
from agent_improvement_lab.contracts.failures import (
    EvaluationScore,
    SamplingEvent,
    SamplingReason,
)
from agent_improvement_lab.contracts.sessions import SessionEvaluationResult, SessionSummary
from agent_improvement_lab.contracts.traces import AgentTrace, ToolCallOutcome
from agent_improvement_lab.evaluators.base import normalize_text, ordered_tool_calls, ordered_turns


@dataclass(frozen=True)
class SamplingPolicy:
    """Thresholds and evaluator hints for completed-session sampling."""

    low_judge_confidence_threshold: float = 0.6
    latency_limit_ms: int | None = None
    token_limit: int | None = None
    deterministic_evaluator_ids: frozenset[str] = field(default_factory=frozenset)
    judge_evaluator_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not 0.0 <= self.low_judge_confidence_threshold <= 1.0:
            raise ValueError("low_judge_confidence_threshold must be between 0 and 1")
        if self.latency_limit_ms is not None and self.latency_limit_ms < 0:
            raise ValueError("latency_limit_ms must not be negative")
        if self.token_limit is not None and self.token_limit < 0:
            raise ValueError("token_limit must not be negative")


def sample_completed_session(
    summary: SessionSummary,
    evaluation: SessionEvaluationResult,
    *,
    traces: Sequence[AgentTrace] = (),
    feedback: str | None = None,
    critic_rejected: bool = False,
    unrecognized_intent: bool = False,
    deterministic_verification_failed: bool | None = None,
    policy: SamplingPolicy | None = None,
    created_at: datetime | None = None,
) -> tuple[SamplingEvent, ...]:
    """Return one persisted sampling event for each matching rule.

    The function requires a completed session and a matching evaluation.
    Events contain reasons, not raw prompts or tool results.
    """

    if summary.session_id != evaluation.session_id:
        raise ValueError("Session summary and evaluation IDs must match")
    if summary.ended_at is None:
        raise ValueError("A session must have ended before it can be sampled")
    active_policy = policy or SamplingPolicy()
    scores = evaluation.scores
    reasons: list[SamplingReason] = []

    if _is_thumbs_down(feedback):
        reasons.append(SamplingReason.THUMBS_DOWN)
    if _verification_failed(scores, active_policy, deterministic_verification_failed):
        reasons.append(SamplingReason.DETERMINISTIC_VERIFICATION_FAILURE)
    if _low_judge_confidence(scores, active_policy):
        reasons.append(SamplingReason.LOW_JUDGE_CONFIDENCE)
    if critic_rejected:
        reasons.append(SamplingReason.CRITIC_REJECTION)
    if _has_tool_error(traces, scores):
        reasons.append(SamplingReason.TOOL_ERROR)
    if _has_repeated_clarification(scores, traces):
        reasons.append(SamplingReason.REPEATED_CLARIFICATION)
    if (
        active_policy.latency_limit_ms is not None
        and summary.total_latency_ms > active_policy.latency_limit_ms
    ):
        reasons.append(SamplingReason.EXCESSIVE_LATENCY)
    if active_policy.token_limit is not None and summary.total_tokens > active_policy.token_limit:
        reasons.append(SamplingReason.EXCESSIVE_TOKENS)
    if unrecognized_intent:
        reasons.append(SamplingReason.UNRECOGNIZED_INTENT)

    stamp = require_aware_utc(created_at or evaluation.evaluated_at)
    return tuple(
        SamplingEvent(
            event_id=f"sampling:{summary.session_id}:{reason.value}",
            session_id=summary.session_id,
            reason=reason,
            trace_ids=summary.trace_ids,
            created_at=stamp,
            metadata={"source": "completed_session"},
        )
        for reason in sorted(set(reasons), key=lambda item: item.value)
    )


def _is_thumbs_down(feedback: str | None) -> bool:
    if not isinstance(feedback, str):
        return False
    return normalize_text(feedback) in {
        "thumbs down",
        "thumbs-down",
        "thumbs_down",
        "down",
        "negative",
    }


def _verification_failed(
    scores: Sequence[EvaluationScore],
    policy: SamplingPolicy,
    explicit: bool | None,
) -> bool:
    if explicit is not None:
        return explicit
    if policy.deterministic_evaluator_ids:
        return any(
            not score.passed and score.evaluator_id in policy.deterministic_evaluator_ids
            for score in scores
        )
    return any(
        not score.passed and score.confidence == 1.0 and not _looks_like_judge(score.evaluator_id)
        for score in scores
    )


def _low_judge_confidence(scores: Sequence[EvaluationScore], policy: SamplingPolicy) -> bool:
    return any(
        score.confidence is not None
        and score.confidence < policy.low_judge_confidence_threshold
        and (
            not policy.judge_evaluator_ids
            and _looks_like_judge(score.evaluator_id)
            or score.evaluator_id in policy.judge_evaluator_ids
        )
        for score in scores
    )


def _looks_like_judge(evaluator_id: str) -> bool:
    normalized = evaluator_id.casefold()
    return any(marker in normalized for marker in ("judge", "critic", "subjective"))


def _has_tool_error(traces: Sequence[AgentTrace], scores: Sequence[EvaluationScore]) -> bool:
    return any(
        call.outcome == ToolCallOutcome.ERROR
        for trace in traces
        for call in ordered_tool_calls(trace)
    ) or any(
        not score.passed
        and any(marker in score.evaluator_id.casefold() for marker in ("tool.error", "error_rate"))
        for score in scores
    )


def _has_repeated_clarification(
    scores: Sequence[EvaluationScore], traces: Sequence[AgentTrace]
) -> bool:
    if any(
        not score.passed and "repeated_question" in score.evaluator_id.casefold()
        for score in scores
    ):
        return True
    for trace in traces:
        questions = [normalize_text(turn.input_text) for turn in ordered_turns(trace)]
        if len(questions) != len(set(questions)):
            return True
    return False
