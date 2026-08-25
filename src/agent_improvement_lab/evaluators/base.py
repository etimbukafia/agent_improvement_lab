"""Shared evaluator contracts and trace helpers."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable, Sequence

from agent_improvement_lab.contracts.cases import EvaluationCaseRef
from agent_improvement_lab.contracts.failures import FailureCategory
from agent_improvement_lab.contracts.traces import AgentTrace, ObservedToolCall, ObservedTurn


@dataclass(frozen=True)
class EvaluationContext:
    """Inputs available to one deterministic Lab evaluator."""

    case: EvaluationCaseRef
    trace: AgentTrace


@dataclass(frozen=True)
class EvaluationOutcome:
    """A validated evaluator result before it receives a persisted score ID."""

    score: float
    passed: bool
    explanation: str
    confidence: float | None = 1.0
    evidence_refs: tuple[str, ...] = ()
    failure_category: FailureCategory | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("Evaluator score must be finite and between 0 and 1")
        if not self.explanation.strip():
            raise ValueError("Evaluator explanation must not be empty")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Evaluator confidence must be between 0 and 1")


class LabEvaluator(ABC):
    """Base class for deterministic evaluator implementations."""

    evaluator_id: ClassVar[str]

    @abstractmethod
    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        """Evaluate one case and trace."""


def outcome(
    score: float,
    passed: bool,
    explanation: str,
    *,
    category: FailureCategory | None = None,
    confidence: float | None = 1.0,
    evidence_refs: Iterable[str] = (),
) -> EvaluationOutcome:
    """Build one evaluator outcome with a common shape."""

    return EvaluationOutcome(
        score=score,
        passed=passed,
        explanation=explanation,
        confidence=confidence,
        evidence_refs=tuple(evidence_refs),
        failure_category=category if not passed else None,
    )


def ordered_turns(trace: AgentTrace) -> list[ObservedTurn]:
    """Return turns in their declared order."""

    return sorted(trace.turns, key=lambda turn: turn.sequence)


def ordered_tool_calls(trace: AgentTrace) -> list[ObservedToolCall]:
    """Return tool calls in trace order."""

    calls: list[ObservedToolCall] = []
    for turn in ordered_turns(trace):
        calls.extend(sorted(turn.tool_calls, key=lambda call: call.sequence))
    return calls


def metadata_value(case: EvaluationCaseRef, key: str, default: Any = None) -> Any:
    """Read one optional evaluator setting from case metadata."""

    return case.metadata.get(key, default)


def metadata_strings(case: EvaluationCaseRef, key: str) -> tuple[str, ...]:
    """Read a string list from case metadata."""

    value = metadata_value(case, key, ())
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value)


def trace_latency_ms(trace: AgentTrace) -> int:
    """Calculate deterministic wall-clock trace latency."""

    if trace.ended_at is not None:
        return max(0, int((trace.ended_at - trace.started_at).total_seconds() * 1000))
    turn_latencies = [turn.latency_ms for turn in trace.turns if turn.latency_ms is not None]
    if turn_latencies:
        return sum(turn_latencies)
    return 0


def trace_total_tokens(trace: AgentTrace) -> int:
    """Return the sum of recorded turn token counts."""

    return sum(turn.token_usage.total_tokens for turn in trace.turns)


def trace_total_cost(trace: AgentTrace) -> float | None:
    """Read an optional deterministic cost value from trace metadata."""

    value = trace.metadata.get("estimated_cost")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def normalize_text(value: str) -> str:
    """Normalize user text for exact repeated-question checks."""

    return " ".join(value.casefold().split())


def numeric_claims(text: str) -> dict[str, tuple[float, ...]]:
    """Extract simple ``label: number`` claims from assistant text."""

    import re

    pattern = re.compile(
        r"(?P<label>[A-Za-z][A-Za-z0-9 _-]{0,40})\s*[:=]\s*"
        r"(?P<value>-?(?:\d+(?:\.\d+)?|\.\d+))%?"
    )
    claims: dict[str, list[float]] = {}
    for match in pattern.finditer(text):
        label = normalize_text(match.group("label")).strip(" -")
        claims.setdefault(label, []).append(float(match.group("value")))
    return {label: tuple(values) for label, values in claims.items()}


def style_signature(text: str) -> tuple[str, str]:
    """Return a small deterministic style signature for a response."""

    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        format_name = "structured"
    elif any(line.lstrip().startswith(("- ", "* ", "1. ")) for line in stripped.splitlines()):
        format_name = "list"
    else:
        format_name = "prose"
    length_band = "short" if len(stripped.split()) < 25 else "long"
    return format_name, length_band


def validate_evaluator_ids(evaluators: Sequence[LabEvaluator]) -> tuple[LabEvaluator, ...]:
    """Validate and freeze an evaluator collection."""

    seen: set[str] = set()
    result: list[LabEvaluator] = []
    for evaluator in evaluators:
        evaluator_id = getattr(evaluator, "evaluator_id", "")
        if not isinstance(evaluator_id, str) or not evaluator_id.strip():
            raise ValueError("Every evaluator must define a non-empty evaluator_id")
        if evaluator_id in seen:
            raise ValueError(f"Duplicate evaluator_id: {evaluator_id}")
        seen.add(evaluator_id)
        result.append(evaluator)
    return tuple(result)
