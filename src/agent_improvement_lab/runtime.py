"""Protocols for connecting agent runtimes to the Lab."""

from __future__ import annotations

from typing import Protocol

from agent_improvement_lab.contracts.candidates import AgentCandidate
from agent_improvement_lab.contracts.cases import EvaluationCaseRef
from agent_improvement_lab.contracts.traces import AgentTrace


class AgentRuntime(Protocol):
    """Public boundary for an agent under evaluation."""

    name: str
    version: str

    async def execute(self, case: EvaluationCaseRef, candidate: AgentCandidate) -> AgentTrace:
        """Execute one case and return an explicit Lab trace."""


class RuntimeLifecycleHooks(Protocol):
    """Hooks called around each adapter execution."""

    async def before_case(self, case: EvaluationCaseRef, candidate: AgentCandidate) -> None:
        """Prepare resources before the runtime executes a case."""

    async def after_case(
        self,
        case: EvaluationCaseRef,
        candidate: AgentCandidate,
        trace: AgentTrace | None,
        error: str | None,
    ) -> None:
        """Release resources after execution and evaluation."""


class NoopRuntimeLifecycle:
    """Default lifecycle with no external side effects."""

    async def before_case(self, case: EvaluationCaseRef, candidate: AgentCandidate) -> None:
        return None

    async def after_case(
        self,
        case: EvaluationCaseRef,
        candidate: AgentCandidate,
        trace: AgentTrace | None,
        error: str | None,
    ) -> None:
        return None


def runtime_identity(runtime: AgentRuntime) -> tuple[str, str]:
    """Return and validate the runtime identity used in a run manifest."""

    name = getattr(runtime, "name", None)
    version = getattr(runtime, "version", None)
    if not isinstance(name, str) or not name:
        raise TypeError("AgentRuntime.name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise TypeError("AgentRuntime.version must be a non-empty string")
    return name, version
