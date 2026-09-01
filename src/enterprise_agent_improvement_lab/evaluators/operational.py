"""Deterministic operational-budget evaluators."""

from __future__ import annotations

from enterprise_agent_improvement_lab.contracts.failures import FailureCategory
from enterprise_agent_improvement_lab.contracts.traces import ToolCallOutcome
from enterprise_agent_improvement_lab.evaluators.base import (
    EvaluationContext,
    EvaluationOutcome,
    LabEvaluator,
    metadata_value,
    ordered_tool_calls,
    outcome,
    trace_latency_ms,
    trace_total_cost,
    trace_total_tokens,
)


class LatencyBudget(LabEvaluator):
    """Check trace latency against a case budget in milliseconds."""

    evaluator_id = "operational.latency_budget"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        budget = metadata_value(context.case, "latency_budget_ms")
        if not isinstance(budget, (int, float)) or isinstance(budget, bool):
            return outcome(1.0, True, "No latency budget was declared.")
        observed = trace_latency_ms(context.trace)
        score = min(1.0, float(budget) / max(observed, 1))
        passed = observed <= budget
        return outcome(
            score,
            passed,
            f"Observed latency {observed} ms against a {budget} ms budget.",
            category=FailureCategory.EFFICIENCY,
        )


class TokenBudget(LabEvaluator):
    """Check total recorded tokens against a case budget."""

    evaluator_id = "operational.token_budget"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        budget = metadata_value(context.case, "token_budget")
        if not isinstance(budget, (int, float)) or isinstance(budget, bool):
            return outcome(1.0, True, "No token budget was declared.")
        observed = trace_total_tokens(context.trace)
        score = min(1.0, float(budget) / max(observed, 1))
        passed = observed <= budget
        return outcome(
            score,
            passed,
            f"Observed {observed} tokens against a {budget} token budget.",
            category=FailureCategory.EFFICIENCY,
        )


class CostBudget(LabEvaluator):
    """Check an optional trace cost against a case budget."""

    evaluator_id = "operational.cost_budget"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        budget = metadata_value(context.case, "cost_budget")
        observed = trace_total_cost(context.trace)
        if not isinstance(budget, (int, float)) or isinstance(budget, bool):
            return outcome(1.0, True, "No cost budget was declared.")
        if observed is None:
            return outcome(
                0.0,
                False,
                "A cost budget was declared, but the trace has no estimated cost.",
                category=FailureCategory.EFFICIENCY,
            )
        score = min(1.0, float(budget) / max(observed, 1e-12))
        passed = observed <= budget
        return outcome(
            score,
            passed,
            f"Observed cost {observed:g} against a {budget:g} budget.",
            category=FailureCategory.EFFICIENCY,
        )


class LoopBoundCompliance(LabEvaluator):
    """Check turn and tool-call loop bounds."""

    evaluator_id = "operational.loop_bound_compliance"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        max_turns = metadata_value(context.case, "max_turns")
        max_tool_calls = context.case.budgets.max_tool_calls if context.case.budgets else None
        turn_count = sum(
            event.event_type.value == "message" for event in context.trace.ordered_events()
        )
        if max_turns is None:
            max_turns = metadata_value(context.case, "max_turns")
        tool_count = len(ordered_tool_calls(context.trace))
        turn_ok = not isinstance(max_turns, int) or turn_count <= max_turns
        tool_ok = not isinstance(max_tool_calls, int) or tool_count <= max_tool_calls
        passed = turn_ok and tool_ok
        return outcome(
            1.0 if passed else 0.0,
            passed,
            f"Observed {turn_count} turns and {tool_count} tool calls within declared bounds."
            if passed
            else f"Loop bounds exceeded: turns={turn_count}/{max_turns}, "
            f"tools={tool_count}/{max_tool_calls}.",
            category=FailureCategory.EFFICIENCY,
        )


class ErrorRate(LabEvaluator):
    """Check tool error rate against a case budget."""

    evaluator_id = "operational.error_rate"

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        budget = metadata_value(context.case, "error_rate_budget")
        calls = ordered_tool_calls(context.trace)
        errors = sum(call.outcome == ToolCallOutcome.ERROR for call in calls)
        rate = errors / max(len(calls), 1)
        if not isinstance(budget, (int, float)) or isinstance(budget, bool):
            return outcome(1.0 - rate, errors == 0, f"Observed tool error rate {rate:.2%}.")
        passed = rate <= budget
        score = 1.0 if passed else max(0.0, 1.0 - (rate - budget) / max(1.0 - budget, 1e-12))
        return outcome(
            score,
            passed,
            f"Observed tool error rate {rate:.2%} against a {budget:.2%} budget.",
            category=FailureCategory.EFFICIENCY,
        )


def default_operational_evaluators() -> tuple[LabEvaluator, ...]:
    """Return the default operational evaluator catalog."""

    return (
        LatencyBudget(),
        TokenBudget(),
        CostBudget(),
        LoopBoundCompliance(),
        ErrorRate(),
    )
