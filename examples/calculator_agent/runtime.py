"""Deterministic calculator runtime used by the enterprise example."""

from __future__ import annotations

import ast
from decimal import Decimal

from enterprise_agent_improvement_lab.contracts.candidates import EnterpriseAgentCandidate
from enterprise_agent_improvement_lab.contracts.cases import EnterpriseEvaluationCase
from enterprise_agent_improvement_lab.contracts.traces import (
    ExecutionEventRecord,
    ExecutionTrace,
    MessageEvent,
    ToolCallEvent,
    ToolCallOutcome,
    TriggerInfo,
)
from enterprise_agent_improvement_lab.environment import EvaluationEnvironment


class CalculatorRuntime:
    """Answer arithmetic cases with a deterministic calculator tool."""

    name = "calculator-agent"
    version = "1.0.0"

    async def execute(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        environment: EvaluationEnvironment,
    ) -> ExecutionTrace:
        del environment
        if not isinstance(case.input, dict) or "expression" not in case.input:
            raise ValueError("Calculator cases need an expression input")
        expression = str(case.input["expression"])
        timestamp = case.provenance.collected_at
        if timestamp is None:
            raise ValueError("Calculator cases need a collected_at timestamp")
        mode = candidate.metadata.get("calculator_mode")
        uses_tool = mode == "tool" or (mode is None and "calculator" in candidate.tools)
        result = _calculate(expression)
        events: list[ExecutionEventRecord] = [
            MessageEvent(
                event_id=f"{case.case_id}-input",
                sequence=0,
                timestamp=timestamp,
                message_id=f"{case.case_id}-input",
                role="user",
                message_summary="Arithmetic expression received.",
            )
        ]
        if uses_tool:
            events.append(
                ToolCallEvent(
                    event_id=f"{case.case_id}-calculator",
                    sequence=1,
                    timestamp=timestamp,
                    call_id=f"{case.case_id}-calculator",
                    name="calculator",
                    arguments={"expression": expression},
                    outcome=ToolCallOutcome.SUCCESS,
                    result_summary=f"Calculated result: {result}",
                )
            )
        events.append(
            MessageEvent(
                event_id=f"{case.case_id}-output",
                sequence=len(events),
                timestamp=timestamp,
                message_id=f"{case.case_id}-output",
                role="assistant",
                message_summary=f"Result: {result}",
            )
        )
        return ExecutionTrace(
            execution_id=f"{candidate.candidate_id}:{case.case_id}",
            agent_id=candidate.agent_id,
            agent_version=candidate.agent_version or candidate.version,
            candidate_id=candidate.candidate_id,
            case_id=case.case_id,
            session_id=f"calculator-demo:{candidate.candidate_id}",
            trigger=case.trigger or TriggerInfo(kind="conversation", source="calculator-example"),
            started_at=timestamp,
            ended_at=timestamp,
            events=tuple(events),
            metadata={"node": "calculator.answer", "runtime_component": "calculator"},
        )


def _calculate(expression: str) -> str:
    """Calculate a small arithmetic expression without dynamic evaluation."""

    tree = ast.parse(expression, mode="eval")
    value = _evaluate_node(tree.body)
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def _evaluate_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div) and right != 0:
            return left / right
    raise ValueError("Only safe numeric arithmetic is supported")


__all__ = ["CalculatorRuntime"]
