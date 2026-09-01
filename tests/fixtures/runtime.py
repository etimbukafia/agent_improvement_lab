"""Deterministic runtime fixture for Lab boundary tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from enterprise_agent_improvement_lab.contracts.candidates import EnterpriseAgentCandidate
from enterprise_agent_improvement_lab.contracts.cases import EnterpriseEvaluationCase
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTrace, MessageEvent
from enterprise_agent_improvement_lab.environment import EvaluationEnvironment

UTC = timezone.utc


@dataclass
class DeterministicFixtureRuntime:
    """Return fixed traces for runtime boundary tests."""

    traces: Mapping[str, ExecutionTrace] = field(default_factory=dict)
    name: str = "deterministic-fixture-runtime"
    version: str = "1.0.0"

    async def execute(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        environment: EvaluationEnvironment,
    ) -> ExecutionTrace:
        fixture = self.traces.get(case.case_id)
        if fixture is not None:
            return fixture.model_copy(
                deep=True,
                update={
                    "execution_id": f"fixture-{candidate.candidate_id}:{case.case_id}",
                    "case_id": case.case_id,
                    "candidate_id": candidate.candidate_id,
                    "agent_id": candidate.agent_id,
                },
            )

        timestamp = case.provenance.collected_at or datetime(2000, 1, 1, tzinfo=UTC)
        output = _expected_output(case)
        event = MessageEvent(
            event_id=f"{case.case_id}-message-0",
            sequence=0,
            timestamp=timestamp,
            message_id=f"{case.case_id}-message-0",
            role="assistant",
            message_summary=output,
        )
        return ExecutionTrace(
            execution_id=f"fixture-{candidate.candidate_id}:{case.case_id}",
            agent_id=candidate.agent_id,
            agent_version=candidate.agent_version or candidate.version,
            candidate_id=candidate.candidate_id,
            case_id=case.case_id,
            started_at=timestamp,
            ended_at=timestamp,
            events=(event,),
            metadata={"runtime": self.name, "deterministic": True},
        )


def _expected_output(case: EnterpriseEvaluationCase) -> str:
    if case.expected_outputs:
        value = case.expected_outputs[0].expected_value
        return str(value)
    if isinstance(case.input, str):
        return case.input
    return "ok"
