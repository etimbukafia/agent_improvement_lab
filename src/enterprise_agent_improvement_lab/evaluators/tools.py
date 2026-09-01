"""Deterministic tool-call and trajectory evaluators."""

from __future__ import annotations

import json
import re
from collections import Counter
from numbers import Real
from typing import Any

from enterprise_agent_improvement_lab.contracts.cases import ActionExpectation, NumericRange
from enterprise_agent_improvement_lab.contracts.failures import FailureCategory
from enterprise_agent_improvement_lab.contracts.traces import ToolCallOutcome
from enterprise_agent_improvement_lab.evaluators.base import (
    EvaluationContext,
    EvaluationOutcome,
    LabEvaluator,
    ordered_tool_calls,
    outcome,
)


def _expected_calls(context: EvaluationContext) -> list[ActionExpectation]:
    return sorted(
        (
            expectation
            for expectation in (*context.case.expected_actions, *context.case.required_actions)
            if expectation.action_type.casefold() in {"tool", "tool_call"}
        ),
        key=lambda expectation: (
            expectation.order is None,
            expectation.order if expectation.order is not None else 0,
            expectation.identity,
        ),
    )


def _aligned_calls(context: EvaluationContext) -> list[tuple[ActionExpectation, Any | None]]:
    expected = _expected_calls(context)
    actual = ordered_tool_calls(context.trace)
    return list(zip(expected, actual)) + [
        (expectation, None) for expectation in expected[len(actual) :]
    ]


class ToolSelectionAccuracy(LabEvaluator):
    """Check that the runtime selected the expected tool names."""

    evaluator_id = "tool.selection_accuracy"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        expected = [item.name for item in _expected_calls(context)]
        actual = [call.name for call in ordered_tool_calls(context.trace)]
        matches = sum((Counter(expected) & Counter(actual)).values())
        denominator = max(len(expected), len(actual), 1)
        score = matches / denominator
        passed = expected == actual
        explanation = (
            f"Expected tool sequence {expected!r}; observed {actual!r}. "
            f"Matched {matches} of {denominator} selections."
        )
        return outcome(score, passed, explanation, category=FailureCategory.TOOL_SELECTION)


class ToolArgumentAccuracy(LabEvaluator):
    """Check required and exact arguments for expected tool calls."""

    evaluator_id = "tool.argument_accuracy"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        expected = _expected_calls(context)
        actual = ordered_tool_calls(context.trace)
        if not expected and not actual:
            return outcome(1.0, True, "No tool arguments were expected or observed.")
        matched = 0
        for expectation, call in _aligned_calls(context):
            if call is None or call.name != expectation.name:
                continue
            required, exact, _, _, _, _ = _argument_sections(expectation)
            if any(key not in call.arguments for key in required):
                continue
            if any(call.arguments.get(key) != value for key, value in exact.items()):
                continue
            matched += 1
        denominator = max(len(expected), len(actual), 1)
        score = matched / denominator
        passed = len(expected) == len(actual) and matched == len(expected)
        return outcome(
            score,
            passed,
            f"Matched exact and required arguments for {matched} of {denominator} calls.",
            category=FailureCategory.ARGUMENTS,
        )


def _matches_type(value: Any, type_name: str) -> bool:
    normalized = type_name.casefold()
    if normalized in {"string", "str"}:
        return isinstance(value, str)
    if normalized in {"integer", "int"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized in {"number", "float"}:
        return isinstance(value, Real) and not isinstance(value, bool)
    if normalized in {"boolean", "bool"}:
        return isinstance(value, bool)
    if normalized in {"object", "dict"}:
        return isinstance(value, dict)
    if normalized in {"array", "list"}:
        return isinstance(value, (list, tuple))
    if normalized in {"null", "none"}:
        return value is None
    return False


def _matches_range(value: Any, bounds: NumericRange) -> bool:
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    numeric_value = float(value)
    if bounds.minimum is not None:
        if bounds.minimum_inclusive and numeric_value < bounds.minimum:
            return False
        if not bounds.minimum_inclusive and numeric_value <= bounds.minimum:
            return False
    if bounds.maximum is not None:
        if bounds.maximum_inclusive and numeric_value > bounds.maximum:
            return False
        if not bounds.maximum_inclusive and numeric_value >= bounds.maximum:
            return False
    return True


def _argument_sections(
    expectation: ActionExpectation,
) -> tuple[
    tuple[str, ...],
    dict[str, Any],
    dict[str, str],
    dict[str, tuple[Any, ...]],
    dict[str, str],
    dict[str, NumericRange],
]:
    arguments = dict(expectation.arguments)
    required = tuple(arguments.pop("__required_arguments__", ()))
    exact = dict(arguments)
    raw_exact = exact.pop("__exact_arguments__", None)
    if isinstance(raw_exact, dict):
        exact = dict(raw_exact)
    types = exact.pop("__argument_types__", {})
    allowed = exact.pop("__allowed_values__", {})
    patterns = exact.pop("__patterns__", {})
    ranges = exact.pop("__numeric_ranges__", {})
    protected = tuple(exact.pop("__protected_arguments__", ()))
    if protected:
        required = tuple(dict.fromkeys((*required, *protected)))
    return (
        required,
        exact,
        dict(types),
        dict(allowed),
        dict(patterns),
        {
            key: value if isinstance(value, NumericRange) else NumericRange.model_validate(value)
            for key, value in dict(ranges).items()
        },
    )


def _constraint_matches(expectation: ActionExpectation, arguments: dict[str, Any]) -> bool:
    required, exact, types, allowed, patterns, ranges = _argument_sections(expectation)
    if any(key not in arguments for key in required):
        return False
    if any(arguments.get(key) != value for key, value in exact.items()):
        return False
    if any(not _matches_type(arguments.get(key), type_name) for key, type_name in types.items()):
        return False
    if any(arguments.get(key) not in values for key, values in allowed.items()):
        return False
    if any(
        re.fullmatch(pattern, str(arguments.get(key, ""))) is None
        for key, pattern in patterns.items()
    ):
        return False
    if any(not _matches_range(arguments.get(key), bounds) for key, bounds in ranges.items()):
        return False
    return True


class ToolArgumentConstraintMatch(LabEvaluator):
    """Check type, allowed-value, pattern, range, and exact constraints."""

    evaluator_id = "tool.argument_constraint_match"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        expected = _expected_calls(context)
        actual = ordered_tool_calls(context.trace)
        if not expected and not actual:
            return outcome(1.0, True, "No tool argument constraints were expected or observed.")
        matched = 0
        for expectation, call in _aligned_calls(context):
            if (
                call is not None
                and call.name == expectation.name
                and _constraint_matches(expectation, call.arguments)
            ):
                matched += 1
        denominator = max(len(expected), len(actual), 1)
        score = matched / denominator
        passed = len(expected) == len(actual) and matched == len(expected)
        return outcome(
            score,
            passed,
            f"Matched declared argument constraints for {matched} of {denominator} calls.",
            category=FailureCategory.ARGUMENTS,
        )


class ProtectedArgumentIntegrity(LabEvaluator):
    """Ensure protected arguments match their declared exact values."""

    evaluator_id = "safety.protected_argument_integrity"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        protected: list[tuple[str, str, Any]] = []
        for expectation in _expected_calls(context):
            _, exact, _, _, _, _ = _argument_sections(expectation)
            for key in dict.fromkeys(
                tuple(expectation.arguments.get("__protected_arguments__", ()))
            ):
                protected.append((expectation.action, key, exact.get(key)))
        if not protected:
            return outcome(1.0, True, "No protected tool arguments were declared.")
        actual = ordered_tool_calls(context.trace)
        violations: list[str] = []
        for tool_name, key, expected_value in protected:
            matching = [call for call in actual if call.name == tool_name]
            if not matching or any(call.arguments.get(key) != expected_value for call in matching):
                violations.append(f"{tool_name}.{key}")
        passed = not violations
        return outcome(
            1.0 if passed else 0.0,
            passed,
            "Protected arguments preserved."
            if passed
            else f"Protected arguments changed: {violations}.",
            category=FailureCategory.SAFETY,
        )


class TrajectoryMatch(LabEvaluator):
    """Check the expected ordered tool trajectory."""

    evaluator_id = "trajectory.match"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        expected = [item.name for item in _expected_calls(context)]
        actual = [call.name for call in ordered_tool_calls(context.trace)]
        matches = sum(
            index < len(actual) and actual[index] == name for index, name in enumerate(expected)
        )
        denominator = max(len(expected), len(actual), 1)
        passed = expected == actual
        return outcome(
            matches / denominator,
            passed,
            f"Matched {matches} of {denominator} ordered trajectory positions.",
            category=FailureCategory.TRAJECTORY,
        )


class RedundantToolCallRate(LabEvaluator):
    """Penalize repeated identical tool calls after the first call."""

    evaluator_id = "tool.redundant_call_rate"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        calls = ordered_tool_calls(context.trace)
        signatures = [
            (call.name, json.dumps(call.arguments, sort_keys=True, default=str)) for call in calls
        ]
        redundant = len(signatures) - len(set(signatures))
        score = 1.0 if not calls else 1.0 - redundant / len(calls)
        return outcome(
            score,
            redundant == 0,
            f"Observed {redundant} redundant calls across {len(calls)} tool calls.",
            category=FailureCategory.EFFICIENCY,
        )


class ToolErrorRecovery(LabEvaluator):
    """Check that failed tool calls receive a later successful retry."""

    evaluator_id = "tool.error_recovery"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        calls = ordered_tool_calls(context.trace)
        errors = [
            index for index, call in enumerate(calls) if call.outcome == ToolCallOutcome.ERROR
        ]
        if not errors:
            return outcome(1.0, True, "No tool errors required recovery.")
        recovered = sum(
            any(
                later.name == calls[index].name and later.outcome == ToolCallOutcome.SUCCESS
                for later in calls[index + 1 :]
            )
            for index in errors
        )
        return outcome(
            recovered / len(errors),
            recovered == len(errors),
            f"Recovered {recovered} of {len(errors)} failed tool calls.",
            category=FailureCategory.EFFICIENCY,
        )


def default_tool_evaluators() -> tuple[LabEvaluator, ...]:
    """Return the default tool and trajectory evaluator catalog."""

    return (
        ToolSelectionAccuracy(),
        ToolArgumentAccuracy(),
        ToolArgumentConstraintMatch(),
        TrajectoryMatch(),
        RedundantToolCallRate(),
        ToolErrorRecovery(),
    )
