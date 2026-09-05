"""Behavior tests for the optional Enterprise Agent Harness boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from enterprise_agent_improvement_lab.contracts.candidates import (
    ArtifactProvenance,
    CandidateArtifact,
    CandidateArtifactKind,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.environments import EnvironmentSnapshot
from enterprise_agent_improvement_lab.contracts.traces import (
    ApprovalDecisionEvent,
    DelegationEvent,
    ExecutionEventStatus,
    ExecutionTrace,
    StateMutationEvent,
    ToolCallEvent,
    WorkflowTransitionEvent,
)
from enterprise_agent_improvement_lab.integrations.enterprise_agent_harness import (
    EnterpriseAgentHarnessAdapter,
    HarnessCandidateDefinition,
    HarnessRegistryReference,
    harness_agent_definition_to_candidate_artifact,
    harness_approval_policy_to_candidate_artifact,
    harness_policy_definition_to_candidate_artifact,
    harness_run_trace_to_execution_trace,
    harness_skill_definition_to_candidate_artifact,
    harness_tool_definition_to_candidate_artifact,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _HarnessEvent:
    event_id: str
    execution_id: str
    sequence: int
    stage: str
    event_type: str
    occurred_at: datetime
    metadata: dict[str, str]


@dataclass(frozen=True)
class _ToolExecution:
    execution_id: str
    tool_id: str
    tool_version: str
    status: str
    attempts: int = 1
    retry_count: int = 0
    latency_ms: float = 0.0
    timeout_seconds: float | None = None
    idempotency_key_digest: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class _ToolCall:
    call_id: str
    tool_id: str
    tool_version: str
    arguments: dict[str, Any]
    evidence_ids: list[str]


@dataclass(frozen=True)
class _Outcome:
    execution_id: str
    evidence_ids: list[str]
    tool_calls: list[_ToolCall]


@dataclass(frozen=True)
class _Metrics:
    total_input_tokens: int = 8
    total_output_tokens: int = 4
    total_cost: float = 0.03


@dataclass(frozen=True)
class _RunTrace:
    trace_id: str
    execution_id: str
    agent_id: str
    agent_version: str
    session_id: str
    events: list[_HarnessEvent]
    generated_at: datetime
    schema_version: str = "agent-run-trace.v1"
    correlation_id: str = "correlation-1"
    trigger_id: str | None = "trigger-1"
    event_id: str | None = "source-event-1"
    provider_calls: list[object] = field(default_factory=list)
    policy_decisions: list[object] = field(default_factory=list)
    tool_executions: list[_ToolExecution] = field(default_factory=list)
    metrics: _Metrics = field(default_factory=_Metrics)


class _FakeAgentConfig:
    def __init__(self, **values: Any) -> None:
        self.values = values

    @classmethod
    def model_validate(cls, values: dict[str, Any]) -> "_FakeAgentConfig":
        return cls(**values)


class _FakeComponentType(str, Enum):
    AGENT = "agent"
    PROMPT = "prompt"
    SKILL = "skill"
    TOOL = "tool"
    POLICY = "policy"


class _FakeLifecycle(str, Enum):
    ACTIVE = "active"
    DRAFT = "draft"


class _FakeRisk(str, Enum):
    LOW = "low"


class _FakeComponentReference:
    def __init__(self, *, component_type: _FakeComponentType, component_id: str, version: str):
        self.component_type = component_type
        self.component_id = component_id
        self.version = version


class _FakeDefinition:
    def __init__(self, **values: Any) -> None:
        self.values = values
        for key, value in values.items():
            setattr(self, key, value)

    @classmethod
    def model_validate(cls, values: dict[str, Any]) -> "_FakeDefinition":
        return cls(**values)


class _FakeHarnessModule:
    __version__ = "0.1.0"
    AgentConfig = _FakeAgentConfig
    ComponentType = _FakeComponentType
    ComponentReference = _FakeComponentReference
    PromptDefinition = _FakeDefinition
    SkillDefinition = _FakeDefinition
    AgentLifecycleStatus = _FakeLifecycle
    RiskLevel = _FakeRisk


@dataclass
class _FakeBuiltAgent:
    trace: _RunTrace
    outcome: _Outcome
    manifest: object | None = None

    def execute(self, principal: object, input_text: str, **kwargs: Any) -> _Outcome:
        del principal, input_text, kwargs
        return self.outcome

    def trace_for(self, execution_id: str) -> _RunTrace:
        assert execution_id == self.trace.execution_id
        return self.trace


class _FakeFactory:
    def __init__(self, built: _FakeBuiltAgent) -> None:
        self.built = built
        self.config: _FakeAgentConfig | None = None

    def build(self, config: _FakeAgentConfig, **kwargs: Any) -> _FakeBuiltAgent:
        self.config = config
        assert not kwargs
        return self.built


class _FakeComponentRegistry:
    def __init__(self, records: list[object], revision: int) -> None:
        self.records = records
        self.revision = revision

    def descriptors(self, **kwargs: Any) -> list[object]:
        del kwargs
        return list(self.records)

    def list(self, **kwargs: Any) -> list[object]:
        del kwargs
        return list(self.records)


class _FakeRegistry:
    revision = 7

    def __init__(self, *, tool_description: str = "Read orders.") -> None:
        self.tools = _FakeComponentRegistry(
            [
                {
                    "tool_id": "orders.read",
                    "version": "1.0.0",
                    "description": tool_description,
                }
            ],
            revision=4,
        )
        self.prompts = _FakeComponentRegistry(
            [{"prompt_id": "orders-prompt", "version": "1.0.0"}],
            revision=2,
        )
        self.skills = _FakeComponentRegistry(
            [{"skill_id": "order-review", "version": "1.0.0"}],
            revision=3,
        )
        self.policies = [
            {
                "policy_id": "orders-policy",
                "version": "1.0.0",
                "description": "Allow reads.",
            }
        ]

    def get(self, agent_id: str, version: str) -> dict[str, Any]:
        assert (agent_id, version) in {("orders-agent", "1.0.0")}
        return {
            "identity": {"agent_id": agent_id, "version": version},
            "goal": "Review orders.",
            "provider_profile": {
                "provider_id": "deterministic",
                "version": "1.0.0",
                "model": "orders-model",
                "options": {"temperature": 0.0},
            },
        }


def _artifact(
    artifact_id: str,
    kind: CandidateArtifactKind,
    content: str,
    registry_reference: str | None = None,
) -> CandidateArtifact:
    return CandidateArtifact(
        artifact_id=artifact_id,
        name=artifact_id,
        version="1.0.0",
        kind=kind,
        content=content,
        provenance=ArtifactProvenance(
            source="harness-registry",
            source_ref=registry_reference or artifact_id,
            created_by="test",
            created_at=NOW,
        ),
        registry_reference=registry_reference,
        created_at=NOW,
    )


def _candidate() -> tuple[EnterpriseAgentCandidate, tuple[CandidateArtifact, ...]]:
    artifacts = (
        _artifact(
            "agent-definition-1",
            CandidateArtifactKind.AGENT_DEFINITION,
            json.dumps(
                {
                    "goal": "Review orders.",
                    "provider_profile": {
                        "provider_id": "deterministic",
                        "version": "1.0.0",
                        "model": "test-model",
                    },
                },
                sort_keys=True,
            ),
            "agent:orders-agent@1.0.0",
        ),
        _artifact(
            "tool-definition-1",
            CandidateArtifactKind.TOOL_BINDING,
            '{"tool_id":"orders.read","version":"1.0.0"}',
            "tool:orders.read@1.0.0",
        ),
        _artifact(
            "prompt-definition-1",
            CandidateArtifactKind.SYSTEM_PROMPT,
            '{"purpose":"Review orders.","instructions":"Review orders safely."}',
            "prompt:orders-prompt@1.0.0",
        ),
        _artifact(
            "skill-definition-1",
            CandidateArtifactKind.SKILL_CONFIGURATION,
            '{"skill_id":"order-review","version":"1.0.0","description":"Review orders."}',
            "skill:order-review@1.0.0",
        ),
        _artifact(
            "policy-definition-1",
            CandidateArtifactKind.POLICY,
            '{"policy_id":"orders-policy","version":"1.0.0"}',
            "policy:orders-policy@1.0.0",
        ),
    )
    candidate = EnterpriseAgentCandidate(
        candidate_id="candidate-orders-1",
        agent_id="orders-agent",
        version="1.0.0",
        artifacts=tuple(item.to_reference() for item in artifacts),
        prompt_ref=artifacts[2].to_component_reference(),
        tool_refs=(artifacts[1].to_component_reference(),),
        skill_refs=(artifacts[3].to_component_reference(),),
        policy_refs=(artifacts[4].to_component_reference(),),
    )
    return candidate, artifacts


def _trace(
    *events: _HarnessEvent,
    tool_executions: list[_ToolExecution] | None = None,
    policy_decisions: list[object] | None = None,
) -> _RunTrace:
    return _RunTrace(
        trace_id="harness-trace-1",
        execution_id="execution-orders-1",
        agent_id="orders-agent",
        agent_version="1.0.0",
        session_id="session-1",
        events=list(events),
        generated_at=NOW + timedelta(seconds=5),
        tool_executions=tool_executions or [],
        policy_decisions=policy_decisions or [],
    )


def _event(
    event_id: str,
    sequence: int,
    event_type: str,
    *,
    stage: str = "runtime",
    second: int = 0,
    **metadata: str,
) -> _HarnessEvent:
    return _HarnessEvent(
        event_id=event_id,
        execution_id="execution-orders-1",
        sequence=sequence,
        stage=stage,
        event_type=event_type,
        occurred_at=NOW + timedelta(seconds=second),
        metadata=metadata,
    )


def test_lab_candidate_becomes_harness_compatible_definition() -> None:
    candidate, artifacts = _candidate()
    adapter = EnterpriseAgentHarnessAdapter(
        harness_module=_FakeHarnessModule(),
        runtime_version="0.1.0-test",
    )

    definition = adapter.to_harness_candidate_definition(
        candidate,
        artifacts=artifacts,
    )

    assert isinstance(definition, HarnessCandidateDefinition)
    assert definition.candidate_id == candidate.candidate_id
    assert definition.runtime_identity.agent_identity == "orders-agent@1.0.0"
    assert definition.agent_config.values["identity"] == {
        "agent_id": "orders-agent",
        "version": "1.0.0",
    }
    assert definition.agent_config.values["prompt_ref"].component_id == "orders-prompt"
    assert [item.component_id for item in definition.agent_config.values["skill_refs"]] == [
        "order-review"
    ]
    assert [item.component_id for item in definition.agent_config.values["tool_refs"]] == [
        "orders.read"
    ]
    assert [item.component_id for item in definition.agent_config.values["policy_refs"]] == [
        "orders-policy"
    ]


def test_harness_snapshot_collector_maps_public_registry_state() -> None:
    candidate, artifacts = _candidate()
    adapter = EnterpriseAgentHarnessAdapter(
        harness_module=_FakeHarnessModule(),
        runtime_version="0.1.0-test",
    )
    registry = _FakeRegistry()

    first = adapter.collect_environment_snapshot(
        candidate,
        registry=registry,
        artifacts=artifacts,
        feature_flags={"safe_mode": True},
        tenant_profile="tenant-test",
        fixture_version="fixture-1",
        external_service_stub_versions={"orders": "2.0.0"},
        environment_name="test",
        clock_mode="fixed",
        seed=7,
    )
    second = adapter.collect_environment_snapshot(
        candidate,
        registry=_FakeRegistry(),
        artifacts=artifacts,
        feature_flags={"safe_mode": True},
        tenant_profile="tenant-test",
        fixture_version="fixture-1",
        external_service_stub_versions={"orders": "2.0.0"},
        environment_name="test",
        clock_mode="fixed",
        seed=7,
    )

    assert isinstance(first, EnvironmentSnapshot)
    assert first.identity == second.identity
    assert first.agent_registry_version == "7"
    assert first.tool_registry_version == "4"
    assert first.prompt_registry_version == "2"
    assert first.skill_registry_version == "3"
    assert first.provider == "deterministic"
    assert first.model == "orders-model"
    assert first.tool_hashes[0].identity == "orders.read@1.0.0"
    assert first.policy_hashes[0].identity == "orders-policy@1.0.0"

    changed = adapter.collect_environment_snapshot(
        candidate,
        registry=_FakeRegistry(tool_description="Changed description."),
        artifacts=artifacts,
        fixture_version="fixture-1",
    )
    assert changed.identity != first.identity


def test_harness_definitions_translate_to_immutable_lab_artifacts() -> None:
    agent = harness_agent_definition_to_candidate_artifact(
        {
            "identity": {"agent_id": "orders-agent", "version": "1.0.0"},
            "goal": "Review orders.",
        },
        created_at=NOW,
    )
    tool = harness_tool_definition_to_candidate_artifact(
        {"tool_id": "orders.read", "version": "1.0.0", "description": "Read orders."},
        created_at=NOW,
        kind=CandidateArtifactKind.TOOL_BINDING,
    )
    skill = harness_skill_definition_to_candidate_artifact(
        {
            "skill_id": "order-review",
            "version": "1.0.0",
            "description": "Review orders.",
            "supported_operations": ["read"],
        },
        created_at=NOW,
    )
    policy = harness_policy_definition_to_candidate_artifact(
        {"policy_id": "orders-policy", "version": "1.0.0", "description": "Allow reads."},
        created_at=NOW,
    )
    approval = harness_approval_policy_to_candidate_artifact(
        {
            "policy_id": "orders-approval",
            "version": "1.0.0",
            "description": "Require approval for writes.",
        },
        created_at=NOW,
    )

    assert [item.kind for item in (agent, tool, skill, policy, approval)] == [
        CandidateArtifactKind.AGENT_DEFINITION,
        CandidateArtifactKind.TOOL_BINDING,
        CandidateArtifactKind.SKILL_CONFIGURATION,
        CandidateArtifactKind.POLICY,
        CandidateArtifactKind.APPROVAL_POLICY,
    ]
    assert agent.registry_reference == "agent:orders-agent@1.0.0"
    assert tool.registry_reference == "tool:orders.read@1.0.0"
    assert all(item.provenance.source == "enterprise-agent-harness" for item in (agent, tool))
    assert all(item.checksum for item in (agent, tool, skill, policy, approval))


def test_harness_agent_executes_through_adapter_and_preserves_identity() -> None:
    candidate, artifacts = _candidate()
    events = (
        _event("event-1", 1, "execution_started", second=0, status="running"),
        _event(
            "event-2",
            2,
            "tool_result_recorded",
            stage="tool",
            second=1,
            tool_id="orders.read",
            tool_version="1.0.0",
            result_status="succeeded",
            argument_digest="digest-1",
        ),
        _event("event-3", 3, "state_transitioned", stage="state", second=2, version="2"),
    )
    tool_call = _ToolCall(
        call_id="call-1",
        tool_id="orders.read",
        tool_version="1.0.0",
        arguments={"order_id": "sensitive-order-id"},
        evidence_ids=["evidence-order-1"],
    )
    trace = _trace(
        *events,
        tool_executions=[
            _ToolExecution(
                execution_id="execution-orders-1",
                tool_id="orders.read",
                tool_version="1.0.0",
                status="succeeded",
                latency_ms=125,
                idempotency_key_digest="idempotency-digest-1",
            )
        ],
    )
    outcome = _Outcome(
        execution_id="execution-orders-1",
        evidence_ids=["evidence-outcome-1"],
        tool_calls=[tool_call],
    )
    factory = _FakeFactory(_FakeBuiltAgent(trace=trace, outcome=outcome))
    adapter = EnterpriseAgentHarnessAdapter(harness_module=_FakeHarnessModule())
    built = adapter.build_candidate(candidate, factory, artifacts=artifacts)

    result = adapter.execute(built, object(), "review this order", case_id="case-1")

    assert result.candidate_id == candidate.candidate_id
    assert result.execution_id == trace.execution_id
    assert result.runtime_identity.agent_identity == "orders-agent@1.0.0"
    assert result.trace.agent_id == trace.agent_id
    assert [event.event_id for event in result.trace.events] == [
        "event-1",
        "event-2",
        "event-3",
    ]
    tool_event = result.trace.events[1]
    assert isinstance(tool_event, ToolCallEvent)
    assert tool_event.evidence_refs == ("evidence-order-1",)
    assert tool_event.arguments == {}
    assert tool_event.metadata["harness_idempotency_key_digest"] == "idempotency-digest-1"
    assert isinstance(result.trace.events[2], StateMutationEvent)
    assert result.trace.evidence_refs == ("evidence-outcome-1", "evidence-order-1")


def test_trace_translation_preserves_event_order_and_duplicate_order_fails() -> None:
    trace = _trace(
        _event("event-2", 2, "workflow_step_completed", stage="workflow", second=2),
        _event("event-1", 1, "execution_started", second=1),
    )

    converted = harness_run_trace_to_execution_trace(trace, candidate_id="candidate-1")

    assert [event.event_id for event in converted.ordered_events()] == ["event-1", "event-2"]
    assert [event.sequence for event in converted.events] == [1, 2]
    with pytest.raises(ValidationError, match="Event sequence values must be unique"):
        harness_run_trace_to_execution_trace(
            _trace(
                _event("event-1", 1, "execution_started"),
                _event("event-duplicate", 1, "workflow_step_completed"),
            )
        )


def test_approval_and_delegation_events_survive_translation() -> None:
    converted = harness_run_trace_to_execution_trace(
        _trace(
            _event(
                "approval-request",
                1,
                "approval_requested",
                stage="approval",
                request_id="request-1",
                action_digest="action-digest-1",
                tool_id="orders.write",
            ),
            _event(
                "approval-decision",
                2,
                "approval_approved",
                stage="approval",
                request_id="request-1",
                approval_id="approval-1",
                decided_by="reviewer-1",
                decision="approved",
            ),
            _event(
                "delegation-start",
                3,
                "delegation_started",
                stage="delegation",
                delegation_id="delegation-1",
                child_agent_id="fraud-agent",
                child_execution_id="child-execution-1",
            ),
            _event(
                "delegation-done",
                4,
                "delegation_completed",
                stage="delegation",
                delegation_id="delegation-1",
                child_agent_id="fraud-agent",
                child_execution_id="child-execution-1",
            ),
        )
    )

    assert converted.events[0].evidence_refs == (
        "approval:request-1",
        "approval-action:action-digest-1",
    )
    assert isinstance(converted.events[1], ApprovalDecisionEvent)
    assert converted.events[1].approval_id == "approval-1"
    assert converted.events[1].evidence_refs == ("approval:request-1",)
    assert isinstance(converted.events[2], DelegationEvent)
    assert converted.events[2].child_execution_id == "child-execution-1"
    assert isinstance(converted.events[3], DelegationEvent)
    assert converted.events[3].status == ExecutionEventStatus.COMPLETED


def test_state_mutation_is_explicit_and_tool_errors_keep_safe_error_evidence() -> None:
    converted = harness_run_trace_to_execution_trace(
        _trace(
            _event(
                "tool-failed",
                1,
                "permission_denied",
                stage="tool",
                tool_id="orders.write",
                tool_version="1.0.0",
                result_status="failed",
                error_code="permission_denied",
            ),
            _event(
                "state-failed",
                2,
                "state_transition_failed",
                stage="state",
                resource="order-state",
                transaction_id="transaction-1",
            ),
            policy_decisions=[SimpleNamespace(decision_id="policy-decision-2")],
        ),
        outcome=_Outcome(
            execution_id="execution-orders-1",
            evidence_ids=[],
            tool_calls=[
                _ToolCall(
                    call_id="call-failed",
                    tool_id="orders.write",
                    tool_version="1.0.0",
                    arguments={"secret": "redacted"},
                    evidence_ids=["tool-error-evidence-1"],
                )
            ],
        ),
        evidence_refs=("policy-decision-1",),
    )

    tool_event = converted.events[0]
    state_event = converted.events[1]
    assert isinstance(tool_event, ToolCallEvent)
    assert tool_event.outcome.value == "error"
    assert tool_event.error_type == "permission_denied"
    assert tool_event.evidence_refs == ("tool-error-evidence-1",)
    assert isinstance(state_event, StateMutationEvent)
    assert state_event.resource == "order-state"
    assert state_event.status == ExecutionEventStatus.ERROR
    assert state_event.transaction_id == "transaction-1"
    assert "policy-decision-2" in converted.evidence_refs


def test_trace_summary_excludes_raw_sensitive_event_payloads() -> None:
    converted = harness_run_trace_to_execution_trace(
        _trace(
            _event(
                "tool-result",
                1,
                "tool_result_recorded",
                stage="tool",
                tool_id="orders.read",
                tool_version="1.0.0",
                result_status="succeeded",
                payload="raw-secret-payload",
                raw_output="raw-secret-output",
            ),
        )
    )

    summary = converted.to_summary()
    serialized = summary.model_dump_json()

    assert "raw-secret-payload" not in serialized
    assert "raw-secret-output" not in serialized
    assert "tool_result_recorded" not in serialized
    assert summary.tool_call_count == 1


def test_trace_does_not_expose_uncontracted_skill_selection_metadata() -> None:
    converted = harness_run_trace_to_execution_trace(
        _trace(
            _event(
                "workflow",
                1,
                "workflow_step_completed",
                stage="workflow",
                skill_id="order-review",
                skill_version="1.0.0",
                skill_selected="true",
                skill_selection="order-review",
            )
        )
    )

    metadata = converted.events[0].metadata
    assert metadata["skill_id"] == "order-review"
    assert metadata["skill_version"] == "1.0.0"
    assert "skill_selected" not in metadata
    assert "skill_selection" not in metadata


def test_lab_core_imports_without_loading_harness_package() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import enterprise_agent_improvement_lab; "
                "assert 'enterprise_agent_harness' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr


def test_registry_reference_contract_keeps_component_identity_exact() -> None:
    reference = HarnessRegistryReference(
        kind="tool",
        id="orders.read",
        version="1.0.0",
        registry_id="tools",
    )

    assert reference.component_kind.value == "tool"
    assert reference.identity == "orders.read@1.0.0"


def test_harness_trace_translation_returns_lab_contract() -> None:
    converted = harness_run_trace_to_execution_trace(
        _trace(_event("workflow", 1, "workflow_step_completed", stage="workflow"))
    )

    assert isinstance(converted, ExecutionTrace)
    assert isinstance(converted.events[0], WorkflowTransitionEvent)


def test_real_harness_agent_round_trip_when_package_is_available() -> None:
    harness = pytest.importorskip("enterprise_agent_harness")
    from pydantic import BaseModel, ConfigDict, Field

    class Query(BaseModel):
        model_config = ConfigDict(extra="forbid")

        query: str = Field(min_length=1)

    class Answer(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str

    tool = harness.ToolDefinition(
        tool_id="orders.read",
        version="1.0.0",
        description="Read one order.",
        input_model=Query,
        output_model=Answer,
        handler=lambda _context, arguments: Answer(value=arguments.query),
    )
    tools = harness.ToolRegistry([tool])
    prompts = harness.PromptRegistry()
    skill_registry = harness.SkillRegistry(tools=tools)
    policy = harness.PolicyDefinition(
        policy_id="orders-policy",
        version="1.0.0",
        description="Allow order reads.",
        default_effect=harness.PolicyEffect.DENY,
        rules=[
            harness.PolicyRule(
                rule_id="allow-order-read",
                effect=harness.PolicyEffect.ALLOW,
                tool_ids=["orders.read"],
            )
        ],
        lifecycle=harness.AgentLifecycleStatus.ACTIVE,
    )
    registry = harness.AgentRegistry(
        prompts=prompts,
        skills=skill_registry,
        tools=tools,
        policies=[policy],
    )
    factory = harness.AgentFactory(
        agent_registry=registry,
        providers={
            ("deterministic", "1.0.0"): harness.DeterministicProvider(
                tool_id="orders.read",
                input_tokens=2,
                output_tokens=3,
            )
        },
        trace_sink=harness.ListTraceSink(),
    )
    candidate, artifacts = _candidate()
    adapter = EnterpriseAgentHarnessAdapter()
    built = adapter.build_candidate(candidate, factory, artifacts=artifacts)
    assert built.provenance is not None
    assert built.provenance.prompt_ref == "prompt:orders-prompt@1.0.0"
    assert built.provenance.skill_refs == ("skill:order-review@1.0.0",)
    assert built.provenance.tool_refs == ("tool:orders.read@1.0.0",)
    assert built.provenance.policy_refs == ("policy:orders-policy@1.0.0",)
    assert len(built.provenance.manifest_digest) == 64

    outcome = adapter.execute(
        built,
        harness.PrincipalContext(
            principal_id="reviewer",
            tenant_id="tenant-1",
            session_id="session-1",
        ),
        "review order-1",
        case_id="case-1",
        execution_id="execution-real-1",
    )

    assert outcome.trace.execution_id == "execution-real-1"
    assert outcome.trace.agent_id == "orders-agent"
    assert outcome.trace.candidate_id == candidate.candidate_id
    assert outcome.trace.events
    assert outcome.trace.prompt_ref == "prompt:orders-prompt@1.0.0"
    assert outcome.trace.skill_refs == ("skill:order-review@1.0.0",)
    assert outcome.trace.manifest_id == built.provenance.manifest_id
    assert outcome.trace.manifest_digest == built.provenance.manifest_digest

    snapshot = adapter.collect_environment_snapshot(
        candidate,
        factory=factory,
        built=built,
        fixture_version="fixture-1",
        environment_name="test",
        clock_mode="fixed",
        seed=7,
    )

    assert snapshot.runtime_name == "enterprise-agent-harness"
    assert snapshot.runtime_version
    assert snapshot.provider == "deterministic"
    assert snapshot.model == "test-model"
    assert snapshot.tool_hashes
    assert snapshot.policy_hashes
