"""Optional Enterprise Agent Harness integration.

The module uses lazy imports and structural boundaries so the Lab core remains
usable when Enterprise Agent Harness is not installed. Runtime construction,
permission checks, policy checks, approvals, state, tool execution, and trace
collection remain Harness responsibilities.
"""

from __future__ import annotations

import importlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from math import isfinite
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

from enterprise_agent_improvement_lab.contracts.candidates import (
    ArtifactProvenance,
    ArtifactRiskClassification,
    CandidateArtifact,
    CandidateArtifactKind,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.common import require_aware_utc, utc_now
from enterprise_agent_improvement_lab.contracts.environments import (
    EnvironmentSnapshot,
    SnapshotComponentHash,
)
from enterprise_agent_improvement_lab.contracts.failures import EvaluationScore
from enterprise_agent_improvement_lab.contracts.ingestion import (
    ProductionIngestionResult,
    ProductionSignal,
    ProductionTraceEvidence,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ApprovalDecision,
    ApprovalDecisionEvent,
    ApprovalRequestEvent,
    DelegationEvent,
    ErrorEvent,
    ExecutionEventRecord,
    ExecutionEventStatus,
    ExecutionTrace,
    ExternalEvent,
    ExternalEventDirection,
    HumanActionEvent,
    MessageEvent,
    ModelCallEvent,
    RetrievalEvent,
    StateMutationEvent,
    StateReadEvent,
    TokenUsage,
    ToolCallEvent,
    ToolCallOutcome,
    TriggerInfo,
    WorkflowTransitionEvent,
)
from enterprise_agent_improvement_lab.production_ingestion import ingest_production_trace
from enterprise_agent_improvement_lab.serialization import stable_json_dumps

if TYPE_CHECKING:
    from enterprise_agent_improvement_lab.storage.ports import LabStore

from .contracts import (
    HarnessBuiltAgent,
    HarnessBuiltCandidate,
    HarnessCandidateDefinition,
    HarnessComponentKind,
    HarnessExecutionResult,
    HarnessRegistryReference,
    HarnessRuntimeIdentity,
)


class HarnessIntegrationError(RuntimeError):
    """Raised when a Harness object cannot cross the integration boundary."""


class HarnessIntegrationUnavailableError(HarnessIntegrationError):
    """Raised when a Harness operation is requested without the optional package."""


_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
_AGENT_CONFIG_FIELDS = frozenset(
    {
        "goal",
        "supported_intents",
        "supported_languages",
        "capabilities",
        "allowed_tools",
        "policies",
        "provider_profile",
        "runtime_profile",
        "runtime_limits",
        "risk_level",
        "approval_requirements",
        "state_strategy",
        "memory_strategy",
        "owner_id",
        "template",
        "performance_metadata",
    }
)
_SAFE_TRACE_METADATA_KEYS = frozenset(
    {
        "agent_id",
        "agent_version",
        "action_digest",
        "allowed",
        "approval_id",
        "approval_required",
        "argument_digest",
        "argument_keys",
        "attempt",
        "attempts",
        "causation_id",
        "child_agent_id",
        "child_agent_version",
        "child_execution_id",
        "correlation_id",
        "decision_id",
        "decision",
        "decided_by",
        "delegation_depth",
        "delegation_id",
        "direction",
        "dropped_block_count",
        "error_code",
        "event_id",
        "evidence_count",
        "expires_at",
        "from_state",
        "input_length",
        "matched_rule_count",
        "operation",
        "outcome_id",
        "parent_agent_id",
        "parent_agent_version",
        "parent_execution_id",
        "policy_id",
        "principal_id",
        "reason_code",
        "request_id",
        "required",
        "resource",
        "result_status",
        "retry_count",
        "reviewer_role",
        "source",
        "state_id",
        "state_version",
        "status",
        "step_id",
        "step_index",
        "target_agent_id",
        "to_state",
        "tool_id",
        "tool_version",
        "transaction_id",
        "tenant_id",
        "trusted_block_count",
        "untrusted_block_count",
        "version",
        "workflow_id",
    }
)
_TOOL_RESULT_EVENTS = frozenset(
    {
        "tool_result_recorded",
        "tool_execution_completed",
        "tool_execution_failed",
        "tool_invocation_failed",
        "permission_denied",
        "tool_rejected",
    }
)
_APPROVAL_DECISION_EVENTS = {
    "approval_approved": ApprovalDecision.APPROVED,
    "approval_rejected": ApprovalDecision.REJECTED,
    "approval_changes_requested": ApprovalDecision.REQUEST_CHANGES,
    "approval_expired": ApprovalDecision.EXPIRED,
    "approval_stale": ApprovalDecision.REJECTED,
}


class EnterpriseAgentHarnessAdapter:
    """Adapt enterprise Lab candidates to and from Harness public boundaries."""

    def __init__(
        self,
        *,
        harness_module: ModuleType | object | None = None,
        runtime_name: str = "enterprise-agent-harness",
        runtime_version: str | None = None,
    ) -> None:
        if not runtime_name.strip():
            raise ValueError("runtime_name must not be empty")
        self._harness_module = harness_module
        self._runtime_name = runtime_name
        self._runtime_version = runtime_version

    @property
    def available(self) -> bool:
        """Return whether the optional Harness package can be imported."""

        try:
            self._harness()
        except HarnessIntegrationUnavailableError:
            return False
        return True

    def collect_environment_snapshot(
        self,
        candidate: EnterpriseAgentCandidate | HarnessCandidateDefinition | None = None,
        *,
        factory: object | None = None,
        built: HarnessBuiltCandidate | HarnessBuiltAgent | None = None,
        registry: object | None = None,
        artifacts: Sequence[CandidateArtifact] = (),
        agent_definition: object | Mapping[str, Any] | None = None,
        provider: str | None = None,
        provider_version: str | None = None,
        model: str | None = None,
        model_parameters: Mapping[str, Any] | Sequence[object] | None = None,
        feature_flags: Mapping[str, Any] | Sequence[object] | None = None,
        tenant_profile: str | Mapping[str, Any] | None = None,
        fixture_version: str = "unknown",
        external_service_stub_versions: Mapping[str, str] | Sequence[object] | None = None,
        environment_name: str = "unknown",
        clock_mode: str = "wall",
        seed: int | None = None,
    ) -> EnvironmentSnapshot:
        """Capture safe Harness registry and runtime state as a Lab snapshot.

        Harness registry revisions and public descriptors are read through the
        integration boundary. The Harness-generated registry snapshot ID is not
        used because it is intentionally unique per snapshot call.
        """

        harness = self._harness()
        definition = candidate if isinstance(candidate, HarnessCandidateDefinition) else None
        lab_candidate: EnterpriseAgentCandidate | None = (
            definition.candidate
            if definition is not None
            else candidate
            if isinstance(candidate, EnterpriseAgentCandidate)
            else None
        )
        resolved_registry = registry or getattr(factory, "agent_registry", None)
        resolved_artifacts = (
            definition.artifacts if definition is not None and definition.artifacts else artifacts
        )

        resolved_agent_definition = agent_definition or _built_agent_definition(built)
        if resolved_agent_definition is None and lab_candidate is not None:
            resolved_agent_definition = _registry_agent_definition(resolved_registry, lab_candidate)
        if resolved_agent_definition is None and definition is not None:
            resolved_agent_definition = _definition_from_config(definition.agent_config)
        if resolved_agent_definition is None:
            agent_artifact = next(
                (
                    artifact
                    for artifact in resolved_artifacts
                    if artifact.kind == CandidateArtifactKind.AGENT_DEFINITION
                ),
                None,
            )
            if agent_artifact is not None:
                resolved_agent_definition = _artifact_payload(agent_artifact)
        if resolved_agent_definition is None and lab_candidate is not None:
            resolved_agent_definition = lab_candidate
        agent_hash = _snapshot_sha256(resolved_agent_definition or {"agent": "unknown"})

        agent_registry_version = _registry_version(resolved_registry, default="unknown")
        tool_registry = getattr(resolved_registry, "tools", None)
        capability_registry = getattr(resolved_registry, "capabilities", None)
        tool_registry_version = _registry_version(tool_registry, default="unknown")
        capability_registry_version = _registry_version(capability_registry, default="unknown")

        tool_records = _registry_records(tool_registry, "descriptors")
        policy_records = _registry_policy_records(resolved_registry)
        tool_hashes = _component_hashes(tool_records, "tool_id")
        policy_hashes = _component_hashes(policy_records, "policy_id")
        policy_registry = getattr(resolved_registry, "policy_registry", None)
        policy_registry_version = _registry_version(policy_registry, default="")
        if not policy_registry_version:
            policy_registry_version = _snapshot_sha256(policy_records)

        runtime_version = self._runtime_version or _runtime_version(harness)
        runtime_source = _built_manifest(built)
        config_source = (
            _value(runtime_source, "source")
            or _value(runtime_source, "provider_profile")
            or (definition.agent_config if definition is not None else None)
            or resolved_agent_definition
        )
        provider_profile = _value(runtime_source, "provider_profile")
        if provider_profile is None:
            provider_profile = _value(config_source, "provider_profile")
        resolved_provider = provider or _optional_string(provider_profile, "provider_id")
        resolved_provider_version = provider_version or _optional_string(
            provider_profile, "version"
        )
        resolved_model = model or _optional_string(provider_profile, "model")
        resolved_parameters: Mapping[str, Any] | Sequence[object] | None = model_parameters
        if resolved_parameters is None:
            options = _value(provider_profile, "options")
            if isinstance(options, Mapping):
                resolved_parameters = options

        metadata = {
            "integration": "enterprise-agent-harness",
            "harness_version": runtime_version,
        }
        return EnvironmentSnapshot(
            agent_registry_version=agent_registry_version,
            tool_registry_version=tool_registry_version,
            capability_registry_version=capability_registry_version,
            policy_registry_version=policy_registry_version,
            agent_definition_hash=agent_hash,
            tool_hashes=tool_hashes,
            policy_hashes=policy_hashes,
            runtime_name=self._runtime_name,
            runtime_version=runtime_version,
            provider=resolved_provider,
            provider_version=resolved_provider_version,
            model=resolved_model,
            model_parameters=cast(Any, resolved_parameters or ()),
            feature_flags=cast(Any, feature_flags or ()),
            tenant_profile=cast(Any, tenant_profile),
            fixture_version=fixture_version,
            external_service_stub_versions=cast(Any, external_service_stub_versions or ()),
            environment_name=environment_name,
            clock_mode=clock_mode,
            seed=seed,
            metadata=cast(Any, metadata),
        )

    def to_harness_candidate_definition(
        self,
        candidate: EnterpriseAgentCandidate,
        *,
        artifacts: Sequence[CandidateArtifact] = (),
        registry_references: Sequence[HarnessRegistryReference | Mapping[str, Any] | str] = (),
        agent_definition: object | Mapping[str, Any] | None = None,
        goal: str | None = None,
        provider_profile: object | Mapping[str, Any] | None = None,
        runtime_profile: object | Mapping[str, Any] | str | None = None,
        runtime_limits: object | Mapping[str, Any] | None = None,
        supported_intents: Sequence[str] | None = None,
        supported_languages: Sequence[str] | None = None,
        approval_requirements: Sequence[str] | None = None,
        state_strategy: str | None = None,
        memory_strategy: str | None = None,
        owner_id: str | None = None,
        template: str | None = None,
    ) -> HarnessCandidateDefinition:
        """Create a typed Harness ``AgentConfig`` for one Lab candidate.

        The candidate's component fields are resolved to exact Harness
        registry references. Prompt artifacts remain references in the wrapper;
        the current Harness ``AgentConfig`` has no prompt field, so prompt text
        is never copied into unsafe metadata or sent to the runtime by this
        adapter.
        """

        harness = self._harness()
        resolved_artifacts = _validate_candidate_artifacts(candidate, artifacts)
        explicit_refs = tuple(_coerce_registry_reference(value) for value in registry_references)
        artifact_refs = tuple(
            _artifact_registry_reference(artifact) for artifact in resolved_artifacts
        )
        all_refs = _unique_registry_references((*explicit_refs, *artifact_refs))

        payload = _agent_config_payload(
            candidate,
            resolved_artifacts,
            agent_definition=agent_definition,
        )
        if goal is not None:
            payload["goal"] = goal
        if provider_profile is not None:
            payload["provider_profile"] = _external_data(provider_profile)
        if runtime_limits is not None:
            payload["runtime_limits"] = _external_data(runtime_limits)
        if supported_intents is not None:
            payload["supported_intents"] = list(supported_intents)
        if supported_languages is not None:
            payload["supported_languages"] = list(supported_languages)
        if approval_requirements is not None:
            payload["approval_requirements"] = list(approval_requirements)
        if state_strategy is not None:
            payload["state_strategy"] = state_strategy
        if memory_strategy is not None:
            payload["memory_strategy"] = memory_strategy
        if owner_id is not None:
            payload["owner_id"] = owner_id
        if template is not None:
            payload["template"] = template

        payload["identity"] = {
            "agent_id": candidate.agent_id,
            "version": _harness_version(candidate.agent_version or candidate.version),
        }

        payload["allowed_tools"], tool_refs = _component_references(
            HarnessComponentKind.TOOL,
            candidate.tools or candidate.tool_bindings,
            payload.get("allowed_tools", ()),
            all_refs,
            resolved_artifacts,
        )
        payload["capabilities"], capability_refs = _component_references(
            HarnessComponentKind.CAPABILITY,
            candidate.capabilities,
            payload.get("capabilities", ()),
            all_refs,
            resolved_artifacts,
        )
        payload["policies"], policy_refs = _component_references(
            HarnessComponentKind.POLICY,
            candidate.policies,
            payload.get("policies", ()),
            all_refs,
            resolved_artifacts,
        )

        if runtime_profile is not None:
            payload["runtime_profile"] = _runtime_profile_reference(
                runtime_profile, all_refs, resolved_artifacts
            )
        elif candidate.runtime_profile is not None:
            payload["runtime_profile"] = _runtime_profile_reference(
                candidate.runtime_profile, all_refs, resolved_artifacts
            )
        elif "runtime_profile" in payload:
            payload["runtime_profile"] = _runtime_profile_reference(
                payload["runtime_profile"], all_refs, resolved_artifacts
            )

        config_values = {
            key: value for key, value in payload.items() if key in _AGENT_CONFIG_FIELDS
        }
        config_values["identity"] = payload["identity"]
        config_type = getattr(harness, "AgentConfig", None)
        if config_type is None or not hasattr(config_type, "model_validate"):
            raise HarnessIntegrationError(
                "Enterprise Agent Harness does not expose its typed AgentConfig contract"
            )
        try:
            config = config_type.model_validate(config_values)
        except Exception as exc:  # noqa: BLE001 - external contract boundary.
            raise HarnessIntegrationError(
                f"Lab candidate could not become a Harness AgentConfig: {exc}"
            ) from exc

        resolved_refs = _unique_registry_references(
            (
                *all_refs,
                *tool_refs,
                *capability_refs,
                *policy_refs,
            )
        )
        agent_version = _harness_version(candidate.agent_version or candidate.version)
        identity = HarnessRuntimeIdentity(
            runtime_name=self._runtime_name,
            runtime_version=self._runtime_version or _runtime_version(harness),
            agent_id=candidate.agent_id,
            agent_version=agent_version,
            candidate_id=candidate.candidate_id,
        )
        return HarnessCandidateDefinition(
            candidate=candidate,
            agent_config=config,
            runtime_identity=identity,
            artifact_references=candidate.artifacts,
            artifacts=tuple(resolved_artifacts),
            registry_references=resolved_refs,
        )

    def build_candidate(
        self,
        candidate: EnterpriseAgentCandidate | HarnessCandidateDefinition,
        factory: object,
        *,
        artifacts: Sequence[CandidateArtifact] = (),
        registry_references: Sequence[HarnessRegistryReference | Mapping[str, Any] | str] = (),
        **build_kwargs: Any,
    ) -> HarnessBuiltCandidate:
        """Build a candidate through the Harness ``AgentFactory`` only."""

        definition = (
            candidate
            if isinstance(candidate, HarnessCandidateDefinition)
            else self.to_harness_candidate_definition(
                candidate,
                artifacts=artifacts,
                registry_references=registry_references,
            )
        )
        build = getattr(factory, "build", None)
        if not callable(build):
            raise HarnessIntegrationError("Harness factory must expose build(config, ...)")
        try:
            built = cast(HarnessBuiltAgent, build(definition.agent_config, **build_kwargs))
        except Exception as exc:  # noqa: BLE001 - external factory boundary.
            raise HarnessIntegrationError(
                f"Harness factory could not build candidate {definition.candidate_id!r}: {exc}"
            ) from exc
        if not callable(getattr(built, "execute", None)) or not callable(
            getattr(built, "trace_for", None)
        ):
            raise HarnessIntegrationError("Harness factory returned an incompatible built agent")
        return HarnessBuiltCandidate(definition=definition, built_agent=built)

    def execute(
        self,
        built: HarnessBuiltCandidate | HarnessBuiltAgent,
        principal: object,
        input_text: str,
        *,
        candidate_id: str | None = None,
        case_id: str | None = None,
        trigger: TriggerInfo | None = None,
        **execute_kwargs: Any,
    ) -> HarnessExecutionResult:
        """Execute a built candidate and translate its exported Harness trace."""

        agent, resolved_candidate_id, identity = _built_parts(
            built,
            candidate_id=candidate_id,
            runtime_name=self._runtime_name,
            runtime_version=self._runtime_version,
        )
        try:
            outcome = agent.execute(principal, input_text, **execute_kwargs)
            execution_id = _required_string(outcome, "execution_id")
            harness_trace = agent.trace_for(execution_id)
        except Exception as exc:  # noqa: BLE001 - external runtime boundary.
            raise HarnessIntegrationError(f"Harness execution failed: {exc}") from exc
        execution_trace = harness_run_trace_to_execution_trace(
            harness_trace,
            candidate_id=resolved_candidate_id,
            case_id=case_id,
            trigger=trigger,
            outcome=outcome,
        )
        return HarnessExecutionResult(
            candidate_id=resolved_candidate_id,
            runtime_identity=identity,
            outcome=outcome,
            harness_trace=harness_trace,
            execution_trace=execution_trace,
        )

    def resume(
        self,
        built: HarnessBuiltCandidate | HarnessBuiltAgent,
        execution_id: str,
        *,
        principal: object | None = None,
        approval_decision: object | None = None,
        candidate_id: str | None = None,
        case_id: str | None = None,
        trigger: TriggerInfo | None = None,
    ) -> HarnessExecutionResult:
        """Resume a Harness execution through its own approval/state boundary."""

        agent, resolved_candidate_id, identity = _built_parts(
            built,
            candidate_id=candidate_id,
            runtime_name=self._runtime_name,
            runtime_version=self._runtime_version,
        )
        runtime = getattr(agent, "runtime", None)
        resume = getattr(runtime, "resume", None) if runtime is not None else None
        if not callable(resume):
            resume = getattr(agent, "resume", None)
        if not callable(resume):
            raise HarnessIntegrationError("Harness built agent does not expose resume(...)")
        kwargs: dict[str, Any] = {}
        if principal is not None:
            kwargs["principal"] = principal
        if approval_decision is not None:
            kwargs["approval_decision"] = approval_decision
        try:
            outcome = resume(execution_id, **kwargs)
            harness_trace = agent.trace_for(execution_id)
        except Exception as exc:  # noqa: BLE001 - external runtime boundary.
            raise HarnessIntegrationError(f"Harness resume failed: {exc}") from exc
        execution_trace = harness_run_trace_to_execution_trace(
            harness_trace,
            candidate_id=resolved_candidate_id,
            case_id=case_id,
            trigger=trigger,
            outcome=outcome,
        )
        return HarnessExecutionResult(
            candidate_id=resolved_candidate_id,
            runtime_identity=identity,
            outcome=outcome,
            harness_trace=harness_trace,
            execution_trace=execution_trace,
        )

    def _harness(self) -> ModuleType | object:
        if self._harness_module is not None:
            return self._harness_module
        try:
            return importlib.import_module("enterprise_agent_harness")
        except ModuleNotFoundError as exc:
            raise HarnessIntegrationUnavailableError(
                "Enterprise Agent Harness is not installed; install it for Harness integration"
            ) from exc


def collect_harness_environment_snapshot(
    candidate: EnterpriseAgentCandidate | HarnessCandidateDefinition | None = None,
    *,
    adapter: EnterpriseAgentHarnessAdapter | None = None,
    **kwargs: Any,
) -> EnvironmentSnapshot:
    """Collect a Harness environment snapshot through the optional adapter."""

    return (adapter or EnterpriseAgentHarnessAdapter()).collect_environment_snapshot(
        candidate,
        **kwargs,
    )


def _built_manifest(built: HarnessBuiltCandidate | HarnessBuiltAgent | None) -> object | None:
    if built is None:
        return None
    if isinstance(built, HarnessBuiltCandidate):
        return getattr(built.built_agent, "manifest", None)
    return getattr(built, "manifest", None)


def _built_agent_definition(
    built: HarnessBuiltCandidate | HarnessBuiltAgent | None,
) -> object | None:
    return _value(_built_manifest(built), "agent")


def _definition_from_config(config: object) -> object | None:
    if config is None:
        return None
    values = getattr(config, "values", None)
    if isinstance(values, Mapping):
        return values
    return config


def _registry_agent_definition(
    registry: object | None,
    candidate: EnterpriseAgentCandidate,
) -> object | None:
    getter = getattr(registry, "get", None)
    if not callable(getter):
        return None
    version = candidate.agent_version or candidate.version
    for candidate_version in (version, _harness_version(version)):
        try:
            return cast(object, getter(candidate.agent_id, candidate_version))
        except Exception:  # noqa: BLE001 - registry implementations vary at this boundary.
            continue
    return None


def _registry_version(registry: object | None, *, default: str) -> str:
    revision = _value(registry, "revision")
    if revision is None:
        return default
    return str(revision)


def _registry_records(registry: object | None, method_name: str) -> tuple[object, ...]:
    method = getattr(registry, method_name, None)
    if not callable(method):
        return ()
    try:
        values = method(include_inactive=True)
    except TypeError:
        values = method()
    return _as_sequence(values)


def _registry_policy_records(registry: object | None) -> tuple[object, ...]:
    policy_registry = getattr(registry, "policy_registry", None)
    if policy_registry is not None:
        records = _registry_records(policy_registry, "list")
        if records:
            return records
    values = _value(registry, "policies")
    if callable(values):
        values = values()
    return _as_sequence(values)


def _component_hashes(
    records: Sequence[object], id_field: str
) -> tuple[SnapshotComponentHash, ...]:
    result: list[SnapshotComponentHash] = []
    for record in records:
        component_id = _optional_string(record, id_field)
        version = _optional_string(record, "version")
        if component_id is None or version is None:
            continue
        result.append(
            SnapshotComponentHash(
                component_id=component_id,
                version=version,
                sha256=_snapshot_sha256(record),
            )
        )
    return tuple(result)


def _snapshot_sha256(value: object) -> str:
    normalized = _external_value(value)
    return sha256(stable_json_dumps(normalized).encode("utf-8")).hexdigest()


def harness_run_trace_to_execution_trace(
    trace: object | Mapping[str, Any],
    *,
    candidate_id: str | None = None,
    case_id: str | None = None,
    trigger: TriggerInfo | None = None,
    outcome: object | None = None,
    evidence_refs: Sequence[str] = (),
) -> ExecutionTrace:
    """Convert one Harness ``RunTrace`` into a typed Lab ``ExecutionTrace``.

    Harness trace events use one-based contiguous sequences. The values and
    event IDs are retained exactly; Lab validates uniqueness and orders the
    resulting events by that sequence. Harness metadata is copied through a
    small safe allowlist. Raw prompts, tool arguments, tool outputs, and state
    payloads are not copied.
    """

    trace_id = _required_string(trace, "trace_id")
    execution_id = _required_string(trace, "execution_id")
    agent_id = _required_string(trace, "agent_id")
    agent_version = _required_string(trace, "agent_version")
    raw_events = _sequence_value(trace, "events")
    if not raw_events:
        raise HarnessIntegrationError("Harness RunTrace must contain at least one event")

    sorted_events = sorted(raw_events, key=lambda event: _required_int(event, "sequence"))
    for event in sorted_events:
        event_execution_id = _optional_string(event, "execution_id")
        if event_execution_id is not None and event_execution_id != execution_id:
            event_id = _required_string(event, "event_id")
            raise HarnessIntegrationError(
                f"Harness event {event_id!r} belongs to a different execution"
            )
    outcome_execution_id = _optional_string(outcome, "execution_id")
    if outcome_execution_id is not None and outcome_execution_id != execution_id:
        raise HarnessIntegrationError("Harness outcome belongs to a different execution")
    provider_records = _sequence_value(trace, "provider_calls")
    policy_decisions = _sequence_value(trace, "policy_decisions")
    tool_records = _sequence_value(trace, "tool_executions")
    outcome_tool_calls = _sequence_value(outcome, "tool_calls") if outcome is not None else ()
    state = _ConversionState(provider_records, tool_records, outcome_tool_calls)
    events = tuple(
        _convert_harness_event(
            event,
            trace=trace,
            state=state,
        )
        for event in sorted_events
    )

    generated_at = _required_datetime(trace, "generated_at")
    event_timestamps = [_required_datetime(event, "occurred_at") for event in sorted_events]
    started_at = min(event_timestamps)
    ended_at = max(generated_at, max(event_timestamps))
    metrics = _value(trace, "metrics")
    usage = _trace_usage(metrics)
    cost = _trace_cost(metrics)
    resolved_candidate_id = (
        candidate_id
        or _optional_string(trace, "candidate_id")
        or _optional_string(_value(trace, "metadata"), "candidate_id")
        or f"harness:{agent_id}@{agent_version}"
    )
    resolved_trigger = trigger or _trace_trigger(trace)
    trace_evidence = list(evidence_refs)
    trace_evidence.extend(_string_sequence(_value(outcome, "evidence_ids")))
    trace_evidence.extend(
        decision_id
        for decision in policy_decisions
        if (decision_id := _optional_string(decision, "decision_id")) is not None
    )
    for call in outcome_tool_calls:
        trace_evidence.extend(_string_sequence(_value(call, "evidence_ids")))
    metadata = _safe_trace_metadata(trace)
    metadata["harness_trace_id"] = trace_id
    metadata["harness_event_count"] = str(len(events))
    metadata["harness_policy_decision_count"] = str(len(policy_decisions))
    if _optional_string(trace, "correlation_id") is not None:
        metadata["correlation_id"] = _optional_string(trace, "correlation_id") or ""

    return ExecutionTrace(
        execution_id=execution_id,
        agent_id=agent_id,
        agent_version=agent_version,
        candidate_id=resolved_candidate_id,
        case_id=case_id,
        session_id=_optional_string(trace, "session_id"),
        principal_id=_optional_string(trace, "principal_id"),
        tenant_id=_optional_string(trace, "tenant_id"),
        trigger=resolved_trigger,
        started_at=started_at,
        ended_at=ended_at,
        events=events,
        usage=usage,
        cost=cost,
        evidence_refs=tuple(dict.fromkeys(item for item in trace_evidence if item)),
        metadata=metadata,
    )


def harness_agent_definition_to_candidate_artifact(
    definition: object | Mapping[str, Any],
    *,
    artifact_id: str | None = None,
    created_at: datetime | None = None,
    provenance: ArtifactProvenance | None = None,
) -> CandidateArtifact:
    """Store a safe Harness agent definition as an immutable Lab artifact."""

    payload = _external_data(definition)
    component_id, version = _component_identity(payload, fallback_id=artifact_id)
    resolved_id = artifact_id or component_id
    return _component_artifact(
        artifact_id=resolved_id,
        name=f"Harness agent {component_id}",
        version=version,
        kind=CandidateArtifactKind.AGENT_DEFINITION,
        payload=payload,
        owner=_string_or_default(payload.get("owner_id"), "application"),
        provenance=provenance,
        created_at=created_at,
        registry_reference=f"agent:{component_id}@{version}",
    )


def harness_tool_definition_to_candidate_artifact(
    definition: object | Mapping[str, Any],
    *,
    artifact_id: str | None = None,
    created_at: datetime | None = None,
    provenance: ArtifactProvenance | None = None,
    kind: CandidateArtifactKind = CandidateArtifactKind.TOOL_CONFIGURATION,
) -> CandidateArtifact:
    """Store a safe Harness tool definition or descriptor as an artifact."""

    descriptor = getattr(definition, "descriptor", None)
    payload = _external_data(descriptor if descriptor is not None else definition)
    component_id, version = _component_identity(payload, fallback_id=artifact_id)
    return _component_artifact(
        artifact_id=artifact_id or component_id,
        name=f"Harness tool {component_id}",
        version=version,
        kind=kind,
        payload=payload,
        owner=_string_or_default(payload.get("owner_id"), "application"),
        risk=_risk_classification(payload.get("risk_level")),
        provenance=provenance,
        created_at=created_at,
        registry_reference=f"tool:{component_id}@{version}",
    )


def harness_capability_definition_to_candidate_artifact(
    definition: object | Mapping[str, Any],
    *,
    artifact_id: str | None = None,
    created_at: datetime | None = None,
    provenance: ArtifactProvenance | None = None,
) -> CandidateArtifact:
    """Store a typed Harness capability definition as an artifact."""

    payload = _external_data(definition)
    component_id, version = _component_identity(payload, fallback_id=artifact_id)
    return _component_artifact(
        artifact_id=artifact_id or component_id,
        name=f"Harness capability {component_id}",
        version=version,
        kind=CandidateArtifactKind.CAPABILITY_CONFIGURATION,
        payload=payload,
        owner=_string_or_default(payload.get("owner_id"), "application"),
        risk=_risk_classification(payload.get("risk_level")),
        provenance=provenance,
        created_at=created_at,
        registry_reference=f"capability:{component_id}@{version}",
    )


def harness_policy_definition_to_candidate_artifact(
    definition: object | Mapping[str, Any],
    *,
    artifact_id: str | None = None,
    created_at: datetime | None = None,
    provenance: ArtifactProvenance | None = None,
) -> CandidateArtifact:
    """Store a typed Harness policy definition as an artifact."""

    payload = _external_data(definition)
    component_id, version = _component_identity(payload, fallback_id=artifact_id)
    return _component_artifact(
        artifact_id=artifact_id or component_id,
        name=f"Harness policy {component_id}",
        version=version,
        kind=CandidateArtifactKind.POLICY,
        payload=payload,
        owner=_string_or_default(payload.get("owner_id"), "application"),
        provenance=provenance,
        created_at=created_at,
        registry_reference=f"policy:{component_id}@{version}",
    )


def harness_approval_policy_to_candidate_artifact(
    definition: object | Mapping[str, Any],
    *,
    artifact_id: str | None = None,
    created_at: datetime | None = None,
    provenance: ArtifactProvenance | None = None,
) -> CandidateArtifact:
    """Store a typed Harness approval policy as an artifact."""

    payload = _external_data(definition)
    component_id, version = _component_identity(payload, fallback_id=artifact_id)
    return _component_artifact(
        artifact_id=artifact_id or component_id,
        name=f"Harness approval policy {component_id}",
        version=version,
        kind=CandidateArtifactKind.APPROVAL_POLICY,
        payload=payload,
        owner=_string_or_default(payload.get("owner_id"), "application"),
        provenance=provenance,
        created_at=created_at,
        registry_reference=f"approval_policy:{component_id}@{version}",
    )


def _load_registry_reference(value: object) -> HarnessRegistryReference:
    if isinstance(value, HarnessRegistryReference):
        return value
    if isinstance(value, Mapping):
        return HarnessRegistryReference.model_validate(value)
    if isinstance(value, str):
        parsed_kind, parsed_id, parsed_version = _parse_reference_string(value)
        return HarnessRegistryReference(
            component_kind=parsed_kind,
            component_id=parsed_id,
            version=parsed_version,
        )
    component_id: str | None = _optional_string(value, "component_id") or _optional_string(
        value, "id"
    )
    version: str | None = _optional_string(value, "version")
    if component_id and version:
        raw_kind = _optional_string(value, "component_kind") or _optional_string(value, "kind")
        if raw_kind is None:
            raise HarnessIntegrationError("Harness registry references need a component kind")
        return HarnessRegistryReference(
            component_kind=HarnessComponentKind(raw_kind),
            component_id=component_id,
            version=version,
        )
    raise HarnessIntegrationError("Unsupported Harness registry reference")


def _coerce_registry_reference(value: object) -> HarnessRegistryReference:
    try:
        return _load_registry_reference(value)
    except Exception as exc:
        if isinstance(exc, HarnessIntegrationError):
            raise
        raise HarnessIntegrationError(f"Invalid Harness registry reference: {exc}") from exc


def _artifact_registry_reference(artifact: CandidateArtifact) -> HarnessRegistryReference | None:
    if artifact.registry_reference is None:
        payload = _artifact_payload(artifact)
        kind = _artifact_component_kind(artifact.kind)
        if kind is None:
            return None
        component_id = _optional_string(payload, "component_id") or _optional_string(
            payload, _component_id_field(kind)
        )
        version = _optional_string(payload, "version") or artifact.version
        if component_id is None:
            return None
        return HarnessRegistryReference(
            component_kind=kind,
            component_id=component_id,
            version=version,
            source_artifact_id=artifact.artifact_id,
        )
    kind, component_id, version = _parse_reference_string(artifact.registry_reference)
    return HarnessRegistryReference(
        component_kind=kind,
        component_id=component_id,
        version=version,
        source_artifact_id=artifact.artifact_id,
    )


def _component_references(
    kind: HarnessComponentKind,
    candidate_values: Sequence[object],
    payload_values: object,
    known_refs: Sequence[HarnessRegistryReference],
    artifacts: Sequence[CandidateArtifact],
) -> tuple[list[dict[str, str]], tuple[HarnessRegistryReference, ...]]:
    values = tuple(candidate_values) if candidate_values else _as_sequence(payload_values)
    if not values:
        return [], ()
    refs: list[HarnessRegistryReference] = []
    result: list[dict[str, str]] = []
    for value in values:
        resolved = _resolve_component_reference(value, kind, known_refs, artifacts)
        if resolved not in refs:
            refs.append(resolved)
            result.append(
                {
                    "component_id": resolved.component_id,
                    "version": _harness_version(resolved.version),
                }
            )
    return result, tuple(refs)


def _resolve_component_reference(
    value: object,
    kind: HarnessComponentKind,
    known_refs: Sequence[HarnessRegistryReference],
    artifacts: Sequence[CandidateArtifact],
) -> HarnessRegistryReference:
    if isinstance(value, Mapping):
        component_id = (
            _optional_string(value, "component_id")
            or _optional_string(value, "id")
            or _optional_string(value, f"{kind.value}_id")
        )
        version = _optional_string(value, "version")
        if component_id and version:
            return HarnessRegistryReference(
                component_kind=kind,
                component_id=component_id,
                version=version,
            )
    elif isinstance(value, str):
        if "@" in value:
            parsed_kind, component_id, version = _parse_reference_string(value, default_kind=kind)
            if parsed_kind != kind:
                raise HarnessIntegrationError(
                    f"Reference {value!r} is for {parsed_kind.value}, not {kind.value}"
                )
            return HarnessRegistryReference(
                component_kind=kind,
                component_id=component_id,
                version=version,
            )
        component_id = value
    else:
        component_id = _optional_string(value, "component_id") or _optional_string(value, "id")
        version = _optional_string(value, "version")
        if component_id and version:
            return HarnessRegistryReference(
                component_kind=kind,
                component_id=component_id,
                version=version,
            )

    if not component_id:
        raise HarnessIntegrationError(f"{kind.value} references must include an identity")
    matches = [
        ref
        for ref in (*known_refs, *(_artifact_registry_reference(item) for item in artifacts))
        if ref is not None and ref.component_kind == kind and ref.component_id == component_id
    ]
    unique = _unique_registry_references(matches)
    if len(unique) != 1:
        if not unique:
            raise HarnessIntegrationError(
                f"{kind.value} {component_id!r} needs an exact Harness registry version"
            )
        raise HarnessIntegrationError(
            f"{kind.value} {component_id!r} has multiple Harness registry versions"
        )
    return unique[0]


def _runtime_profile_reference(
    value: object,
    known_refs: Sequence[HarnessRegistryReference],
    artifacts: Sequence[CandidateArtifact],
) -> dict[str, str]:
    if isinstance(value, Mapping):
        component_id = _optional_string(value, "component_id") or _optional_string(
            value, "profile_id"
        )
        version = _optional_string(value, "version")
        if component_id and version:
            return {"component_id": component_id, "version": _harness_version(version)}
    if isinstance(value, str):
        if "@" in value:
            _kind, component_id, version = _parse_reference_string(
                value, default_kind=HarnessComponentKind.RUNTIME_PROFILE
            )
            return {"component_id": component_id, "version": _harness_version(version)}
        component_id = value
    else:
        component_id = _optional_string(value, "component_id") or _optional_string(
            value, "profile_id"
        )
    if not component_id:
        raise HarnessIntegrationError("runtime_profile needs an exact profile identity")
    matches = [
        ref
        for ref in (*known_refs, *(_artifact_registry_reference(item) for item in artifacts))
        if ref is not None
        and ref.component_kind == HarnessComponentKind.RUNTIME_PROFILE
        and ref.component_id == component_id
    ]
    unique = _unique_registry_references(matches)
    if len(unique) != 1:
        raise HarnessIntegrationError(
            f"runtime profile {component_id!r} needs one exact Harness registry version"
        )
    return {"component_id": component_id, "version": _harness_version(unique[0].version)}


def _agent_config_payload(
    candidate: EnterpriseAgentCandidate,
    artifacts: Sequence[CandidateArtifact],
    *,
    agent_definition: object | Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    source = agent_definition
    if source is None:
        source_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact.kind == CandidateArtifactKind.AGENT_DEFINITION
            ),
            None,
        )
        if source_artifact is not None:
            source = _artifact_payload(source_artifact)
    if source is not None:
        raw = _external_data(source)
        if isinstance(raw.get("agent_config"), Mapping):
            raw = dict(raw["agent_config"])
        payload.update({key: value for key, value in raw.items() if key in _AGENT_CONFIG_FIELDS})
    if "provider_profile" not in payload:
        model_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact.artifact_id == candidate.model_configuration
                or artifact.kind == CandidateArtifactKind.MODEL_CONFIGURATION
            ),
            None,
        )
        if model_artifact is not None:
            model_payload = _artifact_payload(model_artifact)
            payload["provider_profile"] = model_payload.get(
                "provider_profile",
                {
                    key: model_payload[key]
                    for key in ("provider_id", "version", "model", "options")
                    if key in model_payload
                },
            )
    if candidate.memory_configuration is not None and "memory_strategy" not in payload:
        payload["memory_strategy"] = candidate.memory_configuration
    return payload


def _validate_candidate_artifacts(
    candidate: EnterpriseAgentCandidate,
    artifacts: Sequence[CandidateArtifact],
) -> tuple[CandidateArtifact, ...]:
    by_id: dict[str, CandidateArtifact] = {}
    for artifact in artifacts:
        if artifact.artifact_id in by_id:
            raise HarnessIntegrationError(
                f"candidate artifacts contain duplicate ID {artifact.artifact_id!r}"
            )
        if artifact.artifact_id not in candidate.artifact_ids:
            raise HarnessIntegrationError(
                f"artifact {artifact.artifact_id!r} is not referenced by the candidate"
            )
        by_id[artifact.artifact_id] = artifact
    for reference in candidate.artifacts:
        matched_artifact = by_id.get(reference.artifact_id)
        if matched_artifact is None:
            continue
        if reference.version is not None and _harness_version(
            reference.version
        ) != _harness_version(matched_artifact.version):
            raise HarnessIntegrationError(
                f"artifact reference {reference.artifact_id!r} does not match its version"
            )
        if (
            reference.content_sha256 is not None
            and reference.content_sha256 != matched_artifact.checksum
        ):
            raise HarnessIntegrationError(
                f"artifact reference {reference.artifact_id!r} does not match its checksum"
            )
    return tuple(artifacts)


def _component_artifact(
    *,
    artifact_id: str,
    name: str,
    version: str,
    kind: CandidateArtifactKind,
    payload: Mapping[str, Any],
    owner: str,
    risk: ArtifactRiskClassification = ArtifactRiskClassification.LOW,
    provenance: ArtifactProvenance | None,
    created_at: datetime | None,
    registry_reference: str,
) -> CandidateArtifact:
    resolved_created_at = created_at or utc_now()
    return CandidateArtifact(
        artifact_id=artifact_id,
        name=name,
        version=version,
        kind=kind,
        content=stable_json_dumps(dict(payload)),
        provenance=provenance
        or ArtifactProvenance(
            source="enterprise-agent-harness",
            source_ref=registry_reference,
            created_at=resolved_created_at,
        ),
        owner=owner,
        risk_classification=risk,
        registry_reference=registry_reference,
        created_at=resolved_created_at,
        metadata={"integration": "enterprise-agent-harness"},
    )


def _external_data(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): _external_value(item) for key, item in value.items()}
    descriptor = getattr(value, "descriptor", None)
    if descriptor is not None and descriptor is not value:
        value = descriptor
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json", exclude_none=False)
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, Mapping):
            return {str(key): _external_value(item) for key, item in dumped.items()}
    if is_dataclass(value):
        dumped = asdict(cast(Any, value))
        return {str(key): _external_value(item) for key, item in dumped.items()}
    raise HarnessIntegrationError("Harness definition must expose a typed mapping or model_dump()")


def _external_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _external_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_external_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _external_value(asdict(cast(Any, value)))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _external_value(model_dump(mode="json", exclude_none=False))
    return value


def _artifact_payload(artifact: CandidateArtifact) -> dict[str, Any]:
    try:
        payload = json.loads(artifact.content)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HarnessIntegrationError(
            f"artifact {artifact.artifact_id!r} does not contain JSON Harness configuration"
        ) from exc
    if not isinstance(payload, dict):
        raise HarnessIntegrationError(
            f"artifact {artifact.artifact_id!r} must contain a JSON object"
        )
    return cast(dict[str, Any], payload)


def _component_identity(payload: Mapping[str, Any], *, fallback_id: str | None) -> tuple[str, str]:
    identity = payload.get("identity")
    identity_data = identity if isinstance(identity, Mapping) else payload
    component_id = (
        _optional_string(identity_data, "agent_id")
        or _optional_string(identity_data, "component_id")
        or _optional_string(identity_data, "tool_id")
        or _optional_string(identity_data, "capability_id")
        or _optional_string(identity_data, "policy_id")
        or fallback_id
    )
    version = _optional_string(identity_data, "version") or _optional_string(payload, "version")
    if not component_id or not version:
        raise HarnessIntegrationError("Harness definition must include an identity and version")
    return component_id, version


def _artifact_component_kind(kind: CandidateArtifactKind) -> HarnessComponentKind | None:
    return {
        CandidateArtifactKind.AGENT_DEFINITION: HarnessComponentKind.AGENT,
        CandidateArtifactKind.TOOL_BINDING: HarnessComponentKind.TOOL,
        CandidateArtifactKind.TOOL_CONFIGURATION: HarnessComponentKind.TOOL,
        CandidateArtifactKind.CAPABILITY_CONFIGURATION: HarnessComponentKind.CAPABILITY,
        CandidateArtifactKind.POLICY: HarnessComponentKind.POLICY,
        CandidateArtifactKind.APPROVAL_POLICY: HarnessComponentKind.APPROVAL_POLICY,
    }.get(kind)


def _component_id_field(kind: HarnessComponentKind) -> str:
    return {
        HarnessComponentKind.AGENT: "agent_id",
        HarnessComponentKind.TOOL: "tool_id",
        HarnessComponentKind.CAPABILITY: "capability_id",
        HarnessComponentKind.POLICY: "policy_id",
        HarnessComponentKind.APPROVAL_POLICY: "policy_id",
        HarnessComponentKind.RUNTIME_PROFILE: "profile_id",
        HarnessComponentKind.PROVIDER: "provider_id",
    }[kind]


def _parse_reference_string(
    value: str,
    *,
    default_kind: HarnessComponentKind | None = None,
) -> tuple[HarnessComponentKind, str, str]:
    raw = value.strip()
    prefix: str | None = None
    if ":" in raw:
        possible_prefix, raw = raw.split(":", 1)
        if possible_prefix in {item.value for item in HarnessComponentKind}:
            prefix = possible_prefix
    if "@" not in raw:
        raise HarnessIntegrationError(f"Harness reference {value!r} must include @version")
    component_id, version = raw.rsplit("@", 1)
    if not component_id.strip() or not version.strip():
        raise HarnessIntegrationError(f"Invalid Harness reference {value!r}")
    kind = HarnessComponentKind(prefix) if prefix is not None else default_kind
    if kind is None:
        raise HarnessIntegrationError(f"Harness reference {value!r} must include a component kind")
    return kind, component_id, version


def _harness_version(value: str) -> str:
    normalized = value.strip()
    if not _VERSION_PATTERN.fullmatch(normalized):
        raise HarnessIntegrationError(
            f"Harness component version must be MAJOR.MINOR.PATCH: {value!r}"
        )
    if normalized.count(".") == 1:
        return f"{normalized}.0"
    return normalized


def _runtime_version(harness: object) -> str:
    version = getattr(harness, "__version__", None)
    if isinstance(version, str) and version.strip():
        return version.strip()
    try:
        return package_version("enterprise-agent-harness")
    except PackageNotFoundError:
        return "unknown"


def _built_parts(
    built: HarnessBuiltCandidate | HarnessBuiltAgent,
    *,
    candidate_id: str | None,
    runtime_name: str,
    runtime_version: str | None,
) -> tuple[HarnessBuiltAgent, str, HarnessRuntimeIdentity]:
    if isinstance(built, HarnessBuiltCandidate):
        return built.built_agent, built.candidate_id, built.definition.runtime_identity
    if not candidate_id or not candidate_id.strip():
        raise HarnessIntegrationError(
            "candidate_id is required when executing an unwrapped Harness built agent"
        )
    manifest = getattr(built, "manifest", None)
    agent = getattr(manifest, "agent", None)
    agent_id = (
        _optional_string(agent, "agent_id")
        or _optional_string(built, "agent_id")
        or "unknown-agent"
    )
    agent_version = (
        _optional_string(agent, "version") or _optional_string(built, "version") or "1.0.0"
    )
    return (
        built,
        candidate_id,
        HarnessRuntimeIdentity(
            runtime_name=runtime_name,
            runtime_version=runtime_version or "unknown",
            agent_id=agent_id,
            agent_version=_harness_version(agent_version),
            candidate_id=candidate_id,
        ),
    )


class _ConversionState:
    """Deterministic cursors for safe Harness side records."""

    def __init__(
        self,
        provider_records: Sequence[object],
        tool_records: Sequence[object],
        outcome_tool_calls: Sequence[object],
    ) -> None:
        self.tool_records: dict[tuple[str, str], list[object]] = defaultdict(list)
        for record in tool_records:
            key = (
                _optional_string(record, "tool_id") or "unknown-tool",
                _optional_string(record, "tool_version") or "unknown",
            )
            self.tool_records[key].append(record)
        self.active_tool_records: dict[tuple[str, str], object] = {}
        self.outcome_tool_calls: dict[str, list[object]] = defaultdict(list)
        for call in outcome_tool_calls:
            tool_id = _optional_string(call, "tool_id")
            if tool_id:
                self.outcome_tool_calls[tool_id].append(call)
        self.provider_records: dict[str, list[object]] = defaultdict(list)
        for record in provider_records:
            operation = _optional_string(record, "operation") or "unknown"
            self.provider_records[operation].append(record)
        self.provider_active: dict[str, object] = {}

    def tool_record(self, tool_id: str, version: str, event_type: str) -> object | None:
        key = (tool_id, version)
        if key in self.active_tool_records:
            if event_type in {"tool_result_recorded", "tool_execution_failed"}:
                record = self.active_tool_records[key]
                if event_type == "tool_result_recorded":
                    self.active_tool_records.pop(key, None)
                return record
            return self.active_tool_records[key]
        records = self.tool_records.get(key, [])
        if not records:
            return None
        record = records.pop(0)
        if event_type in {"tool_execution_completed", "tool_execution_failed"}:
            self.active_tool_records[key] = record
        return record

    def outcome_call(self, tool_id: str) -> object | None:
        calls = self.outcome_tool_calls.get(tool_id, [])
        return calls[0] if calls else None

    def provider_record(self, operation: str, terminal: bool) -> object | None:
        records = self.provider_records.get(operation, [])
        if operation in self.provider_active:
            record = self.provider_active[operation]
            if terminal:
                self.provider_active.pop(operation, None)
            return record
        if not records:
            return None
        record = records.pop(0)
        if not terminal:
            self.provider_active[operation] = record
        return record


def _convert_harness_event(
    event: object,
    *,
    trace: object | Mapping[str, Any],
    state: _ConversionState,
) -> ExecutionEventRecord:
    event_type = _required_string(event, "event_type").lower()
    event_id = _required_string(event, "event_id")
    sequence = _required_int(event, "sequence")
    timestamp = _required_datetime(event, "occurred_at")
    stage = _optional_string(event, "stage") or "runtime"
    raw_metadata = _value(event, "metadata")
    metadata = _safe_event_metadata(event_type, stage, raw_metadata)
    duration_ms = _duration_ms(event, raw_metadata)
    status = _event_status(event_type, raw_metadata)
    base: dict[str, Any] = {
        "event_id": event_id,
        "sequence": sequence,
        "timestamp": timestamp,
        "duration_ms": duration_ms,
        "status": status,
        "metadata": metadata,
    }

    if "provider_call" in event_type:
        operation = _string_or_default(_optional_string(raw_metadata, "operation"), stage)
        record = state.provider_record(
            operation, terminal=event_type.endswith("completed") or event_type.endswith("failed")
        )
        record_metadata = _value(record, "metadata") if record is not None else None
        if record is not None and duration_ms == 0:
            duration_ms = _number_as_int(_value(record_metadata, "latency_ms"))
            base["duration_ms"] = duration_ms
        usage = TokenUsage(
            input_tokens=_number_as_int(_value(record_metadata, "input_tokens")),
            output_tokens=_number_as_int(_value(record_metadata, "output_tokens")),
        )
        model = _string_or_default(_optional_string(record_metadata, "model"), "unknown-model")
        provider = _optional_string(record_metadata, "provider_id")
        error_type = (
            _optional_string(raw_metadata, "error_code") if event_type.endswith("failed") else None
        )
        return ModelCallEvent(
            **base,
            model=model,
            provider=provider,
            usage=usage,
            error_type=error_type,
        )

    if event_type in _TOOL_RESULT_EVENTS:
        tool_id = _string_or_default(_optional_string(raw_metadata, "tool_id"), "unknown-tool")
        tool_version = _string_or_default(_optional_string(raw_metadata, "tool_version"), "unknown")
        record = state.tool_record(tool_id, tool_version, event_type)
        if record is not None:
            if duration_ms == 0:
                duration_ms = _number_as_int(_value(record, "latency_ms"))
                base["duration_ms"] = duration_ms
            metadata.update(_safe_record_metadata(record))
        result_status = (
            _optional_string(raw_metadata, "result_status")
            or _optional_string(record, "status")
            or "failed"
        )
        is_success = result_status in {"succeeded", "success", "empty"}
        outcome_call = state.outcome_call(tool_id)
        arguments = _value(outcome_call, "arguments") if outcome_call is not None else {}
        evidence = _string_sequence(_value(outcome_call, "evidence_ids"))
        error_type = (
            None
            if is_success
            else (
                _optional_string(record, "error_code")
                or _optional_string(raw_metadata, "error_code")
                or _optional_string(raw_metadata, "reason_code")
                or "harness_tool_error"
            )
        )
        tool_base = {
            **base,
            "status": ExecutionEventStatus.SUCCESS if is_success else ExecutionEventStatus.ERROR,
        }
        return ToolCallEvent(
            **tool_base,
            input_summary=(
                f"{_optional_string(raw_metadata, 'argument_keys')} tool argument keys"
                if _optional_string(raw_metadata, "argument_keys")
                else None
            ),
            evidence_refs=evidence,
            call_id=_optional_string(outcome_call, "call_id") or event_id,
            name=tool_id,
            arguments=cast(dict[str, Any], arguments if isinstance(arguments, dict) else {}),
            outcome=ToolCallOutcome.SUCCESS if is_success else ToolCallOutcome.ERROR,
            result_summary=f"Harness tool result status: {result_status}",
            error_type=error_type,
            resource_id=_optional_string(raw_metadata, "resource"),
            tenant_id=_optional_string(trace, "tenant_id"),
            principal_id=_optional_string(trace, "principal_id"),
            authorization_granted=(
                _optional_string(raw_metadata, "allowed") == "true"
                if _optional_string(raw_metadata, "allowed") is not None
                else None
            ),
            idempotency_key_digest=_optional_string(record, "idempotency_key_digest"),
            retry_count=_number_as_int(_value(record, "retry_count")),
            timeout_seconds=_optional_positive_float(_value(record, "timeout_seconds")),
        )

    if event_type == "approval_requested":
        request_id = _string_or_default(_optional_string(raw_metadata, "request_id"), event_id)
        tool_name = _string_or_default(_optional_string(raw_metadata, "tool_id"), "unknown-tool")
        return ApprovalRequestEvent(
            **base,
            approval_id=request_id,
            action=f"tool:{tool_name}",
            requester=_optional_string(trace, "principal_id"),
            reason_summary=_optional_string(raw_metadata, "reason_code"),
            expires_at=_optional_datetime(raw_metadata, "expires_at"),
            evidence_refs=_approval_evidence(raw_metadata, request_id),
        )

    if event_type in _APPROVAL_DECISION_EVENTS:
        request_id = _string_or_default(_optional_string(raw_metadata, "request_id"), event_id)
        return ApprovalDecisionEvent(
            **base,
            approval_id=_string_or_default(
                _optional_string(raw_metadata, "approval_id"), request_id
            ),
            decision=_APPROVAL_DECISION_EVENTS[event_type],
            reviewer=_optional_string(raw_metadata, "decided_by"),
            reviewer_role=_optional_string(raw_metadata, "reviewer_role"),
            reason_summary=_optional_string(raw_metadata, "reason_code")
            or _optional_string(raw_metadata, "error_code"),
            evidence_refs=_approval_evidence(raw_metadata, request_id),
        )

    if event_type.startswith("delegation_"):
        child_execution_id = _optional_string(raw_metadata, "child_execution_id")
        delegation_base = {
            **base,
            "status": ExecutionEventStatus.COMPLETED
            if event_type.endswith("completed")
            else status,
        }
        return DelegationEvent(
            **delegation_base,
            delegation_id=_string_or_default(
                _optional_string(raw_metadata, "delegation_id"),
                _optional_string(trace, "delegation_id") or event_id,
            ),
            target_agent_id=_string_or_default(
                _optional_string(raw_metadata, "child_agent_id")
                or _optional_string(raw_metadata, "target_agent_id"),
                _optional_string(trace, "agent_id") or "unknown-agent",
            ),
            task_summary=None,
            source_agent_id=_optional_string(raw_metadata, "parent_agent_id"),
            child_execution_id=child_execution_id or _optional_string(trace, "execution_id"),
            authorized_tool_ids=_string_sequence(_value(raw_metadata, "authorized_tool_ids")),
            granted_permissions=_string_sequence(_value(raw_metadata, "granted_permissions")),
            context_checksum=_optional_string(raw_metadata, "context_checksum"),
            result_validated=(
                _optional_string(raw_metadata, "result_validated") == "true"
                if _optional_string(raw_metadata, "result_validated") is not None
                else None
            ),
        )

    if event_type in {"state_transitioned", "state_transition_failed"}:
        version = _optional_string(raw_metadata, "version")
        changed_paths = tuple(path for path in ("$.status", "$.version") if path)
        state_base = {
            **base,
            "status": ExecutionEventStatus.SUCCESS
            if event_type == "state_transitioned"
            else ExecutionEventStatus.ERROR,
        }
        return StateMutationEvent(
            **state_base,
            mutation_id=event_id,
            resource=_string_or_default(
                _optional_string(raw_metadata, "resource"), "workflow_state"
            ),
            operation="transition" if event_type == "state_transitioned" else "transition_failed",
            changed_paths=changed_paths if event_type == "state_transitioned" else (),
            after_state_ref=(
                f"state:{_optional_string(trace, 'execution_id')}:v{version}" if version else None
            ),
            transaction_id=_optional_string(raw_metadata, "transaction_id"),
        )

    if stage == "context" or event_type.startswith("state_read"):
        return StateReadEvent(
            **base,
            read_id=event_id,
            resource=_string_or_default(
                _optional_string(raw_metadata, "resource"), "workflow_state"
            ),
            result_summary="Harness state or context was read.",
        )

    if stage == "retrieval" or event_type.startswith("retrieval"):
        return RetrievalEvent(
            **base,
            retrieval_id=event_id,
            source=_string_or_default(
                _optional_string(raw_metadata, "source"), "harness-retrieval"
            ),
            result_count=_number_as_int(_value(raw_metadata, "result_count")),
            document_refs=_string_sequence(_value(raw_metadata, "document_refs")),
            tenant_id=_optional_string(trace, "tenant_id"),
            principal_id=_optional_string(trace, "principal_id"),
            authorized=(
                _optional_string(raw_metadata, "allowed") == "true"
                if _optional_string(raw_metadata, "allowed") is not None
                else None
            ),
            source_version=_optional_string(raw_metadata, "source_version"),
            retrieved_at=timestamp,
        )

    if stage == "external" or event_type.startswith("external") or event_type.startswith("event_"):
        direction = (
            ExternalEventDirection.RECEIVED
            if "received" in event_type
            else ExternalEventDirection.EMITTED
        )
        return ExternalEvent(
            **base,
            external_event_id=_string_or_default(
                _optional_string(raw_metadata, "external_event_id"), event_id
            ),
            source=_string_or_default(
                _optional_string(raw_metadata, "source"), "enterprise-agent-harness"
            ),
            name=_string_or_default(_optional_string(raw_metadata, "name"), event_type),
            direction=direction,
        )

    if stage == "human" or event_type.startswith("human_"):
        return HumanActionEvent(
            **base,
            action_id=event_id,
            actor_id=_string_or_default(
                _optional_string(raw_metadata, "actor_id")
                or _optional_string(raw_metadata, "decided_by"),
                _optional_string(trace, "principal_id") or "unknown-human",
            ),
            action=_string_or_default(_optional_string(raw_metadata, "action"), event_type),
            target=_optional_string(raw_metadata, "target"),
        )

    if event_type.endswith("_failed") or event_type in {
        "error",
        "budget_exhausted",
        "execution_timed_out",
        "execution_cancelled",
        "direct_injection_refused",
    }:
        return ErrorEvent(
            **base,
            error_type=_string_or_default(
                _optional_string(raw_metadata, "error_code")
                or _optional_string(raw_metadata, "reason_code"),
                event_type,
            ),
            message_summary=f"Harness reported {event_type}.",
            source_event_id=_optional_string(raw_metadata, "source_event_id"),
        )

    if event_type.startswith("message"):
        return MessageEvent(
            **base,
            message_id=event_id,
            role=_string_or_default(_optional_string(raw_metadata, "role"), "system"),
            channel=_optional_string(raw_metadata, "channel"),
        )

    workflow_id = _string_or_default(
        _optional_string(raw_metadata, "workflow_id"),
        (
            f"{_optional_string(trace, 'agent_id') or 'agent'}:"
            f"{_optional_string(trace, 'execution_id') or 'execution'}"
        ),
    )
    if event_type in {"policy_decision", "approval_policy_decision"}:
        base["evidence_refs"] = (event_id,)
    return WorkflowTransitionEvent(
        **base,
        workflow_id=workflow_id,
        from_state=_optional_string(raw_metadata, "from_state"),
        to_state=_string_or_default(
            _optional_string(raw_metadata, "to_state") or _optional_string(raw_metadata, "status"),
            stage,
        ),
        transition=event_type,
    )


def _trace_usage(metrics: object) -> TokenUsage:
    input_tokens = _number_as_int(_value(metrics, "total_input_tokens"))
    output_tokens = _number_as_int(_value(metrics, "total_output_tokens"))
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _trace_cost(metrics: object) -> float | None:
    value = _value(metrics, "total_cost")
    if value is None:
        return None
    try:
        converted = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return converted if isfinite(converted) and converted >= 0 else None


def _trace_trigger(trace: object | Mapping[str, Any]) -> TriggerInfo:
    event_id = _optional_string(trace, "event_id")
    trigger_id = _optional_string(trace, "trigger_id")
    return TriggerInfo(
        kind="event" if event_id or trigger_id else "harness_execution",
        source="enterprise-agent-harness",
        name=trigger_id,
        event_id=event_id or trigger_id,
    )


def _safe_trace_metadata(trace: object | Mapping[str, Any]) -> dict[str, str]:
    safe = {
        "harness_schema_version": _string_or_default(
            _optional_string(trace, "schema_version"), "unknown"
        ),
    }
    for key in (
        "correlation_id",
        "parent_execution_id",
        "delegation_id",
        "delegation_depth",
        "trigger_id",
        "causation_id",
        "attempt",
        "event_id",
        "final_status",
    ):
        value = _value(trace, key)
        if value is not None:
            safe[key] = str(value)
    return safe


def _safe_event_metadata(event_type: str, stage: str, metadata: object) -> dict[str, str]:
    result = {"harness_event_type": event_type, "harness_stage": stage}
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            normalized_key = str(key)
            if normalized_key not in _SAFE_TRACE_METADATA_KEYS:
                continue
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue
            text = str(value)
            if text:
                result[normalized_key] = text
    return result


def _safe_record_metadata(record: object) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in (
        "tool_id",
        "tool_version",
        "status",
        "attempts",
        "retry_count",
        "latency_ms",
        "timeout_seconds",
        "error_code",
        "idempotency_key_digest",
    ):
        value = _value(record, key)
        if value is not None:
            values[f"harness_{key}"] = str(value)
    return values


def _approval_evidence(metadata: object, request_id: str) -> tuple[str, ...]:
    values = [f"approval:{request_id}"]
    action_digest = _optional_string(metadata, "action_digest")
    if action_digest:
        values.append(f"approval-action:{action_digest}")
    return tuple(values)


def _event_status(event_type: str, metadata: object) -> ExecutionEventStatus:
    raw = _optional_string(metadata, "status")
    if raw is not None:
        aliases = {
            "succeeded": ExecutionEventStatus.SUCCESS,
            "success": ExecutionEventStatus.SUCCESS,
            "failed": ExecutionEventStatus.FAILED,
            "refused": ExecutionEventStatus.DENIED,
            "permission_denied": ExecutionEventStatus.DENIED,
            "approval_required": ExecutionEventStatus.PENDING,
        }
        if raw in aliases:
            return aliases[raw]
        try:
            return ExecutionEventStatus(raw)
        except ValueError:
            pass
    if event_type.endswith("_failed") or event_type.endswith("_refused"):
        return ExecutionEventStatus.ERROR
    if event_type == "approval_requested":
        return ExecutionEventStatus.REQUESTED
    if event_type == "approval_approved":
        return ExecutionEventStatus.APPROVED
    if event_type in {"approval_rejected", "approval_stale"}:
        return ExecutionEventStatus.REJECTED
    if event_type == "approval_expired":
        return ExecutionEventStatus.CANCELLED
    return ExecutionEventStatus.SUCCESS


def _duration_ms(event: object, metadata: object) -> int:
    direct = _value(metadata, "duration_ms")
    if direct is None:
        direct = _value(event, "duration_ms")
    return _number_as_int(direct)


def _number_as_int(value: object) -> int:
    try:
        number = float(cast(Any, value or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, int(round(number)))


def _optional_positive_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number > 0 else None


def _required_string(value: object, name: str) -> str:
    result = _optional_string(value, name)
    if result is None:
        raise HarnessIntegrationError(f"Harness object is missing {name}")
    return result


def _required_int(value: object, name: str) -> int:
    raw = _value(value, name)
    try:
        return int(cast(Any, raw))
    except (TypeError, ValueError) as exc:
        raise HarnessIntegrationError(f"Harness object has invalid {name}") from exc


def _required_datetime(value: object, name: str) -> datetime:
    raw = _value(value, name)
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise HarnessIntegrationError(f"Harness object has invalid {name}") from exc
    if not isinstance(raw, datetime):
        raise HarnessIntegrationError(f"Harness object is missing {name}")
    try:
        return require_aware_utc(raw)
    except ValueError as exc:
        raise HarnessIntegrationError(f"Harness object has invalid {name}") from exc


def _optional_datetime(value: object, name: str) -> datetime | None:
    raw = _value(value, name)
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if not isinstance(raw, datetime):
        return None
    return require_aware_utc(raw)


def _optional_string(value: object, name: str) -> str | None:
    raw = _value(value, name)
    if isinstance(raw, Enum):
        raw = raw.value
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _string_or_default(value: str | None, default: str) -> str:
    return value if value is not None else default


def _string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, Sequence):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _as_sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return ()
    return tuple(value)


def _sequence_value(value: object, name: str) -> tuple[object, ...]:
    return _as_sequence(_value(value, name))


def _value(value: object, name: str) -> object:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _risk_classification(value: object) -> ArtifactRiskClassification:
    raw = value.value if isinstance(value, Enum) else value
    try:
        return ArtifactRiskClassification(str(raw))
    except ValueError:
        return ArtifactRiskClassification.LOW


def _unique_registry_references(
    values: Sequence[HarnessRegistryReference | None],
) -> tuple[HarnessRegistryReference, ...]:
    result: list[HarnessRegistryReference] = []
    for value in values:
        if value is not None and value not in result:
            result.append(value)
    return tuple(result)


def ingest_harness_production_trace(
    store: LabStore,
    harness_trace: object | Mapping[str, Any],
    *,
    candidate_id: str | None = None,
    source_id: str = "enterprise-agent-harness",
    evaluator_results: tuple[EvaluationScore, ...] = (),
    operational_metadata: Mapping[str, str] | None = None,
    promotion_or_rollback_context: Mapping[str, str] | None = None,
    human_review_signals: tuple[ProductionSignal, ...] = (),
    incident_signals: tuple[ProductionSignal, ...] = (),
    received_at: datetime | None = None,
) -> ProductionIngestionResult:
    """Convert Harness evidence, then send it through the Lab import boundary."""

    trace = harness_run_trace_to_execution_trace(harness_trace, candidate_id=candidate_id)
    return ingest_production_trace(
        store,
        ProductionTraceEvidence(
            source_id=source_id,
            trace=trace,
            evaluator_results=evaluator_results,
            operational_metadata=dict(operational_metadata or {}),
            promotion_or_rollback_context=dict(promotion_or_rollback_context or {}),
            human_review_signals=human_review_signals,
            incident_signals=incident_signals,
            received_at=received_at or utc_now(),
        ),
    )


# Function aliases make the translation direction explicit at call sites.
convert_harness_run_trace = harness_run_trace_to_execution_trace
harness_trace_to_execution_trace = harness_run_trace_to_execution_trace
harness_environment_snapshot = collect_harness_environment_snapshot
agent_definition_to_candidate_artifact = harness_agent_definition_to_candidate_artifact
tool_definition_to_candidate_artifact = harness_tool_definition_to_candidate_artifact
capability_definition_to_candidate_artifact = harness_capability_definition_to_candidate_artifact
policy_definition_to_candidate_artifact = harness_policy_definition_to_candidate_artifact
approval_policy_to_candidate_artifact = harness_approval_policy_to_candidate_artifact


__all__ = [
    "EnterpriseAgentHarnessAdapter",
    "HarnessIntegrationError",
    "HarnessIntegrationUnavailableError",
    "agent_definition_to_candidate_artifact",
    "approval_policy_to_candidate_artifact",
    "capability_definition_to_candidate_artifact",
    "collect_harness_environment_snapshot",
    "convert_harness_run_trace",
    "harness_agent_definition_to_candidate_artifact",
    "harness_approval_policy_to_candidate_artifact",
    "harness_capability_definition_to_candidate_artifact",
    "harness_environment_snapshot",
    "ingest_harness_production_trace",
    "harness_policy_definition_to_candidate_artifact",
    "harness_run_trace_to_execution_trace",
    "harness_trace_to_execution_trace",
    "harness_tool_definition_to_candidate_artifact",
    "policy_definition_to_candidate_artifact",
    "tool_definition_to_candidate_artifact",
]
