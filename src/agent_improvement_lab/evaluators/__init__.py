"""Deterministic evaluator catalog for Lab traces."""

from agent_improvement_lab.evaluators.base import (
    EvaluationContext,
    EvaluationOutcome,
    LabEvaluator,
    validate_evaluator_ids,
)
from agent_improvement_lab.evaluators.operational import (
    CostBudget,
    ErrorRate,
    LatencyBudget,
    LoopBoundCompliance,
    TokenBudget,
    default_operational_evaluators,
)
from agent_improvement_lab.evaluators.safety import (
    AuthorizationBoundaryPreserved,
    InstructionOverrideResistance,
    ProtectedArgumentIntegrity,
    RequiredVerificationExecuted,
    default_safety_evaluators,
)
from agent_improvement_lab.evaluators.sessions import (
    ClarificationQuality,
    CrossTurnNumericalConsistency,
    RepeatedQuestionRate,
    SessionContextRetention,
    SessionContradictionRate,
    SessionStyleConsistency,
    UnnecessaryClarificationRate,
    default_session_evaluators,
)
from agent_improvement_lab.evaluators.tools import (
    RedundantToolCallRate,
    ToolArgumentAccuracy,
    ToolArgumentConstraintMatch,
    ToolErrorRecovery,
    ToolSelectionAccuracy,
    TrajectoryMatch,
    default_tool_evaluators,
)


def default_evaluators() -> tuple[LabEvaluator, ...]:
    """Return the complete deterministic evaluator catalog."""

    return validate_evaluator_ids(
        (
            *default_tool_evaluators(),
            *default_session_evaluators(),
            *default_safety_evaluators(),
            *default_operational_evaluators(),
        )
    )


__all__ = [
    "AuthorizationBoundaryPreserved",
    "ClarificationQuality",
    "CostBudget",
    "CrossTurnNumericalConsistency",
    "ErrorRate",
    "EvaluationContext",
    "EvaluationOutcome",
    "InstructionOverrideResistance",
    "LabEvaluator",
    "LatencyBudget",
    "LoopBoundCompliance",
    "ProtectedArgumentIntegrity",
    "RedundantToolCallRate",
    "RepeatedQuestionRate",
    "RequiredVerificationExecuted",
    "SessionContextRetention",
    "SessionContradictionRate",
    "SessionStyleConsistency",
    "TokenBudget",
    "ToolArgumentAccuracy",
    "ToolArgumentConstraintMatch",
    "ToolErrorRecovery",
    "ToolSelectionAccuracy",
    "TrajectoryMatch",
    "UnnecessaryClarificationRate",
    "default_evaluators",
    "default_operational_evaluators",
    "default_safety_evaluators",
    "default_session_evaluators",
    "default_tool_evaluators",
    "validate_evaluator_ids",
]
