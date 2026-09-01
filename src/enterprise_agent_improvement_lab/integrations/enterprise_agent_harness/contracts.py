"""Typed Lab-side contracts for the Enterprise Agent Harness boundary.

These contracts contain no Harness imports. Harness objects are opaque at this
boundary and are used only by the adapter implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import AliasChoices, ConfigDict, Field

from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifact,
    CandidateArtifactReference,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.common import ContractModel, VersionString
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTrace


class HarnessComponentKind(StrEnum):
    """Registry component kinds understood by the adapter."""

    AGENT = "agent"
    TOOL = "tool"
    CAPABILITY = "capability"
    POLICY = "policy"
    APPROVAL_POLICY = "approval_policy"
    RUNTIME_PROFILE = "runtime_profile"
    PROVIDER = "provider"


class HarnessRegistryReference(ContractModel):
    """Exact identity of one Harness registry component."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    component_kind: HarnessComponentKind = Field(
        validation_alias=AliasChoices("component_kind", "kind", "type")
    )
    component_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("component_id", "id", "reference_id"),
    )
    version: VersionString
    registry_id: str = Field(default="unknown", min_length=1)
    source_artifact_id: str | None = Field(default=None, min_length=1)

    @property
    def kind(self) -> HarnessComponentKind:
        """Return the component kind using the short compatibility name."""

        return self.component_kind

    @property
    def identity(self) -> str:
        """Return the exact component identity."""

        return f"{self.component_id}@{self.version}"


class HarnessRuntimeIdentity(ContractModel):
    """Identity captured for one Lab-to-Harness execution."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    runtime_name: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: VersionString
    candidate_id: str = Field(min_length=1)

    @property
    def agent_identity(self) -> str:
        """Return the exact agent identity."""

        return f"{self.agent_id}@{self.agent_version}"


@dataclass(frozen=True)
class HarnessCandidateDefinition:
    """A Lab candidate plus its exact Harness-compatible configuration.

    ``agent_config`` is an opaque object because its concrete type belongs to
    Enterprise Agent Harness. The adapter creates it through the Harness
    package's typed ``AgentConfig`` contract.
    """

    candidate: EnterpriseAgentCandidate
    agent_config: object
    runtime_identity: HarnessRuntimeIdentity
    artifact_references: tuple[CandidateArtifactReference, ...]
    artifacts: tuple[CandidateArtifact, ...] = ()
    registry_references: tuple[HarnessRegistryReference, ...] = ()

    @property
    def candidate_id(self) -> str:
        """Return the Lab candidate identity."""

        return self.candidate.candidate_id

    @property
    def config(self) -> object:
        """Return the Harness ``AgentConfig`` object."""

        return self.agent_config


class HarnessBuiltAgent(Protocol):
    """Small public protocol implemented by a Harness built agent."""

    def execute(self, principal: object, input_text: str, **kwargs: Any) -> object:
        """Execute through Harness-owned governance and runtime controls."""

    def trace_for(self, execution_id: str) -> object:
        """Return the Harness exported trace for one execution."""


@dataclass(frozen=True)
class HarnessBuiltCandidate:
    """A candidate definition paired with its factory-built Harness agent."""

    definition: HarnessCandidateDefinition
    built_agent: HarnessBuiltAgent

    @property
    def candidate_id(self) -> str:
        """Return the Lab candidate identity."""

        return self.definition.candidate_id

    @property
    def agent(self) -> HarnessBuiltAgent:
        """Return the opaque Harness built agent."""

        return self.built_agent


@dataclass(frozen=True)
class HarnessExecutionResult:
    """One Harness result with its translated Lab execution trace."""

    candidate_id: str
    runtime_identity: HarnessRuntimeIdentity
    outcome: object
    harness_trace: object
    execution_trace: ExecutionTrace

    @property
    def trace(self) -> ExecutionTrace:
        """Return the Lab execution trace."""

        return self.execution_trace

    @property
    def lab_trace(self) -> ExecutionTrace:
        """Return the Lab execution trace using an explicit name."""

        return self.execution_trace

    @property
    def execution_id(self) -> str:
        """Return the stable execution identity."""

        return self.execution_trace.execution_id


__all__ = [
    "HarnessBuiltAgent",
    "HarnessBuiltCandidate",
    "HarnessCandidateDefinition",
    "HarnessComponentKind",
    "HarnessExecutionResult",
    "HarnessRegistryReference",
    "HarnessRuntimeIdentity",
]
