"""Deterministic calculator runtime used by the Phase 6 example."""

from __future__ import annotations

import ast
from decimal import Decimal

from agent_improvement_lab.contracts.candidates import AgentCandidate
from agent_improvement_lab.contracts.cases import EvaluationCaseRef
from agent_improvement_lab.contracts.traces import (
    AgentTrace,
    ObservedToolCall,
    ObservedTurn,
    ToolCallOutcome,
)


class CalculatorRuntime:
    """Answer arithmetic cases with a deterministic calculator tool."""

    name = "calculator-agent"
    version = "1.0.0"

    async def execute(self, case: EvaluationCaseRef, candidate: AgentCandidate) -> AgentTrace:
        expression = str(case.input["expression"])
        timestamp = case.provenance.collected_at
        if timestamp is None:
            raise ValueError("Calculator cases need a collected_at timestamp")
        uses_tool = candidate.metadata.get("calculator_mode") == "tool"
        result = _calculate(expression)
        tool_calls: tuple[ObservedToolCall, ...]
        if uses_tool:
            call = ObservedToolCall(
                call_id=f"{case.case_id}-calculator",
                sequence=0,
                name="calculator",
                arguments={"expression": expression},
                outcome=ToolCallOutcome.SUCCESS,
                result_summary=result,
                started_at=timestamp,
                ended_at=timestamp,
            )
            output = f"Result: {result}"
            tool_calls = (call,)
        else:
            output = f"Result: {result}"
            tool_calls = ()
        turn = ObservedTurn(
            turn_id=f"{case.case_id}-turn-0",
            sequence=0,
            input_text=f"Calculate {expression}.",
            output_text=output,
            tool_calls=tool_calls,
            started_at=timestamp,
            ended_at=timestamp,
        )
        return AgentTrace(
            trace_id=f"{candidate.candidate_id}:{case.case_id}",
            case_id=case.case_id,
            candidate_id=candidate.candidate_id,
            session_id=f"calculator-demo:{candidate.candidate_id}",
            started_at=timestamp,
            ended_at=timestamp,
            turns=(turn,),
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
