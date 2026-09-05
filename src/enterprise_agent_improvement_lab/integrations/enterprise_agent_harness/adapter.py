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
from dataclasses import asdict, is_dataclass, replace
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
    CandidateArtifactReference,
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
    HarnessManifestProvenance,
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
        "prompt_ref",
        "skill_refs",
        "tool_refs",
        "policy_refs",
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
        "skill_id",
        "skill_version",
        "skill_selected",
        "skill_selection",
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
        integration boundary. When available, the deterministic Harness
        registry snapshot identity is retained alongside the Lab snapshot.
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
        resolved_registry = (
            registry if registry is not None else getattr(factory, "agent_registry", None)
        )
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
        prompt_registry = getattr(resolved_registry, "prompts", None)
        skill_registry = getattr(resolved_registry, "skills", None)
        tool_registry_version = _registry_version(tool_registry, default="unknown")
        prompt_registry_version = _registry_version(prompt_registry, default="unknown")
        skill_registry_version = _registry_version(skill_registry, default="unknown")

        tool_records = _registry_records(tool_registry, "descriptors")
        prompt_records = _registry_records(prompt_registry, "list")
        skill_records = _registry_records(skill_registry, "list")
        policy_records = _registry_policy_records(resolved_registry)
        prompt_hashes = _component_hashes(prompt_records, "prompt_id")
        skill_hashes = _component_hashes(skill_records, "skill_id")
        tool_hashes = _component_hashes(tool_records, "tool_id")
        policy_hashes = _component_hashes(policy_records, "policy_id")
        policy_registry = getattr(resolved_registry, "policy_registry", None)
        policy_registry_version = _registry_version(policy_registry, default="")
        if not policy_registry_version:
            policy_registry_version = _snapshot_sha256(policy_records)

        runtime_version = self._runtime_version or _runtime_version(harness)
        runtime_source = _built_manifest(built)
        manifest_provenance: HarnessManifestProvenance | None = None
        if runtime_source is not None:
            try:
                manifest_provenance = _manifest_provenance(
                    (lab_candidate.candidate_id if lab_candidate is not None else "harness-build"),
                    runtime_source,
                )
            except HarnessIntegrationError:
                # A structural test double may expose only an agent identity.
                # Full current Harness manifests are required for provenance.
                manifest_provenance = None
        registry_snapshot_id = (
            manifest_provenance.registry_snapshot_id
            if manifest_provenance is not None
            else _registry_snapshot_id(resolved_registry)
        )
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
            prompt_registry_version=prompt_registry_version,
            skill_registry_version=skill_registry_version,
            tool_registry_version=tool_registry_version,
            policy_registry_version=policy_registry_version,
            agent_definition_hash=agent_hash,
            prompt_hashes=prompt_hashes,
            skill_hashes=skill_hashes,
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
            registry_snapshot_id=registry_snapshot_id,
            resolved_manifest_id=(manifest_provenance.manifest_id if manifest_provenance else None),
            resolved_manifest_digest=(
                manifest_provenance.manifest_digest if manifest_provenance else None
            ),
            agent_ref=manifest_provenance.agent_ref if manifest_provenance else None,
            prompt_ref=manifest_provenance.prompt_ref if manifest_provenance else None,
            skill_refs=manifest_provenance.skill_refs if manifest_provenance else (),
            tool_refs=manifest_provenance.tool_refs if manifest_provenance else (),
            policy_refs=manifest_provenance.policy_refs if manifest_provenance else (),
        )

    def to_harness_candidate_definition(
        self,
        candidate: EnterpriseAgentCandidate,
        *,
        artifacts: Sequence[CandidateArtifact] = (),
        registry_references: Sequence[HarnessRegistryReference | Mapping[str, Any] | str] = (),
        registry: object | None = None,
        materialize: bool = True,
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
        """Create a current Harness ``AgentConfig`` for one Lab candidate.

        Prompt and skill candidate artifacts are materialized as immutable
        Harness definitions.  Registration is limited to the registry object
        supplied by the caller, which should be an evaluation-scoped registry.
        The Lab never imports Harness contracts into its core models.
        """

        harness = self._harness()
        resolved_artifacts = _validate_candidate_artifacts(candidate, artifacts)
        explicit_refs = tuple(_coerce_registry_reference(value) for value in registry_references)
        artifact_refs = tuple(
            _artifact_registry_reference(artifact) for artifact in resolved_artifacts
        )
        registry_refs = _registry_component_references(registry)
        agent_ref = HarnessRegistryReference(
            component_kind=HarnessComponentKind.AGENT,
            component_id=candidate.agent_id,
            version=_harness_version(candidate.agent_version or candidate.version),
            registry_id="agents",
        )
        all_refs = _unique_registry_references(
            (*explicit_refs, *artifact_refs, *registry_refs, agent_ref)
        )

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

        prompt_definition, prompt_ref = _resolve_prompt_component(
            candidate,
            resolved_artifacts,
            payload,
            all_refs,
            harness=harness,
            registry=registry,
            materialize=materialize,
        )
        payload["prompt_ref"] = _harness_component_reference(harness, prompt_ref)

        payload["tool_refs"], tool_refs = _component_references(
            HarnessComponentKind.TOOL,
            (*candidate.tools, *candidate.tool_bindings),
            payload.get("tool_refs", ()),
            all_refs,
            resolved_artifacts,
        )
        payload["skill_refs"], skill_refs = _component_references(
            HarnessComponentKind.SKILL,
            candidate.skills,
            payload.get("skill_refs", ()),
            all_refs,
            resolved_artifacts,
        )
        payload["policy_refs"], policy_refs = _component_references(
            HarnessComponentKind.POLICY,
            candidate.policies,
            payload.get("policy_refs", ()),
            all_refs,
            resolved_artifacts,
        )

        skill_definitions, skill_refs = _resolve_skill_components(
            candidate,
            resolved_artifacts,
            payload,
            all_refs,
            harness=harness,
            registry=registry,
            materialize=materialize,
            existing_refs=skill_refs,
        )
        # ComponentReference instances make the boundary explicit and avoid
        # relying on the Harness model's coercion of arbitrary dictionaries.
        payload["skill_refs"] = [
            _harness_component_reference(harness, reference) for reference in skill_refs
        ]
        payload["tool_refs"] = [
            _harness_component_reference(harness, reference) for reference in tool_refs
        ]
        payload["policy_refs"] = [
            _harness_component_reference(harness, reference) for reference in policy_refs
        ]

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
                *explicit_refs,
                *artifact_refs,
                agent_ref,
                prompt_ref,
                *tool_refs,
                *skill_refs,
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
            materialized_prompt=prompt_definition,
            materialized_skills=tuple(skill_definitions),
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
                registry=getattr(factory, "agent_registry", None),
            )
        )
        evaluation_registry = getattr(factory, "agent_registry", None)
        if evaluation_registry is not None:
            if definition.materialized_prompt is not None:
                _register_materialized_component(
                    evaluation_registry,
                    HarnessComponentKind.PROMPT,
                    definition.materialized_prompt,
                )
            for skill_definition in definition.materialized_skills:
                _register_materialized_component(
                    evaluation_registry,
                    HarnessComponentKind.SKILL,
                    skill_definition,
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
        manifest = getattr(built, "manifest", None)
        if manifest is not None:
            _validate_resolved_manifest(definition, manifest)
            provenance = _manifest_provenance(definition.candidate.candidate_id, manifest)
            identity = definition.runtime_identity.model_copy(
                update={
                    "manifest_id": provenance.manifest_id,
                    "manifest_digest": provenance.manifest_digest,
                    "registry_snapshot_id": provenance.registry_snapshot_id,
                }
            )
            definition = replace(definition, runtime_identity=identity, provenance=provenance)
        elif getattr(self._harness(), "ResolvedAgentManifest", None) is not None:
            raise HarnessIntegrationError(
                "Current Enterprise Agent Harness build did not expose ResolvedAgentManifest"
            )
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
            manifest=getattr(agent, "manifest", None),
            provenance=(built.provenance if isinstance(built, HarnessBuiltCandidate) else None),
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
            manifest=getattr(agent, "manifest", None),
            provenance=(built.provenance if isinstance(built, HarnessBuiltCandidate) else None),
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


def _validate_resolved_manifest(
    definition: HarnessCandidateDefinition,
    manifest: object,
) -> None:
    """Reject a Harness build whose resolved graph differs from candidate intent."""

    expected_agent = _value(definition.agent_config, "identity")
    actual_agent = _value(_value(manifest, "agent"), "identity") or _value(manifest, "agent")
    expected_identity = _identity_pair(expected_agent)
    actual_identity = _identity_pair(actual_agent)
    if expected_identity != actual_identity:
        raise HarnessIntegrationError(
            "Harness resolved manifest agent identity does not match candidate intent"
        )
    for field in ("prompt_ref", "skill_refs", "tool_refs", "policy_refs"):
        expected = _value(definition.agent_config, field)
        actual = _value(manifest, field)
        default_kind = "prompt" if field == "prompt_ref" else field.removesuffix("_refs")
        expected_ids = _reference_identities(expected, default_kind=default_kind)
        actual_ids = _reference_identities(actual, default_kind=default_kind)
        if field == "prompt_ref":
            expected_ids = expected_ids[:1]
            actual_ids = actual_ids[:1]
        if expected_ids != actual_ids:
            raise HarnessIntegrationError(
                f"Harness resolved manifest {field} do not match candidate intent"
            )
    for field, id_field, label in (
        ("provider_profile", "provider_id", "provider profile"),
        ("runtime_profile", "profile_id", "runtime profile"),
    ):
        expected = _component_pair_identity(_value(definition.agent_config, field), id_field)
        actual = _component_pair_identity(_value(manifest, field), id_field)
        if expected != actual:
            raise HarnessIntegrationError(
                f"Harness resolved manifest {label} does not match candidate intent"
            )


def _manifest_provenance(candidate_id: str, manifest: object) -> HarnessManifestProvenance:
    """Extract safe immutable provenance from a current Harness manifest."""

    manifest_id = _required_string(manifest, "manifest_id")
    manifest_digest = _required_string(manifest, "manifest_digest")
    registry_snapshot_id = _required_string(manifest, "registry_snapshot_id")
    agent = _value(manifest, "agent")
    agent_identity = _identity_pair(agent)
    if agent_identity is None:
        raise HarnessIntegrationError("Harness manifest is missing resolved agent identity")
    agent_id, agent_version = agent_identity
    prompt = _value(manifest, "prompt_ref")
    prompt_identity = _reference_identity(prompt, default_kind="prompt")
    if prompt_identity is None:
        raise HarnessIntegrationError("Harness manifest is missing resolved prompt reference")
    provider = _value(manifest, "provider_profile")
    provider_identity = _component_pair_identity(provider, "provider_id")
    runtime = _value(manifest, "runtime_profile")
    runtime_identity = _component_pair_identity(runtime, "profile_id")
    try:
        return HarnessManifestProvenance(
            candidate_id=candidate_id,
            manifest_id=manifest_id,
            manifest_digest=manifest_digest,
            registry_snapshot_id=registry_snapshot_id,
            agent_ref=f"agent:{agent_id}@{agent_version}",
            prompt_ref=prompt_identity,
            skill_refs=tuple(
                _reference_identities(_value(manifest, "skill_refs"), default_kind="skill")
            ),
            tool_refs=tuple(
                _reference_identities(_value(manifest, "tool_refs"), default_kind="tool")
            ),
            policy_refs=tuple(
                _reference_identities(_value(manifest, "policy_refs"), default_kind="policy")
            ),
            runtime_profile=runtime_identity,
            provider_profile=provider_identity,
        )
    except Exception as exc:  # noqa: BLE001 - typed contract boundary.
        raise HarnessIntegrationError("Harness resolved manifest provenance is invalid") from exc


def _identity_pair(value: object) -> tuple[str, str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if ":" in raw:
            _prefix, raw = raw.split(":", 1)
        raw_component_id, separator, raw_version = raw.rpartition("@")
        return (
            (raw_component_id, raw_version)
            if separator and raw_component_id and raw_version
            else None
        )
    identity = _value(value, "identity")
    if identity is not None and identity is not value:
        nested = _identity_pair(identity)
        if nested is not None:
            return nested
    component_id = (
        _optional_string(value, "agent_id")
        or _optional_string(value, "component_id")
        or _optional_string(value, "id")
    )
    version = _optional_string(value, "version") or _optional_string(value, "agent_version")
    return (component_id, version) if component_id and version else None


def _component_pair_identity(value: object, id_field: str) -> str | None:
    if isinstance(value, str):
        identity = _identity_pair(value)
        if identity is not None:
            return f"{identity[0]}@{identity[1]}"
    component_id = _optional_string(value, id_field) or _optional_string(value, "component_id")
    version = _optional_string(value, "version")
    if component_id and version:
        return f"{component_id}@{version}"
    return None


def _reference_identity(value: object, default_kind: str | None = None) -> str | None:
    registry_reference = _optional_string(value, "registry_reference") or _optional_string(
        value, "registry_ref"
    )
    if registry_reference:
        return registry_reference if "@" in registry_reference else None
    component_type = (
        _optional_string(value, "component_type")
        or _optional_string(value, "component_kind")
        or _optional_string(value, "kind")
    )
    component_id = (
        _optional_string(value, "component_id")
        or _optional_string(value, "id")
        or _optional_string(value, "artifact_id")
    )
    if component_id is None:
        for field in ("prompt_id", "skill_id", "tool_id", "policy_id", "agent_id"):
            component_id = _optional_string(value, field)
            if component_id:
                if component_type is None:
                    component_type = field.removesuffix("_id")
                break
    version = _optional_string(value, "version") or _optional_string(value, "component_version")
    if component_id and version:
        prefix = f"{component_type}:" if component_type else ""
        return f"{prefix}{component_id}@{version}"
    if isinstance(value, str) and "@" in value:
        raw = value.strip()
        if not raw:
            return None
        if ":" not in raw and default_kind:
            return f"{default_kind}:{raw}"
        return raw
    return None


def _reference_identities(value: object, default_kind: str | None = None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        one = _reference_identity(value, default_kind=default_kind)
        return (one,) if one else ()
    if isinstance(value, Sequence):
        return tuple(
            identity
            for item in value
            if (identity := _reference_identity(item, default_kind=default_kind)) is not None
        )
    one = _reference_identity(value, default_kind=default_kind)
    return (one,) if one else ()


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
        revision = _value(registry, "registry_version")
    if revision is None:
        revision = _value(registry, "version")
    if revision is None:
        return default
    return str(revision)


def _registry_snapshot_id(registry: object | None) -> str | None:
    """Return a deterministic public registry snapshot identity when available."""

    snapshot = getattr(registry, "snapshot", None)
    if not callable(snapshot):
        return None
    try:
        value = snapshot(include_inactive=True)
    except TypeError:
        try:
            value = snapshot()
        except Exception:  # noqa: BLE001 - optional registry boundary.
            return None
    except Exception:  # noqa: BLE001 - optional registry boundary.
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return (
        _optional_string(value, "snapshot_id")
        or _optional_string(value, "identity")
        or _optional_string(registry, "snapshot_id")
    )


def _registry_records(registry: object | None, method_name: str) -> tuple[object, ...]:
    if isinstance(registry, Sequence) and not isinstance(registry, (str, bytes)):
        return tuple(registry)
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
    # Harness versions expose policies either as a plain sequence or as a
    # registry object with the same list/descriptors surface as prompts and
    # skills.  Keep both forms on the adapter boundary.
    records = _registry_records(values, "list")
    if records:
        return records
    records = _registry_records(values, "descriptors")
    if records:
        return records
    if callable(values):
        try:
            values = values(include_inactive=True)
        except TypeError:
            values = values()
    return _as_sequence(values)


def _registry_component_references(
    registry: object | None,
) -> tuple[HarnessRegistryReference, ...]:
    """Read exact component identities exposed by an evaluation registry.

    Candidates may use stable Lab-side IDs.  When the supplied Harness
    registry contains exactly one version for that ID, these references let
    the adapter resolve it without copying registry payloads into Lab
    contracts.  Multiple versions remain ambiguous and are rejected by the
    normal exact-reference resolver.
    """

    if registry is None:
        return ()
    result: list[HarnessRegistryReference] = []
    for kind, collection_name, method_name, id_field in (
        (HarnessComponentKind.PROMPT, "prompts", "list", "prompt_id"),
        (HarnessComponentKind.SKILL, "skills", "list", "skill_id"),
        (HarnessComponentKind.TOOL, "tools", "descriptors", "tool_id"),
    ):
        collection = getattr(registry, collection_name, None)
        for record in _registry_records(collection, method_name):
            component_id = _optional_string(record, id_field)
            version = _optional_string(record, "version")
            if component_id is None or version is None:
                continue
            try:
                result.append(
                    HarnessRegistryReference(
                        component_kind=kind,
                        component_id=component_id,
                        version=version,
                        registry_id=collection_name,
                    )
                )
            except Exception:  # noqa: BLE001 - malformed optional registry record.
                continue
    for record in _registry_policy_records(registry):
        component_id = _optional_string(record, "policy_id")
        version = _optional_string(record, "version")
        if component_id is None or version is None:
            continue
        try:
            result.append(
                HarnessRegistryReference(
                    component_kind=HarnessComponentKind.POLICY,
                    component_id=component_id,
                    version=version,
                    registry_id="policies",
                )
            )
        except Exception:  # noqa: BLE001 - malformed optional registry record.
            continue
    return _unique_registry_references(result)


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
    manifest: object | None = None,
    provenance: HarnessManifestProvenance | None = None,
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

    provenance_source = provenance if provenance is not None else manifest
    if provenance_source is not None:
        nested_manifest = _value(provenance_source, "manifest")
        if nested_manifest is not None:
            provenance_source = nested_manifest
        nested_provenance = _value(provenance_source, "provenance")
        if nested_provenance is not None:
            provenance_source = nested_provenance
    manifest_id = _optional_string(trace, "manifest_id") or _optional_string(
        provenance_source, "manifest_id"
    )
    manifest_digest = _optional_string(trace, "manifest_digest") or _optional_string(
        provenance_source, "manifest_digest"
    )
    registry_snapshot_id = _optional_string(trace, "registry_snapshot_id") or _optional_string(
        provenance_source, "registry_snapshot_id"
    )
    prompt_source = _value(trace, "prompt_ref") or _value(provenance_source, "prompt_ref")
    skill_source = _value(trace, "skill_refs") or _value(provenance_source, "skill_refs")
    prompt_ref = _reference_identity(prompt_source, default_kind="prompt")
    skill_refs = _reference_identities(skill_source, default_kind="skill")
    if manifest_id:
        metadata["manifest_id"] = manifest_id
    if manifest_digest:
        metadata["manifest_digest"] = manifest_digest
    if registry_snapshot_id:
        metadata["registry_snapshot_id"] = registry_snapshot_id
    if prompt_ref:
        metadata["prompt_ref"] = prompt_ref
    if skill_refs:
        metadata["skill_refs"] = ",".join(skill_refs)

    return ExecutionTrace(
        execution_id=execution_id,
        agent_id=agent_id,
        agent_version=agent_version,
        candidate_id=resolved_candidate_id,
        case_id=case_id,
        session_id=_optional_string(trace, "session_id"),
        principal_id=_optional_string(trace, "principal_id"),
        tenant_id=_optional_string(trace, "tenant_id"),
        manifest_id=manifest_id,
        manifest_digest=manifest_digest,
        registry_snapshot_id=registry_snapshot_id,
        prompt_ref=prompt_ref,
        skill_refs=skill_refs,
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


def harness_prompt_definition_to_candidate_artifact(
    definition: object | Mapping[str, Any],
    *,
    artifact_id: str | None = None,
    created_at: datetime | None = None,
    provenance: ArtifactProvenance | None = None,
) -> CandidateArtifact:
    """Store a typed Harness prompt definition as a Lab prompt artifact."""

    payload = _external_data(definition)
    component_id, version = _component_identity(payload, fallback_id=artifact_id)
    return _component_artifact(
        artifact_id=artifact_id or component_id,
        name=f"Harness prompt {component_id}",
        version=version,
        kind=CandidateArtifactKind.SYSTEM_PROMPT,
        payload=payload,
        owner=_string_or_default(payload.get("owner_id"), "application"),
        provenance=provenance,
        created_at=created_at,
        registry_reference=f"prompt:{component_id}@{version}",
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


def harness_skill_definition_to_candidate_artifact(
    definition: object | Mapping[str, Any],
    *,
    artifact_id: str | None = None,
    created_at: datetime | None = None,
    provenance: ArtifactProvenance | None = None,
) -> CandidateArtifact:
    """Store a typed Harness skill definition as an artifact."""

    payload = _external_data(definition)
    component_id, version = _component_identity(payload, fallback_id=artifact_id)
    return _component_artifact(
        artifact_id=artifact_id or component_id,
        name=f"Harness skill {component_id}",
        version=version,
        kind=CandidateArtifactKind.SKILL_CONFIGURATION,
        payload=payload,
        owner=_string_or_default(payload.get("owner_id"), "application"),
        risk=_risk_classification(payload.get("risk_level")),
        provenance=provenance,
        created_at=created_at,
        registry_reference=f"skill:{component_id}@{version}",
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
        payload = dict(value)
        if "component_kind" not in payload:
            for alias in ("component_type", "kind", "type"):
                if alias in payload:
                    payload["component_kind"] = payload[alias]
                    break
        return HarnessRegistryReference.model_validate(payload)
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
        raw_kind = (
            _optional_string(value, "component_kind")
            or _optional_string(value, "component_type")
            or _optional_string(value, "kind")
        )
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
        payload = _json_object_or_empty(artifact.content)
        kind = _artifact_component_kind(artifact.kind)
        if kind is None:
            return None
        component_id = _optional_string(payload, "component_id") or _optional_string(
            payload, _component_id_field(kind)
        )
        if component_id is None and kind == HarnessComponentKind.PROMPT:
            component_id = _safe_component_id(artifact.artifact_id)
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
    if _harness_version(version) != _harness_version(artifact.version):
        raise HarnessIntegrationError(
            f"artifact {artifact.artifact_id!r} registry reference version does not "
            "match the artifact version"
        )
    return HarnessRegistryReference(
        component_kind=kind,
        component_id=component_id,
        version=version,
        source_artifact_id=artifact.artifact_id,
    )


def _resolve_prompt_component(
    candidate: EnterpriseAgentCandidate,
    artifacts: Sequence[CandidateArtifact],
    payload: Mapping[str, Any],
    known_refs: Sequence[HarnessRegistryReference],
    *,
    harness: object,
    registry: object | None,
    materialize: bool,
) -> tuple[object | None, HarnessRegistryReference]:
    """Resolve one exact prompt and materialize a Lab prompt artifact if needed."""

    prompt_artifact = _select_prompt_artifact(candidate, artifacts)
    requested: object | None = candidate.prompt_ref
    if requested is None:
        requested = _value(payload, "prompt_ref")
    if requested is None and prompt_artifact is not None:
        requested = prompt_artifact
    if prompt_artifact is not None and materialize:
        definition, reference = _materialize_prompt_definition(
            prompt_artifact,
            candidate_id=candidate.candidate_id,
            harness=harness,
        )
        _register_materialized_component(registry, HarnessComponentKind.PROMPT, definition)
        return definition, reference
    if requested is None:
        raise HarnessIntegrationError(
            "candidate needs one exact prompt reference or a prompt candidate artifact"
        )
    reference = _resolve_component_reference(
        requested,
        HarnessComponentKind.PROMPT,
        known_refs,
        artifacts,
    )
    return None, reference


def _select_prompt_artifact(
    candidate: EnterpriseAgentCandidate,
    artifacts: Sequence[CandidateArtifact],
) -> CandidateArtifact | None:
    prompt_kinds = {
        CandidateArtifactKind.SYSTEM_PROMPT,
        CandidateArtifactKind.DEVELOPER_PROMPT,
        CandidateArtifactKind.USER_TEMPLATE,
    }
    prompt_artifacts = [artifact for artifact in artifacts if artifact.kind in prompt_kinds]
    if not prompt_artifacts:
        return None
    if candidate.prompt_ref is not None:
        for artifact in prompt_artifacts:
            if artifact.artifact_id == candidate.prompt_ref.artifact_id:
                _validate_artifact_reference(candidate.prompt_ref, artifact)
                return artifact
            if (
                candidate.prompt_ref.registry_reference is not None
                and artifact.registry_reference == candidate.prompt_ref.registry_reference
            ):
                _validate_artifact_reference(candidate.prompt_ref, artifact)
                return artifact
        raise HarnessIntegrationError(
            f"prompt reference {candidate.prompt_ref.artifact_id!r} has no candidate artifact"
        )
    # Prefer the explicit system prompt, then preserve declaration order for
    # a candidate that uses one prompt artifact of another Lab kind.
    return next(
        (
            artifact
            for artifact in prompt_artifacts
            if artifact.kind == CandidateArtifactKind.SYSTEM_PROMPT
        ),
        prompt_artifacts[0],
    )


def _materialize_prompt_definition(
    artifact: CandidateArtifact,
    *,
    candidate_id: str,
    harness: object,
) -> tuple[object, HarnessRegistryReference]:
    """Create an immutable Harness PromptDefinition from Lab prompt evidence."""

    reference = _artifact_registry_reference(artifact)
    if reference is None or reference.component_kind != HarnessComponentKind.PROMPT:
        component_id = _safe_component_id(artifact.artifact_id)
        reference = HarnessRegistryReference(
            component_kind=HarnessComponentKind.PROMPT,
            component_id=component_id,
            version=artifact.version,
            source_artifact_id=artifact.artifact_id,
        )
    payload = _json_object_or_empty(artifact.content)
    _validate_materialized_identity(payload, reference, "prompt_id")
    instructions = payload.get("instructions") or payload.get("content") or artifact.content
    purpose = payload.get("purpose") or artifact.name
    owner_id = payload.get("owner_id") or artifact.owner
    lifecycle = payload.get("lifecycle") or "active"
    prompt_type = getattr(harness, "PromptDefinition", None)
    if prompt_type is None:
        raise HarnessIntegrationError("Enterprise Agent Harness does not expose PromptDefinition")
    lifecycle_type = getattr(harness, "AgentLifecycleStatus", None)
    lifecycle_value: object = lifecycle
    if lifecycle_type is not None:
        try:
            lifecycle_value = lifecycle_type(str(lifecycle))
        except (TypeError, ValueError):
            lifecycle_value = lifecycle_type.ACTIVE
    metadata: dict[str, Any] = {}
    metadata.update(_safe_metadata_values(payload.get("metadata")))
    metadata.update(_safe_metadata_values(artifact.metadata))
    metadata.update(
        {
            "lab_candidate_id": candidate_id,
            "lab_artifact_id": artifact.artifact_id,
            "lab_artifact_sha256": artifact.checksum,
            "lab_artifact_kind": artifact.kind.value,
            "lab_parent_artifact_id": artifact.provenance.parent_artifact_id or "",
        }
    )
    values = {
        "prompt_id": reference.component_id,
        "version": _harness_version(reference.version),
        "purpose": str(purpose),
        "instructions": str(instructions),
        "owner_id": str(owner_id),
        "lifecycle": lifecycle_value,
        "metadata": metadata,
    }
    try:
        definition = prompt_type.model_validate(values)
    except Exception as exc:  # noqa: BLE001 - external contract boundary.
        raise HarnessIntegrationError(f"Prompt artifact could not be materialized: {exc}") from exc
    return definition, reference.model_copy(update={"version": _harness_version(reference.version)})


def _resolve_skill_components(
    candidate: EnterpriseAgentCandidate,
    artifacts: Sequence[CandidateArtifact],
    payload: Mapping[str, Any],
    known_refs: Sequence[HarnessRegistryReference],
    *,
    harness: object,
    registry: object | None,
    materialize: bool,
    existing_refs: Sequence[HarnessRegistryReference],
) -> tuple[tuple[object, ...], tuple[HarnessRegistryReference, ...]]:
    """Resolve skills and materialize candidate SkillDefinition artifacts."""

    values = (
        tuple(candidate.skills) if candidate.skills else _as_sequence(_value(payload, "skill_refs"))
    )
    definitions: list[object] = []
    references: list[HarnessRegistryReference] = list(existing_refs)
    skill_artifacts = tuple(
        artifact
        for artifact in artifacts
        if artifact.kind == CandidateArtifactKind.SKILL_CONFIGURATION
    )
    for value in values:
        reference = _resolve_component_reference(
            value,
            HarnessComponentKind.SKILL,
            known_refs,
            artifacts,
        )
        artifact = _skill_artifact_for_reference(reference, skill_artifacts)
        if artifact is not None and materialize:
            definition, reference = _materialize_skill_definition(
                artifact,
                candidate_id=candidate.candidate_id,
                harness=harness,
                known_refs=known_refs,
                artifacts=artifacts,
            )
            _register_materialized_component(registry, HarnessComponentKind.SKILL, definition)
            definitions.append(definition)
        if reference not in references:
            references.append(reference)
    return tuple(definitions), tuple(references)


def _skill_artifact_for_reference(
    reference: HarnessRegistryReference,
    artifacts: Sequence[CandidateArtifact],
) -> CandidateArtifact | None:
    for artifact in artifacts:
        candidate_reference = _artifact_registry_reference(artifact)
        if candidate_reference is not None and candidate_reference == reference:
            return artifact
    return None


def _materialize_skill_definition(
    artifact: CandidateArtifact,
    *,
    candidate_id: str,
    harness: object,
    known_refs: Sequence[HarnessRegistryReference],
    artifacts: Sequence[CandidateArtifact],
) -> tuple[object, HarnessRegistryReference]:
    """Create an immutable Harness SkillDefinition from Lab skill evidence."""

    reference = _artifact_registry_reference(artifact)
    if reference is None or reference.component_kind != HarnessComponentKind.SKILL:
        payload = _json_object_or_empty(artifact.content)
        component_id = _safe_component_id(
            _optional_string(payload, "skill_id") or artifact.artifact_id
        )
        reference = HarnessRegistryReference(
            component_kind=HarnessComponentKind.SKILL,
            component_id=component_id,
            version=artifact.version,
            source_artifact_id=artifact.artifact_id,
        )
    payload = _json_object_or_empty(artifact.content)
    _validate_materialized_identity(payload, reference, "skill_id")
    definition_type = getattr(harness, "SkillDefinition", None)
    component_reference_type = getattr(harness, "ComponentReference", None)
    if definition_type is None or component_reference_type is None:
        raise HarnessIntegrationError("Enterprise Agent Harness does not expose SkillDefinition")
    required = _materialized_tool_references(
        payload.get("required_tool_refs", payload.get("required_tools", ())),
        harness=harness,
        known_refs=known_refs,
        artifacts=artifacts,
    )
    optional = _materialized_tool_references(
        payload.get("optional_tool_refs", payload.get("optional_tools", ())),
        harness=harness,
        known_refs=known_refs,
        artifacts=artifacts,
    )
    lifecycle = payload.get("lifecycle", "active")
    lifecycle_type = getattr(harness, "AgentLifecycleStatus", None)
    if lifecycle_type is not None:
        try:
            lifecycle = lifecycle_type(str(lifecycle))
        except (TypeError, ValueError):
            lifecycle = lifecycle_type.ACTIVE
    risk = payload.get("risk_level", payload.get("risk", "low"))
    risk_type = getattr(harness, "RiskLevel", None)
    if risk_type is not None:
        try:
            risk = risk_type(str(risk))
        except (TypeError, ValueError):
            risk = risk_type.LOW
    metadata: dict[str, Any] = {}
    metadata.update(_safe_metadata_values(payload.get("metadata")))
    metadata.update(_safe_metadata_values(artifact.metadata))
    metadata.update(
        {
            "lab_candidate_id": candidate_id,
            "lab_artifact_id": artifact.artifact_id,
            "lab_artifact_sha256": artifact.checksum,
            "lab_artifact_kind": artifact.kind.value,
            "lab_parent_artifact_id": artifact.provenance.parent_artifact_id or "",
        }
    )
    values = {
        "skill_id": reference.component_id,
        "version": _harness_version(reference.version),
        "name": str(payload.get("name") or artifact.name),
        "description": str(payload.get("description") or artifact.name),
        "supported_operations": _string_sequence(payload.get("supported_operations", ())),
        "supported_intents": _string_sequence(payload.get("supported_intents", ())),
        "supported_languages": _string_sequence(payload.get("supported_languages", ())),
        "required_tool_refs": list(required),
        "optional_tool_refs": list(optional),
        "risk_level": risk,
        "owner_id": str(payload.get("owner_id") or artifact.owner),
        "lifecycle": lifecycle,
        "tags": _string_sequence(payload.get("tags", ())),
        "metadata": metadata,
    }
    try:
        definition = definition_type.model_validate(values)
    except Exception as exc:  # noqa: BLE001 - external contract boundary.
        raise HarnessIntegrationError(f"Skill artifact could not be materialized: {exc}") from exc
    return definition, reference.model_copy(update={"version": _harness_version(reference.version)})


def _materialized_tool_references(
    values: object,
    *,
    harness: object,
    known_refs: Sequence[HarnessRegistryReference],
    artifacts: Sequence[CandidateArtifact],
) -> tuple[object, ...]:
    component_reference_type = getattr(harness, "ComponentReference", None)
    if component_reference_type is None:
        raise HarnessIntegrationError("Enterprise Agent Harness does not expose ComponentReference")
    result: list[object] = []
    for value in _as_sequence(values):
        reference = _resolve_component_reference(
            value,
            HarnessComponentKind.TOOL,
            known_refs,
            artifacts,
        )
        typed = _harness_component_reference(harness, reference)
        if typed not in result:
            result.append(typed)
    return tuple(result)


def _validate_materialized_identity(
    payload: Mapping[str, Any],
    reference: HarnessRegistryReference,
    id_field: str,
) -> None:
    """Reject stale identity fields embedded in candidate artifact content."""

    payload_id = _optional_string(payload, id_field) or _optional_string(payload, "component_id")
    if payload_id is not None and payload_id != reference.component_id:
        raise HarnessIntegrationError(
            f"candidate {reference.component_kind.value} artifact identity does not match "
            "its registry reference"
        )
    payload_version = _optional_string(payload, "version")
    if payload_version is not None and _harness_version(payload_version) != _harness_version(
        reference.version
    ):
        raise HarnessIntegrationError(
            f"candidate {reference.component_kind.value} artifact version does not match "
            "its registry reference"
        )


def _harness_component_reference(
    harness: object,
    reference: HarnessRegistryReference,
) -> object:
    component_reference_type = getattr(harness, "ComponentReference", None)
    component_type = getattr(harness, "ComponentType", None)
    if component_reference_type is None or component_type is None:
        raise HarnessIntegrationError(
            "Enterprise Agent Harness does not expose component references"
        )
    try:
        typed_kind = component_type(reference.component_kind.value)
        return component_reference_type(
            component_type=typed_kind,
            component_id=reference.component_id,
            version=_harness_version(reference.version),
        )
    except Exception as exc:  # noqa: BLE001 - external contract boundary.
        raise HarnessIntegrationError(
            f"Could not create Harness {reference.component_kind.value} reference: {exc}"
        ) from exc


def _register_materialized_component(
    registry: object | None,
    kind: HarnessComponentKind,
    definition: object,
) -> None:
    """Register a candidate artifact only in the supplied evaluation registry."""

    if registry is None:
        return
    target = getattr(registry, f"{kind.value}s", None)
    if target is None and kind == HarnessComponentKind.POLICY:
        target = registry
    register = getattr(target, "register", None)
    if not callable(register):
        return
    component_id = _optional_string(definition, _component_id_field(kind))
    version = _optional_string(definition, "version")
    getter = getattr(target, "get", None)
    if callable(getter) and component_id and version:
        try:
            existing = getter(component_id, version)
        except Exception:  # noqa: BLE001 - absent exact version.
            existing = None
        if existing is not None:
            if _external_value(existing) != _external_value(definition):
                raise HarnessIntegrationError(
                    f"evaluation registry contains a different {kind.value} definition "
                    f"for {component_id}@{version}"
                )
            return
    try:
        register(definition)
    except Exception as exc:  # noqa: BLE001 - external registry boundary.
        # A concurrent or repeated registration is safe only if the exact
        # immutable definition is already present.
        if callable(getter) and component_id and version:
            try:
                existing = getter(component_id, version)
            except Exception:
                existing = None
            if existing is not None and _external_value(existing) == _external_value(definition):
                return
        raise HarnessIntegrationError(
            f"could not register candidate {kind.value} artifact: {exc}"
        ) from exc


def _json_object_or_empty(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _safe_component_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return normalized or "candidate-component"


def _is_sensitive_key(value: str) -> bool:
    normalized = value.strip().lower().replace("-", "_")
    return any(
        part in normalized
        for part in (
            "secret",
            "password",
            "token",
            "credential",
            "authorization",
            "api_key",
            "private_key",
        )
    )


def _safe_metadata_values(value: object) -> dict[str, Any]:
    """Keep scalar, non-secret metadata while crossing into Harness."""

    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, (str, int, float, bool))
        and not _is_sensitive_key(str(key))
        and not str(key).startswith("lab_")
    }


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
    seen: set[tuple[HarnessComponentKind, str, str]] = set()
    for value in values:
        resolved = _resolve_component_reference(value, kind, known_refs, artifacts)
        key = _registry_reference_key(resolved)
        if key not in seen:
            seen.add(key)
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
    component_id: str | None = None
    version: str | None = None
    declared_kind = _declared_component_kind(value)
    if declared_kind is not None and declared_kind != kind:
        raise HarnessIntegrationError(
            f"Reference identifies a {declared_kind.value} component, not {kind.value}"
        )
    if isinstance(value, CandidateArtifact):
        artifact_reference = _artifact_registry_reference(value)
        if artifact_reference is not None:
            if artifact_reference.component_kind != kind:
                raise HarnessIntegrationError(
                    f"Artifact {value.artifact_id!r} is not a {kind.value} component"
                )
            return artifact_reference
        component_id = value.artifact_id
        version = value.version
    elif isinstance(value, CandidateArtifactReference):
        raw_registry = value.registry_reference
        if raw_registry:
            parsed_kind, component_id, version = _parse_reference_string(raw_registry)
            if parsed_kind != kind:
                raise HarnessIntegrationError(
                    f"Reference {raw_registry!r} is for {parsed_kind.value}, not {kind.value}"
                )
            return HarnessRegistryReference(
                component_kind=kind,
                component_id=component_id,
                version=version,
                source_artifact_id=value.artifact_id,
            )
        # A Lab reference can pin an artifact ID without carrying the Harness
        # registry identity.  Prefer the exact lineage exposed by the supplied
        # evaluation registry or artifact body before treating the ID as a
        # Harness component ID.
        lineage_matches = [
            ref
            for ref in (*known_refs, *(_artifact_registry_reference(item) for item in artifacts))
            if ref is not None
            and ref.component_kind == kind
            and ref.source_artifact_id == value.artifact_id
            and (
                value.version is None
                or _harness_version(ref.version) == _harness_version(value.version)
            )
        ]
        unique_lineage = _unique_registry_references(lineage_matches)
        if len(unique_lineage) == 1:
            return unique_lineage[0]
        component_id = value.artifact_id
        version = value.version
    elif hasattr(value, "artifact_id") and not isinstance(value, Mapping):
        raw_registry = _optional_string(value, "registry_reference")
        if raw_registry:
            parsed_kind, component_id, version = _parse_reference_string(raw_registry)
            if parsed_kind != kind:
                raise HarnessIntegrationError(
                    f"Reference {raw_registry!r} is for {parsed_kind.value}, not {kind.value}"
                )
            return HarnessRegistryReference(
                component_kind=kind,
                component_id=component_id,
                version=version,
                source_artifact_id=_optional_string(value, "artifact_id"),
            )
        component_id = _optional_string(value, "artifact_id")
        version = _optional_string(value, "version")
        if component_id and version:
            return HarnessRegistryReference(
                component_kind=kind,
                component_id=component_id,
                version=version,
            )
    elif isinstance(value, Mapping):
        component_id = (
            _optional_string(value, "component_id")
            or _optional_string(value, "id")
            or _optional_string(value, f"{kind.value}_id")
            or _optional_string(value, "artifact_id")
        )
        version = _optional_string(value, "version")
        registry_reference = _optional_string(value, "registry_reference")
        if registry_reference:
            parsed_kind, parsed_id, parsed_version = _parse_reference_string(registry_reference)
            if parsed_kind != kind:
                raise HarnessIntegrationError(
                    f"Reference {registry_reference!r} is for {parsed_kind.value}, not {kind.value}"
                )
            return HarnessRegistryReference(
                component_kind=kind,
                component_id=parsed_id,
                version=parsed_version,
                source_artifact_id=_optional_string(value, "artifact_id"),
            )
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
        component_id = component_id or _optional_string(value, "artifact_id")
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


def _declared_component_kind(value: object) -> HarnessComponentKind | None:
    """Read an optional kind declaration from a Lab or boundary reference."""

    raw_kind = _value(value, "component_kind") or _value(value, "component_type")
    if raw_kind is None:
        raw_kind = _value(value, "kind")
    if raw_kind is None:
        return None
    if isinstance(raw_kind, Enum):
        raw_kind = raw_kind.value
    try:
        return HarnessComponentKind(str(raw_kind))
    except ValueError:
        try:
            candidate_kind = CandidateArtifactKind(str(raw_kind))
        except ValueError:
            return None
        return _artifact_component_kind(candidate_kind)


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
            return {"profile_id": component_id, "version": _harness_version(version)}
    if isinstance(value, str):
        if "@" in value:
            kind, component_id, version = _parse_reference_string(
                value, default_kind=HarnessComponentKind.RUNTIME_PROFILE
            )
            if kind != HarnessComponentKind.RUNTIME_PROFILE:
                raise HarnessIntegrationError(
                    f"Runtime profile reference {value!r} has kind {kind.value!r}"
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
    return {"profile_id": component_id, "version": _harness_version(unique[0].version)}


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
        _validate_artifact_reference(reference, matched_artifact)
    return tuple(artifacts)


def _validate_artifact_reference(
    reference: CandidateArtifactReference,
    artifact: CandidateArtifact,
) -> None:
    """Ensure a supplied artifact preserves every pinned lineage field."""

    if reference.kind is not None and reference.kind != artifact.kind:
        raise HarnessIntegrationError(
            f"artifact reference {reference.artifact_id!r} does not match its kind"
        )
    if reference.version is not None and _harness_version(reference.version) != _harness_version(
        artifact.version
    ):
        raise HarnessIntegrationError(
            f"artifact reference {reference.artifact_id!r} does not match its version"
        )
    if reference.content_sha256 is not None and reference.content_sha256 != artifact.checksum:
        raise HarnessIntegrationError(
            f"artifact reference {reference.artifact_id!r} does not match its checksum"
        )
    if reference.registry_reference is None:
        return
    if artifact.registry_reference is None:
        raise HarnessIntegrationError(
            f"artifact reference {reference.artifact_id!r} has no matching registry lineage"
        )
    try:
        expected = _canonical_registry_reference(reference.registry_reference)
        actual = _canonical_registry_reference(artifact.registry_reference)
    except HarnessIntegrationError as exc:
        raise HarnessIntegrationError(
            f"artifact reference {reference.artifact_id!r} has an invalid registry lineage"
        ) from exc
    if expected != actual:
        raise HarnessIntegrationError(
            f"artifact reference {reference.artifact_id!r} does not match its registry lineage"
        )
    if actual[2] != _harness_version(artifact.version):
        raise HarnessIntegrationError(
            f"artifact reference {reference.artifact_id!r} has a stale registry version"
        )


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
        or _optional_string(identity_data, "prompt_id")
        or _optional_string(identity_data, "tool_id")
        or _optional_string(identity_data, "skill_id")
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
        CandidateArtifactKind.SKILL_CONFIGURATION: HarnessComponentKind.SKILL,
        CandidateArtifactKind.SYSTEM_PROMPT: HarnessComponentKind.PROMPT,
        CandidateArtifactKind.DEVELOPER_PROMPT: HarnessComponentKind.PROMPT,
        CandidateArtifactKind.USER_TEMPLATE: HarnessComponentKind.PROMPT,
        CandidateArtifactKind.POLICY: HarnessComponentKind.POLICY,
        CandidateArtifactKind.APPROVAL_POLICY: HarnessComponentKind.APPROVAL_POLICY,
    }.get(kind)


def _component_id_field(kind: HarnessComponentKind) -> str:
    return {
        HarnessComponentKind.AGENT: "agent_id",
        HarnessComponentKind.TOOL: "tool_id",
        HarnessComponentKind.PROMPT: "prompt_id",
        HarnessComponentKind.SKILL: "skill_id",
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
        if possible_prefix not in {item.value for item in HarnessComponentKind}:
            raise HarnessIntegrationError(
                f"Harness reference {value!r} uses an unknown component kind"
            )
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


def _canonical_registry_reference(value: str) -> tuple[HarnessComponentKind, str, str]:
    """Parse and normalize one exact registry identity for lineage checks."""

    kind, component_id, version = _parse_reference_string(value)
    return kind, component_id, _harness_version(version)


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
    agent = _value(manifest, "agent")
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
            manifest_id=_optional_string(manifest, "manifest_id"),
            manifest_digest=_optional_string(manifest, "manifest_digest"),
            registry_snapshot_id=_optional_string(manifest, "registry_snapshot_id"),
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
            # Harness outcomes may contain sensitive or tenant-scoped tool
            # arguments.  Keep the call/result evidence, but never copy the
            # raw argument payload into the Lab trace.  Safe argument keys or
            # digests can still be carried through event metadata.
            arguments={},
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
    positions: dict[tuple[HarnessComponentKind, str, str], int] = {}
    for value in values:
        if value is None:
            continue
        key = _registry_reference_key(value)
        existing_position = positions.get(key)
        if existing_position is None:
            positions[key] = len(result)
            result.append(value)
            continue
        existing = result[existing_position]
        # Preserve the most useful lineage when an artifact reference and a
        # registry-discovered reference identify the same exact component.
        if existing.source_artifact_id is None and value.source_artifact_id is not None:
            result[existing_position] = value
    return tuple(result)


def _registry_reference_key(
    reference: HarnessRegistryReference,
) -> tuple[HarnessComponentKind, str, str]:
    """Return identity-only key for one registry reference."""

    return (
        reference.component_kind,
        reference.component_id,
        _harness_version(reference.version),
    )


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
prompt_definition_to_candidate_artifact = harness_prompt_definition_to_candidate_artifact
tool_definition_to_candidate_artifact = harness_tool_definition_to_candidate_artifact
skill_definition_to_candidate_artifact = harness_skill_definition_to_candidate_artifact
policy_definition_to_candidate_artifact = harness_policy_definition_to_candidate_artifact
approval_policy_to_candidate_artifact = harness_approval_policy_to_candidate_artifact


__all__ = [
    "EnterpriseAgentHarnessAdapter",
    "HarnessIntegrationError",
    "HarnessIntegrationUnavailableError",
    "agent_definition_to_candidate_artifact",
    "approval_policy_to_candidate_artifact",
    "prompt_definition_to_candidate_artifact",
    "skill_definition_to_candidate_artifact",
    "collect_harness_environment_snapshot",
    "convert_harness_run_trace",
    "harness_agent_definition_to_candidate_artifact",
    "harness_prompt_definition_to_candidate_artifact",
    "harness_approval_policy_to_candidate_artifact",
    "harness_skill_definition_to_candidate_artifact",
    "harness_environment_snapshot",
    "ingest_harness_production_trace",
    "harness_policy_definition_to_candidate_artifact",
    "harness_run_trace_to_execution_trace",
    "harness_trace_to_execution_trace",
    "harness_tool_definition_to_candidate_artifact",
    "policy_definition_to_candidate_artifact",
    "tool_definition_to_candidate_artifact",
]
