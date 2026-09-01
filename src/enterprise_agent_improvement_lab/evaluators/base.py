"""Shared evaluator contracts and trace helpers."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable, Sequence

from enterprise_agent_improvement_lab.contracts.cases import EnterpriseEvaluationCase
from enterprise_agent_improvement_lab.contracts.evaluation_environment import (
    StateComparison,
    StateSnapshot,
)
from enterprise_agent_improvement_lab.contracts.failures import FailureCategory
from enterprise_agent_improvement_lab.contracts.traces import (
    ExecutionTrace,
    MessageEvent,
    ToolCallEvent,
)


@dataclass(frozen=True)
class EvaluationContext:
    """Inputs available to one deterministic Lab evaluator."""

    case: EnterpriseEvaluationCase
    trace: ExecutionTrace
    initial_state: StateSnapshot | None = None
    final_state: StateSnapshot | None = None
    state_comparison: StateComparison | None = None


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


def ordered_messages(trace: ExecutionTrace) -> list[MessageEvent]:
    """Return message events in execution order."""

    return [event for event in trace.ordered_events() if isinstance(event, MessageEvent)]


def ordered_tool_calls(trace: ExecutionTrace) -> list[ToolCallEvent]:
    """Return tool-call events in execution order."""

    return [event for event in trace.ordered_events() if isinstance(event, ToolCallEvent)]


def metadata_value(case: EnterpriseEvaluationCase, key: str, default: Any = None) -> Any:
    """Read one optional evaluator setting from case metadata."""

    return case.metadata.get(key, default)


def metadata_strings(case: EnterpriseEvaluationCase, key: str) -> tuple[str, ...]:
    """Read a string list from case metadata."""

    value = metadata_value(case, key, ())
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value)


def trace_latency_ms(trace: ExecutionTrace) -> int:
    """Calculate deterministic wall-clock trace latency."""

    if trace.ended_at is not None:
        return max(0, int((trace.ended_at - trace.started_at).total_seconds() * 1000))
    return sum(event.duration_ms for event in trace.events)


def trace_total_tokens(trace: ExecutionTrace) -> int:
    """Return the recorded execution token count."""

    return trace.usage.total_tokens


def trace_total_cost(trace: ExecutionTrace) -> float | None:
    """Read the recorded execution cost."""

    return trace.cost


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
