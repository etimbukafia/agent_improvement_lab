"""Provider-neutral runtime boundaries for enterprise evaluation."""

from __future__ import annotations

from typing import Protocol

from enterprise_agent_improvement_lab.contracts.candidates import EnterpriseAgentCandidate
from enterprise_agent_improvement_lab.contracts.cases import EnterpriseEvaluationCase
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTrace
from enterprise_agent_improvement_lab.environment import EvaluationEnvironment


class EnterpriseRuntime(Protocol):
    """Execution boundary owned by an application or runtime integration."""

    name: str
    version: str

    async def execute(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        environment: EvaluationEnvironment,
    ) -> ExecutionTrace:
        """Execute one case in the supplied disposable environment."""


class EnterpriseRuntimeLifecycleHooks(Protocol):
    """Optional hooks around one enterprise runtime execution."""

    async def before_case(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
    ) -> None:
        """Prepare resources before execution."""

    async def after_case(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        trace: ExecutionTrace | None,
        error: str | None,
    ) -> None:
        """Release resources after execution and evaluation."""


class NoopEnterpriseRuntimeLifecycle:
    """Default lifecycle with no external side effects."""

    async def before_case(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
    ) -> None:
        return None

    async def after_case(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        trace: ExecutionTrace | None,
        error: str | None,
    ) -> None:
        return None


def runtime_identity(runtime: EnterpriseRuntime) -> tuple[str, str]:
    """Return the validated runtime identity used in a run manifest."""

    name = getattr(runtime, "name", None)
    version = getattr(runtime, "version", None)
    if not isinstance(name, str) or not name.strip():
        raise TypeError("EnterpriseRuntime.name must be a non-empty string")
    if not isinstance(version, str) or not version.strip():
        raise TypeError("EnterpriseRuntime.version must be a non-empty string")
    return name, version


__all__ = [
    "EnterpriseRuntime",
    "EnterpriseRuntimeLifecycleHooks",
    "NoopEnterpriseRuntimeLifecycle",
    "runtime_identity",
]
