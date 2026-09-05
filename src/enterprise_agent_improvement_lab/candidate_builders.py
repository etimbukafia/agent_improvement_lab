"""Specialized, bounded builders for enterprise candidate changes."""

from __future__ import annotations

import difflib
import re
from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, ClassVar, cast

from enterprise_agent_improvement_lab.contracts.candidates import (
    ArtifactProvenance,
    ArtifactRiskClassification,
    CandidateArtifact,
    CandidateArtifactKind,
    CandidateArtifactReference,
    ChangeKind,
    EnterpriseAgentCandidate,
    EnterpriseCandidateChange,
    EnterpriseCandidateLineage,
    EnterpriseChangeLineage,
    ImprovementScope,
)
from enterprise_agent_improvement_lab.contracts.common import utc_now
from enterprise_agent_improvement_lab.contracts.improvement import ImprovementPlan
from enterprise_agent_improvement_lab.serialization import stable_json_dumps


class CandidateBuilderError(ValueError):
    """Raised when a specialized builder cannot create a safe candidate."""


@dataclass(frozen=True)
class CandidateBuildRequest:
    """Explicit inputs accepted by a specialized candidate builder."""

    parent_candidate: EnterpriseAgentCandidate
    scope: ImprovementScope
    source_failure_ids: tuple[str, ...]
    current_artifacts: tuple[CandidateArtifact, ...] = ()
    base_artifact: CandidateArtifact | None = None
    after_artifact: CandidateArtifact | None = None
    replacement_content: str | None = None
    target_id: str | None = None
    target_version: str = "1.0.0"
    target_kind: CandidateArtifactKind | None = None
    target_registry_reference: str | None = None
    registry_reference: str | None = None
    artifact_id: str | None = None
    artifact_name: str | None = None
    candidate_id: str | None = None
    candidate_version: str | None = None
    change_kind: ChangeKind | None = None
    changed_paths: tuple[str, ...] = ("$",)
    rationale: str | None = None
    expected_effect: str | None = None
    risk: ArtifactRiskClassification = ArtifactRiskClassification.MEDIUM
    human_approval_required: bool = True
    change_id: str | None = None
    created_at: datetime | None = None
    environment_snapshot_ref: str | None = None


@dataclass(frozen=True)
class CandidateBuildResult:
    """Materialized artifacts, changes, and candidate summary."""

    candidate: EnterpriseAgentCandidate
    artifacts: tuple[CandidateArtifact, ...]
    change: EnterpriseCandidateChange
    rendered_summary: str

    @property
    def summary(self) -> str:
        """Return the human-readable change summary."""

        return self.rendered_summary


class BoundedCandidateBuilder(ABC):
    """Base implementation shared by typed enterprise builders."""

    builder_id: ClassVar[str] = "BoundedCandidateBuilder"
    default_change_kind: ClassVar[ChangeKind]
    default_artifact_kind: ClassVar[CandidateArtifactKind]
    allowed_artifact_kinds: ClassVar[frozenset[CandidateArtifactKind]] = frozenset()

    def build(
        self,
        parent_candidate: EnterpriseAgentCandidate | CandidateBuildRequest,
        scope: ImprovementScope | None = None,
        *,
        source_failure_ids: Sequence[str] = (),
        current_artifacts: Sequence[CandidateArtifact] = (),
        artifacts: Sequence[CandidateArtifact] = (),
        base_artifact: CandidateArtifact | None = None,
        after_artifact: CandidateArtifact | None = None,
        replacement_content: str | None = None,
        content: str | None = None,
        target_id: str | None = None,
        target_version: str = "1.0.0",
        target_kind: CandidateArtifactKind | None = None,
        target_registry_reference: str | None = None,
        registry_reference: str | None = None,
        artifact_id: str | None = None,
        artifact_name: str | None = None,
        candidate_id: str | None = None,
        candidate_version: str | None = None,
        change_kind: ChangeKind | None = None,
        changed_paths: Sequence[str] = ("$",),
        rationale: str | None = None,
        expected_effect: str | None = None,
        risk: ArtifactRiskClassification = ArtifactRiskClassification.MEDIUM,
        human_approval_required: bool = True,
        change_id: str | None = None,
        created_at: datetime | None = None,
        environment_snapshot_ref: str | None = None,
    ) -> CandidateBuildResult:
        """Build one candidate using only explicit typed inputs.

        A :class:`CandidateBuildRequest` is accepted as the first argument for
        callers that want a single immutable input object.  Keyword inputs are
        retained for a small and readable public API.
        """

        request = _request_from_inputs(
            parent_candidate,
            scope,
            source_failure_ids=source_failure_ids,
            current_artifacts=(*current_artifacts, *artifacts),
            base_artifact=base_artifact,
            after_artifact=after_artifact,
            replacement_content=replacement_content if replacement_content is not None else content,
            target_id=target_id,
            target_version=target_version,
            target_kind=target_kind,
            target_registry_reference=target_registry_reference or registry_reference,
            artifact_id=artifact_id,
            artifact_name=artifact_name,
            candidate_id=candidate_id,
            candidate_version=candidate_version,
            change_kind=change_kind,
            changed_paths=tuple(changed_paths),
            rationale=rationale,
            expected_effect=expected_effect,
            risk=risk,
            human_approval_required=human_approval_required,
            change_id=change_id,
            created_at=created_at,
            environment_snapshot_ref=environment_snapshot_ref,
        )
        return self._build(request)

    def build_from_plan(
        self,
        plan: ImprovementPlan,
        parent_candidate: EnterpriseAgentCandidate,
        scope: ImprovementScope,
        *,
        source_failure_ids: Sequence[str] | None = None,
        **kwargs: object,
    ) -> CandidateBuildResult:
        """Build a candidate from a typed plan and preserve its lineage."""

        if plan.current_candidate_id != parent_candidate.candidate_id:
            raise CandidateBuilderError("Improvement plan does not target the parent candidate")
        if plan.scope_id != scope.scope_id:
            raise CandidateBuilderError("Improvement plan does not reference this scope")
        expected_kind = _change_kind_from_decision(plan.decision.value)
        if expected_kind is not None and expected_kind != self.default_change_kind:
            if (
                not (
                    self.default_change_kind == ChangeKind.TOOL_ADDITION
                    and expected_kind
                    in {
                        ChangeKind.TOOL_REMOVAL,
                        ChangeKind.TOOL_CONFIGURATION_CHANGE,
                    }
                )
                and not (
                    self.default_change_kind == ChangeKind.POLICY_CHANGE
                    and expected_kind
                    in {
                        ChangeKind.PERMISSION_CHANGE,
                        ChangeKind.RETRIEVAL_CHANGE,
                        ChangeKind.MEMORY_CHANGE,
                    }
                )
                and not (
                    self.default_change_kind == ChangeKind.SKILL_ADDITION
                    and expected_kind == ChangeKind.SKILL_REMOVAL
                )
            ):
                raise CandidateBuilderError(f"{self.builder_id} cannot build {plan.decision.value}")
        return self.build(
            parent_candidate,
            scope,
            source_failure_ids=source_failure_ids or plan.source_failure_ids,
            rationale=plan.rationale,
            expected_effect=(
                ", ".join(plan.expected_affected_metrics)
                if plan.expected_affected_metrics
                else "The targeted enterprise behavior improves."
            ),
            risk=plan.risk,
            human_approval_required=bool(plan.required_approvals) or plan.requires_human_review,
            environment_snapshot_ref=plan.registry_snapshot_id,
            **kwargs,  # type: ignore[arg-type]
        )

    def _build(self, request: CandidateBuildRequest) -> CandidateBuildResult:
        _validate_request(self, request)
        created_at = request.created_at or utc_now()
        change_kind = request.change_kind or self.default_change_kind
        target_id = request.target_id or _target_from_artifact(request.base_artifact)
        current_artifacts = _unique_artifacts(request.current_artifacts)
        artifact_map = {artifact.artifact_id: artifact for artifact in current_artifacts}
        parent_refs = tuple(request.parent_candidate.artifacts)

        base_artifact = request.base_artifact
        if base_artifact is None and target_id:
            base_artifact = _find_base_artifact(target_id, current_artifacts, parent_refs)
        if base_artifact is not None:
            existing = artifact_map.get(base_artifact.artifact_id)
            if existing is not None and existing != base_artifact:
                raise CandidateBuilderError(
                    f"Conflicting definitions supplied for artifact {base_artifact.artifact_id}"
                )
            if base_artifact.artifact_id not in artifact_map:
                artifact_map[base_artifact.artifact_id] = base_artifact
            parent_reference = _parent_reference(parent_refs, base_artifact.artifact_id)
            if parent_reference is None and target_id is not None:
                parent_reference = _parent_reference_for_target(parent_refs, target_id)
            if (
                change_kind
                not in {
                    ChangeKind.TOOL_ADDITION,
                    ChangeKind.SKILL_ADDITION,
                }
                and parent_reference is None
            ):
                raise CandidateBuilderError(
                    f"Base artifact {base_artifact.artifact_id!r} is not referenced by the parent"
                )
            if parent_reference is not None and (
                (
                    parent_reference.version is not None
                    and parent_reference.version != base_artifact.version
                )
                or (
                    parent_reference.kind is not None
                    and parent_reference.kind != base_artifact.kind
                )
                or (
                    parent_reference.registry_reference is not None
                    and parent_reference.registry_reference != base_artifact.registry_reference
                )
                or (
                    parent_reference.content_sha256 is not None
                    and parent_reference.content_sha256 != base_artifact.checksum
                )
            ):
                raise CandidateBuilderError(
                    f"Base artifact {base_artifact.artifact_id!r} does not match "
                    "its parent reference"
                )

        # A parent candidate may carry only an immutable reference while the
        # caller supplies the replacement definition separately. Preserve that
        # pinned before identity so replacements remain reviewable without a
        # loaded artifact body.
        parent_target_reference = (
            _parent_reference_for_target(parent_refs, target_id) if target_id else None
        )

        replacement = self._replacement_artifact(
            request, target_id, change_kind, base_artifact, created_at
        )
        if (
            replacement is not None
            and replacement.registry_reference is None
            and parent_target_reference is not None
            and parent_target_reference.registry_reference is not None
        ):
            # The parent may intentionally be represented only by an
            # immutable reference. Preserve that exact component identity
            # while pinning a replacement to its new version.
            replacement = replacement.model_copy(
                update={
                    "registry_reference": _registry_reference_for_version(
                        parent_target_reference.registry_reference,
                        replacement.version,
                    )
                }
            )
        if replacement is not None:
            existing = artifact_map.get(replacement.artifact_id)
            if (
                existing is not None
                and (base_artifact is None or existing.artifact_id != base_artifact.artifact_id)
                and existing != replacement
            ):
                raise CandidateBuilderError(
                    f"Replacement artifact ID collides with an existing artifact: "
                    f"{replacement.artifact_id}"
                )
            artifact_map[replacement.artifact_id] = replacement

        if (
            change_kind == ChangeKind.PROMPT_CHANGE
            and replacement is not None
            and parent_target_reference is not None
            and parent_target_reference.version is not None
            and _version_key(replacement.version) == _version_key(parent_target_reference.version)
        ):
            raise CandidateBuilderError("Prompt changes require a new prompt version")

        base_reference = base_artifact.to_reference() if base_artifact is not None else None
        if (
            base_reference is not None
            and parent_target_reference is not None
            and parent_target_reference.artifact_id != base_reference.artifact_id
        ):
            # Keep the immutable parent identity when a loaded artifact body
            # is keyed by a different Lab artifact ID but the same exact
            # component registry reference.
            base_reference = parent_target_reference
        before_reference = (
            base_reference
            if base_reference is not None
            and change_kind not in {ChangeKind.TOOL_ADDITION, ChangeKind.SKILL_ADDITION}
            else (
                parent_target_reference
                if parent_target_reference is not None
                and change_kind not in {ChangeKind.TOOL_ADDITION, ChangeKind.SKILL_ADDITION}
                else None
            )
        )
        after_reference = replacement.to_reference() if replacement is not None else None
        if change_kind in {ChangeKind.TOOL_REMOVAL, ChangeKind.SKILL_REMOVAL}:
            after_reference = None

        resolved_target_id = target_id or (
            replacement.artifact_id if replacement is not None else None
        )
        affected_artifact_id = (
            replacement.artifact_id
            if replacement is not None
            else base_artifact.artifact_id
            if base_artifact is not None
            else None
        )
        affected_tool_id = _tool_target(change_kind, resolved_target_id)
        affected_skill_id = _skill_target(change_kind, resolved_target_id)
        affected_policy_id = _policy_target(change_kind, resolved_target_id)
        change = EnterpriseCandidateChange(
            change_id=request.change_id
            or _change_id(request.parent_candidate, change_kind, resolved_target_id, replacement),
            change_kind=change_kind,
            affected_agent_id=request.parent_candidate.agent_id,
            affected_artifact_id=affected_artifact_id,
            affected_tool_id=affected_tool_id,
            affected_skill_id=affected_skill_id,
            affected_policy_id=affected_policy_id,
            before_reference=before_reference,
            after_reference=after_reference,
            changed_paths=tuple(request.changed_paths),
            rationale=request.rationale or f"Apply the bounded {change_kind.value} change.",
            source_failure_ids=tuple(request.source_failure_ids),
            expected_effect=request.expected_effect or "The targeted enterprise behavior improves.",
            risk=request.risk,
            human_approval_required=request.human_approval_required,
            diff=_diff(before_reference, after_reference, base_artifact, replacement),
            lineage=EnterpriseChangeLineage(
                scope_id=request.scope.scope_id,
                source_failure_ids=tuple(request.source_failure_ids),
                parent_candidate_id=request.parent_candidate.candidate_id,
                generator_id=self.builder_id,
                reason=request.rationale,
                created_at=created_at,
            ),
        )
        try:
            request.scope.validate_change(change)
        except ValueError as exc:
            raise CandidateBuilderError(str(exc)) from exc

        refs = _updated_references(
            parent_refs,
            base_artifact=base_artifact,
            replacement=replacement,
            target_id=resolved_target_id,
            change_kind=change_kind,
        )
        candidate = _candidate_from_change(
            request,
            change,
            refs,
            replacement,
            target_id=resolved_target_id,
            environment_snapshot_ref=request.environment_snapshot_ref,
            created_at=created_at,
        )
        all_artifacts = tuple(
            artifact_map[reference.artifact_id]
            for reference in refs
            if reference.artifact_id in artifact_map
        )
        summary = self._summary(candidate, change, replacement)
        return CandidateBuildResult(
            candidate=candidate,
            artifacts=all_artifacts,
            change=change,
            rendered_summary=summary,
        )

    def _replacement_artifact(
        self,
        request: CandidateBuildRequest,
        target_id: str | None,
        change_kind: ChangeKind,
        base_artifact: CandidateArtifact | None,
        created_at: datetime,
    ) -> CandidateArtifact | None:
        if change_kind in {ChangeKind.TOOL_REMOVAL, ChangeKind.SKILL_REMOVAL}:
            if request.after_artifact is not None or request.replacement_content is not None:
                raise CandidateBuilderError(f"{change_kind.value} cannot add an artifact")
            return None
        if request.after_artifact is not None:
            if request.replacement_content is not None:
                raise CandidateBuilderError("after_artifact and replacement_content are exclusive")
            if (
                change_kind == ChangeKind.PROMPT_CHANGE
                and base_artifact is not None
                and _version_key(request.after_artifact.version)
                == _version_key(base_artifact.version)
            ):
                raise CandidateBuilderError("Prompt changes require a new prompt version")
            inherited_registry_reference = (
                _registry_reference_for_version(
                    base_artifact.registry_reference,
                    request.after_artifact.version,
                )
                if base_artifact is not None and base_artifact.registry_reference is not None
                else None
            )
            registry_reference = (
                _request_registry_reference(request) or inherited_registry_reference
            )
            if registry_reference is not None:
                if (
                    request.after_artifact.registry_reference is not None
                    and request.after_artifact.registry_reference != registry_reference
                ):
                    raise CandidateBuilderError(
                        "after_artifact registry reference does not match the requested target"
                    )
                if request.after_artifact.registry_reference is None:
                    return request.after_artifact.model_copy(
                        update={"registry_reference": registry_reference}
                    )
            elif (
                base_artifact is not None
                and base_artifact.registry_reference is not None
                and request.after_artifact.registry_reference is None
            ):
                return request.after_artifact.model_copy(
                    update={"registry_reference": inherited_registry_reference}
                )
            return request.after_artifact
        content = request.replacement_content
        if content is None:
            if change_kind in {ChangeKind.TOOL_ADDITION, ChangeKind.SKILL_ADDITION}:
                identity_field = (
                    "skill_id" if change_kind == ChangeKind.SKILL_ADDITION else "tool_id"
                )
                content = stable_json_dumps(
                    {
                        identity_field: target_id,
                        "registry_reference": _request_registry_reference(request),
                    }
                )
            else:
                raise CandidateBuilderError(
                    "A replacement artifact or replacement_content is required"
                )
        if not content.strip():
            raise CandidateBuilderError("Replacement content must not be empty")
        kind = _artifact_kind_for_request(
            self,
            request,
            change_kind,
            base_artifact,
        )
        version = request.target_version
        if base_artifact is not None and request.target_version == "1.0.0":
            version = _next_version(base_artifact.version)
        if (
            change_kind == ChangeKind.PROMPT_CHANGE
            and base_artifact is not None
            and _version_key(version) == _version_key(base_artifact.version)
        ):
            raise CandidateBuilderError("Prompt changes require a new prompt version")
        registry_reference = _request_registry_reference(request) or (
            _registry_reference_for_version(base_artifact.registry_reference, version)
            if base_artifact is not None
            else None
        )
        artifact_id = request.artifact_id or _generated_artifact_id(
            request.parent_candidate,
            change_kind,
            target_id,
            content,
            artifact_kind=kind,
            version=version,
            registry_reference=registry_reference,
        )
        return CandidateArtifact(
            artifact_id=artifact_id,
            name=request.artifact_name or target_id or artifact_id,
            version=version,
            kind=kind,
            content=content,
            provenance=ArtifactProvenance(
                source="improvement-builder",
                source_ref=request.parent_candidate.candidate_id,
                created_by=self.builder_id,
                created_at=created_at,
                parent_artifact_id=base_artifact.artifact_id if base_artifact else None,
            ),
            registry_reference=registry_reference,
            created_at=created_at,
        )

    def _summary(
        self,
        candidate: EnterpriseAgentCandidate,
        change: EnterpriseCandidateChange,
        replacement: CandidateArtifact | None,
    ) -> str:
        artifact = f" artifact {replacement.artifact_id}" if replacement else ""
        return (
            f"{self.builder_id} created candidate {candidate.candidate_id} with "
            f"{change.change_kind.value}{artifact}; protected resources were not modified."
        )


class PromptCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "PromptCandidateBuilder"
    default_change_kind = ChangeKind.PROMPT_CHANGE
    default_artifact_kind = CandidateArtifactKind.SYSTEM_PROMPT
    allowed_artifact_kinds = frozenset(
        {
            CandidateArtifactKind.SYSTEM_PROMPT,
            CandidateArtifactKind.DEVELOPER_PROMPT,
            CandidateArtifactKind.USER_TEMPLATE,
        }
    )


class ToolBindingCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "ToolBindingCandidateBuilder"
    default_change_kind = ChangeKind.TOOL_ADDITION
    default_artifact_kind = CandidateArtifactKind.TOOL_BINDING
    allowed_artifact_kinds = frozenset(
        {CandidateArtifactKind.TOOL_BINDING, CandidateArtifactKind.TOOL_CONFIGURATION}
    )


class PolicyCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "PolicyCandidateBuilder"
    default_change_kind = ChangeKind.POLICY_CHANGE
    default_artifact_kind = CandidateArtifactKind.POLICY
    allowed_artifact_kinds = frozenset(
        {
            CandidateArtifactKind.POLICY,
            CandidateArtifactKind.CONFIGURATION,
            CandidateArtifactKind.RETRIEVAL_CONFIGURATION,
            CandidateArtifactKind.MEMORY_CONFIGURATION,
        }
    )


class RoutingCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "RoutingCandidateBuilder"
    default_change_kind = ChangeKind.ROUTING_CHANGE
    default_artifact_kind = CandidateArtifactKind.ROUTING_POLICY
    allowed_artifact_kinds = frozenset({CandidateArtifactKind.ROUTING_POLICY})


class ModelCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "ModelCandidateBuilder"
    default_change_kind = ChangeKind.MODEL_CHANGE
    default_artifact_kind = CandidateArtifactKind.MODEL_CONFIGURATION
    allowed_artifact_kinds = frozenset({CandidateArtifactKind.MODEL_CONFIGURATION})


class WorkflowCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "WorkflowCandidateBuilder"
    default_change_kind = ChangeKind.WORKFLOW_CHANGE
    default_artifact_kind = CandidateArtifactKind.WORKFLOW_CONFIGURATION
    allowed_artifact_kinds = frozenset({CandidateArtifactKind.WORKFLOW_CONFIGURATION})


class ThresholdCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "ThresholdCandidateBuilder"
    default_change_kind = ChangeKind.THRESHOLD_CHANGE
    default_artifact_kind = CandidateArtifactKind.CONFIGURATION
    allowed_artifact_kinds = frozenset({CandidateArtifactKind.CONFIGURATION})


class ApprovalRuleCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "ApprovalRuleCandidateBuilder"
    default_change_kind = ChangeKind.APPROVAL_RULE_CHANGE
    default_artifact_kind = CandidateArtifactKind.APPROVAL_POLICY
    allowed_artifact_kinds = frozenset({CandidateArtifactKind.APPROVAL_POLICY})


class SkillCandidateBuilder(BoundedCandidateBuilder):
    builder_id = "SkillCandidateBuilder"
    default_change_kind = ChangeKind.SKILL_ADDITION
    default_artifact_kind = CandidateArtifactKind.SKILL_CONFIGURATION
    allowed_artifact_kinds = frozenset({CandidateArtifactKind.SKILL_CONFIGURATION})


_BUILDER_TYPES: dict[str, type[BoundedCandidateBuilder]] = {
    builder.builder_id: builder
    for builder in (
        PromptCandidateBuilder,
        ToolBindingCandidateBuilder,
        PolicyCandidateBuilder,
        RoutingCandidateBuilder,
        ModelCandidateBuilder,
        WorkflowCandidateBuilder,
        ThresholdCandidateBuilder,
        ApprovalRuleCandidateBuilder,
        SkillCandidateBuilder,
    )
}


def builder_for_plan(plan: ImprovementPlan) -> BoundedCandidateBuilder:
    """Return the specialized builder named by a typed improvement plan."""

    builder_type = _BUILDER_TYPES.get(plan.candidate_builder_type)
    if builder_type is None:
        raise CandidateBuilderError(
            f"No specialized builder is registered for {plan.candidate_builder_type!r}"
        )
    return builder_type()


def _request_from_inputs(
    parent_candidate: EnterpriseAgentCandidate | CandidateBuildRequest,
    scope: ImprovementScope | None,
    **values: object,
) -> CandidateBuildRequest:
    if isinstance(parent_candidate, CandidateBuildRequest):
        if scope is not None:
            raise CandidateBuilderError("A request already contains its improvement scope")
        return parent_candidate
    if scope is None:
        raise CandidateBuilderError("An improvement scope is required")
    try:
        return CandidateBuildRequest(
            parent_candidate=parent_candidate,
            scope=scope,
            **cast(Any, values),
        )
    except (TypeError, ValueError) as exc:
        raise CandidateBuilderError(str(exc)) from exc


def _validate_request(builder: BoundedCandidateBuilder, request: CandidateBuildRequest) -> None:
    if request.parent_candidate.agent_id in request.scope.protected_agents:
        raise CandidateBuilderError("Protected agent cannot be changed")
    if not request.source_failure_ids:
        raise CandidateBuilderError("Candidate builders require source failure evidence")
    failure_ids = tuple(str(value).strip() for value in request.source_failure_ids)
    if any(not value for value in failure_ids) or len(failure_ids) != len(set(failure_ids)):
        raise CandidateBuilderError("source_failure_ids must contain unique non-empty IDs")
    change_kind = request.change_kind or builder.default_change_kind
    target_id = request.target_id or _target_from_artifact(request.base_artifact)
    if target_id is None and request.after_artifact is not None:
        target_id = _target_from_artifact(request.after_artifact)
    if change_kind in {
        ChangeKind.TOOL_ADDITION,
        ChangeKind.TOOL_CONFIGURATION_CHANGE,
    }:
        if not target_id:
            raise CandidateBuilderError("Tool changes need an affected tool ID")
        registry_reference = (
            _request_registry_reference(request)
            or _artifact_registry_reference(request.after_artifact)
            or _artifact_registry_reference(request.base_artifact)
        )
        if change_kind == ChangeKind.TOOL_ADDITION or registry_reference is not None:
            _validate_registry_reference(
                registry_reference,
                "tool",
                target_id,
            )
    if change_kind == ChangeKind.SKILL_ADDITION:
        if not target_id:
            raise CandidateBuilderError("Skill additions need an affected skill ID")
        _validate_registry_reference(
            _request_registry_reference(request)
            or _artifact_registry_reference(request.after_artifact),
            "skill",
            target_id,
        )
    if request.change_kind is not None:
        compatible_change_kinds = (
            {
                ChangeKind.TOOL_REMOVAL,
                ChangeKind.TOOL_CONFIGURATION_CHANGE,
            }
            if builder.default_change_kind == ChangeKind.TOOL_ADDITION
            else {
                ChangeKind.PERMISSION_CHANGE,
                ChangeKind.RETRIEVAL_CHANGE,
                ChangeKind.MEMORY_CHANGE,
            }
            if builder.default_change_kind == ChangeKind.POLICY_CHANGE
            else {
                ChangeKind.SKILL_REMOVAL,
            }
            if builder.default_change_kind == ChangeKind.SKILL_ADDITION
            else set()
        )
        if request.change_kind in compatible_change_kinds:
            pass
        elif request.change_kind != builder.default_change_kind:
            raise CandidateBuilderError(
                f"{builder.builder_id} cannot build {request.change_kind.value}"
            )
    expected_kind = _artifact_kind_for_request(
        builder,
        request,
        change_kind,
        request.base_artifact,
    )
    if builder.allowed_artifact_kinds and expected_kind not in builder.allowed_artifact_kinds:
        raise CandidateBuilderError(
            f"{builder.builder_id} cannot materialize {expected_kind.value} artifacts"
        )
    if request.after_artifact is not None and request.after_artifact.kind != expected_kind:
        raise CandidateBuilderError("after_artifact kind does not match the specialized builder")


def _artifact_kind_for_request(
    builder: BoundedCandidateBuilder,
    request: CandidateBuildRequest,
    change_kind: ChangeKind,
    base_artifact: CandidateArtifact | None,
) -> CandidateArtifactKind:
    if request.target_kind is not None:
        return request.target_kind
    if base_artifact is not None:
        return base_artifact.kind
    if request.after_artifact is not None:
        return request.after_artifact.kind
    return {
        ChangeKind.TOOL_CONFIGURATION_CHANGE: CandidateArtifactKind.TOOL_CONFIGURATION,
        ChangeKind.RETRIEVAL_CHANGE: CandidateArtifactKind.RETRIEVAL_CONFIGURATION,
        ChangeKind.MEMORY_CHANGE: CandidateArtifactKind.MEMORY_CONFIGURATION,
        ChangeKind.ROUTING_CHANGE: CandidateArtifactKind.ROUTING_POLICY,
        ChangeKind.APPROVAL_RULE_CHANGE: CandidateArtifactKind.APPROVAL_POLICY,
        ChangeKind.MODEL_CHANGE: CandidateArtifactKind.MODEL_CONFIGURATION,
        ChangeKind.WORKFLOW_CHANGE: CandidateArtifactKind.WORKFLOW_CONFIGURATION,
        ChangeKind.SKILL_ADDITION: CandidateArtifactKind.SKILL_CONFIGURATION,
    }.get(change_kind, builder.default_artifact_kind)


def _artifact_registry_reference(artifact: CandidateArtifact | None) -> str | None:
    return artifact.registry_reference if artifact is not None else None


def _request_registry_reference(request: CandidateBuildRequest) -> str | None:
    return request.target_registry_reference or request.registry_reference


def _registry_reference_for_version(reference: str | None, version: str) -> str | None:
    """Pin an inherited component reference to the replacement version."""

    if reference is None:
        return None
    prefix, separator, _ = reference.rpartition("@")
    if not separator or not prefix:
        return reference
    return f"{prefix}@{version}"


def _validate_registry_reference(
    reference: str | None,
    component_kind: str,
    component_id: str,
) -> None:
    expected_prefix = f"{component_kind}:{component_id}@"
    if reference is None or not reference.startswith(expected_prefix):
        raise CandidateBuilderError(
            f"{component_kind} changes need a valid registry reference ({expected_prefix}<version>)"
        )
    version = reference[len(expected_prefix) :]
    if (
        not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version)
        or "@" in version
        or ":" in version
        or any(char.isspace() for char in version)
    ):
        raise CandidateBuilderError("Registry references must include a valid component version")


def _unique_artifacts(artifacts: Sequence[CandidateArtifact]) -> tuple[CandidateArtifact, ...]:
    result: list[CandidateArtifact] = []
    seen: set[str] = set()
    for artifact in artifacts:
        if artifact.artifact_id in seen:
            raise CandidateBuilderError(f"Duplicate artifact ID: {artifact.artifact_id}")
        seen.add(artifact.artifact_id)
        result.append(artifact)
    return tuple(result)


def _find_base_artifact(
    target_id: str,
    artifacts: Sequence[CandidateArtifact],
    references: Sequence[CandidateArtifactReference],
) -> CandidateArtifact | None:
    for artifact in artifacts:
        if artifact.artifact_id == target_id or _reference_matches_target(
            artifact.to_reference(), target_id
        ):
            return artifact
    for reference in references:
        if _reference_matches_target(reference, target_id):
            for artifact in artifacts:
                if artifact.artifact_id == reference.artifact_id:
                    return artifact
    return None


def _reference_matches_target(reference: CandidateArtifactReference, target_id: str) -> bool:
    return reference.artifact_id == target_id or (
        reference.registry_reference is not None
        and (
            reference.registry_reference == target_id
            or reference.registry_reference.startswith(f"prompt:{target_id}@")
            or reference.registry_reference.startswith(f"tool:{target_id}@")
            or reference.registry_reference.startswith(f"skill:{target_id}@")
            or reference.registry_reference.startswith(f"policy:{target_id}@")
        )
    )


def _parent_has_reference(
    references: Sequence[CandidateArtifactReference], artifact_id: str
) -> bool:
    return any(reference.artifact_id == artifact_id for reference in references)


def _parent_reference(
    references: Sequence[CandidateArtifactReference], artifact_id: str
) -> CandidateArtifactReference | None:
    return next(
        (reference for reference in references if reference.artifact_id == artifact_id),
        None,
    )


def _parent_reference_for_target(
    references: Sequence[CandidateArtifactReference], target_id: str
) -> CandidateArtifactReference | None:
    """Find a parent reference by artifact ID or exact component identity."""

    return next(
        (reference for reference in references if _reference_matches_target(reference, target_id)),
        None,
    )


def _target_from_artifact(artifact: CandidateArtifact | None) -> str | None:
    if artifact is None:
        return None
    if artifact.registry_reference:
        value = artifact.registry_reference.rsplit(":", 1)[-1]
        return value.split("@", 1)[0]
    return artifact.artifact_id


def _replacement_target(change_kind: ChangeKind) -> bool:
    return change_kind not in {ChangeKind.TOOL_REMOVAL, ChangeKind.SKILL_REMOVAL}


def _tool_target(change_kind: ChangeKind, target_id: str | None) -> str | None:
    return (
        target_id
        if change_kind
        in {
            ChangeKind.TOOL_ADDITION,
            ChangeKind.TOOL_REMOVAL,
            ChangeKind.TOOL_CONFIGURATION_CHANGE,
        }
        else None
    )


def _skill_target(change_kind: ChangeKind, target_id: str | None) -> str | None:
    return (
        target_id
        if change_kind
        in {
            ChangeKind.SKILL_ADDITION,
            ChangeKind.SKILL_REMOVAL,
        }
        else None
    )


def _policy_target(change_kind: ChangeKind, target_id: str | None) -> str | None:
    return (
        target_id
        if change_kind
        in {
            ChangeKind.POLICY_CHANGE,
            ChangeKind.PERMISSION_CHANGE,
            ChangeKind.APPROVAL_RULE_CHANGE,
        }
        else None
    )


def _updated_references(
    parent_refs: Sequence[CandidateArtifactReference],
    *,
    base_artifact: CandidateArtifact | None,
    replacement: CandidateArtifact | None,
    target_id: str | None,
    change_kind: ChangeKind,
) -> tuple[CandidateArtifactReference, ...]:
    removed_ids: set[str] = set()
    if base_artifact is not None and _replacement_target(change_kind):
        removed_ids.add(base_artifact.artifact_id)
    if change_kind in {ChangeKind.TOOL_REMOVAL, ChangeKind.SKILL_REMOVAL}:
        removed_ids.update(
            reference.artifact_id
            for reference in parent_refs
            if target_id and _reference_matches_target(reference, target_id)
        )
    result = [
        reference
        for reference in parent_refs
        if reference.artifact_id not in removed_ids
        and not (
            replacement is not None
            and _replacement_target(change_kind)
            and target_id is not None
            and _reference_matches_target(reference, target_id)
        )
    ]
    if replacement is not None:
        result.append(replacement.to_reference())
    if not result:
        raise CandidateBuilderError("A candidate must retain at least one artifact reference")
    return tuple(result)


def _candidate_from_change(
    request: CandidateBuildRequest,
    change: EnterpriseCandidateChange,
    references: tuple[CandidateArtifactReference, ...],
    replacement: CandidateArtifact | None,
    *,
    target_id: str | None,
    environment_snapshot_ref: str | None,
    created_at: datetime,
) -> EnterpriseAgentCandidate:
    parent = request.parent_candidate
    tools = _update_component_ids(
        parent.tools,
        target_id,
        change.change_kind,
        {ChangeKind.TOOL_ADDITION},
        {ChangeKind.TOOL_REMOVAL},
    )
    skills = _update_component_ids(
        parent.skills,
        target_id,
        change.change_kind,
        {ChangeKind.SKILL_ADDITION},
        {ChangeKind.SKILL_REMOVAL},
    )
    policies = _update_component_ids(
        parent.policies,
        target_id,
        change.change_kind,
        set(),
        set(),
        replace_policy=change.change_kind
        in {
            ChangeKind.POLICY_CHANGE,
            ChangeKind.PERMISSION_CHANGE,
            ChangeKind.APPROVAL_RULE_CHANGE,
        },
    )
    tool_bindings = _update_component_ids(
        parent.tool_bindings,
        target_id,
        change.change_kind,
        {ChangeKind.TOOL_ADDITION, ChangeKind.TOOL_CONFIGURATION_CHANGE},
        {ChangeKind.TOOL_REMOVAL},
    )
    configuration_field = {
        ChangeKind.MODEL_CHANGE: "model_configuration",
        ChangeKind.MEMORY_CHANGE: "memory_configuration",
        ChangeKind.RETRIEVAL_CHANGE: "retrieval_configuration",
        ChangeKind.ROUTING_CHANGE: "routing_configuration",
        ChangeKind.APPROVAL_RULE_CHANGE: "approval_configuration",
        ChangeKind.WORKFLOW_CHANGE: "workflow_configuration",
    }.get(change.change_kind)
    values: dict[str, Any] = {
        "candidate_id": request.candidate_id
        or _generated_candidate_id(parent, change, replacement),
        "agent_id": parent.agent_id,
        "name": parent.name,
        "version": request.candidate_version or _next_version(parent.version),
        "agent_version": parent.agent_version,
        "parent_candidate_id": parent.candidate_id,
        "artifacts": references,
        "prompt_ref": (
            replacement.to_reference()
            if replacement is not None
            and replacement.kind
            in {
                CandidateArtifactKind.SYSTEM_PROMPT,
                CandidateArtifactKind.DEVELOPER_PROMPT,
                CandidateArtifactKind.USER_TEMPLATE,
            }
            else parent.prompt_ref
        ),
        "runtime_profile": parent.runtime_profile,
        "tools": tools,
        "tool_bindings": tool_bindings,
        "skills": skills,
        "policies": policies,
        "model_configuration": parent.model_configuration,
        "memory_configuration": parent.memory_configuration,
        "retrieval_configuration": parent.retrieval_configuration,
        "routing_configuration": parent.routing_configuration,
        "approval_configuration": parent.approval_configuration,
        "workflow_configuration": parent.workflow_configuration,
        "changes": (change,),
        "rationale": request.rationale or change.rationale,
        "created_at": created_at,
        "metadata": dict(parent.metadata),
    }
    values["lineage"] = EnterpriseCandidateLineage(
        parent_candidate_id=parent.candidate_id,
        source_failure_ids=change.source_failure_ids,
        improvement_scope_id=request.scope.scope_id,
        generator_id=change.lineage.generator_id,
        environment_snapshot_ref=environment_snapshot_ref,
        reason=request.rationale,
        created_at=created_at,
    )
    if configuration_field is not None and replacement is not None:
        values[configuration_field] = replacement.artifact_id
    return EnterpriseAgentCandidate(**values)


def _update_component_ids(
    values: Sequence[str],
    target_id: str | None,
    change_kind: ChangeKind,
    additions: set[ChangeKind],
    removals: set[ChangeKind],
    *,
    replace_policy: bool = False,
) -> tuple[str, ...]:
    result = list(values)
    if target_id is None:
        return tuple(result)
    if change_kind in additions and target_id not in result:
        result.append(target_id)
    if change_kind in removals:
        result = [value for value in result if value != target_id]
    if replace_policy and target_id not in result:
        result.append(target_id)
    return tuple(result)


def _diff(
    before_reference: CandidateArtifactReference | None,
    after_reference: CandidateArtifactReference | None,
    before: CandidateArtifact | None,
    after: CandidateArtifact | None,
) -> str:
    if before is None and after is None:
        return ""
    before_text = before.content if before is not None else ""
    after_text = after.content if after is not None else ""
    before_label = before_reference.artifact_id if before_reference else "before"
    after_label = after_reference.artifact_id if after_reference else "after"
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=before_label,
            tofile=after_label,
        )
    )


def _generated_artifact_id(
    parent: EnterpriseAgentCandidate,
    change_kind: ChangeKind,
    target_id: str | None,
    content: str,
    *,
    artifact_kind: CandidateArtifactKind,
    version: str,
    registry_reference: str | None,
) -> str:
    digest = sha256(
        stable_json_dumps(
            {
                "parent": parent.candidate_id,
                "kind": change_kind.value,
                "target": target_id,
                "content_sha256": sha256(content.encode("utf-8")).hexdigest(),
                "artifact_kind": artifact_kind.value,
                "version": version,
                "registry_reference": registry_reference,
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"artifact:{change_kind.value}:{digest}"


def _generated_candidate_id(
    parent: EnterpriseAgentCandidate,
    change: EnterpriseCandidateChange,
    replacement: CandidateArtifact | None,
) -> str:
    digest = sha256(
        stable_json_dumps(
            {
                "parent": parent.candidate_id,
                "change": change.change_id,
                "kind": change.change_kind.value,
                "after": replacement.checksum if replacement else None,
                "source_failures": change.source_failure_ids,
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"candidate:{digest}"


def _change_id(
    parent: EnterpriseAgentCandidate,
    change_kind: ChangeKind,
    target_id: str | None,
    replacement: CandidateArtifact | None,
) -> str:
    digest = sha256(
        stable_json_dumps(
            {
                "parent": parent.candidate_id,
                "kind": change_kind.value,
                "target": target_id,
                "after": replacement.checksum if replacement else None,
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"change:{digest}"


def _next_version(version: str) -> str:
    parts = [int(part) for part in version.split(".")]
    if len(parts) == 2:
        parts.append(0)
    parts[-1] += 1
    return ".".join(str(part) for part in parts)


def _version_key(version: str) -> tuple[int, ...]:
    """Return a comparable version key for two- or three-part Lab versions."""

    parts = tuple(int(part) for part in version.split("."))
    return parts if len(parts) == 3 else (*parts, 0)


def _change_kind_from_decision(value: str) -> ChangeKind | None:
    try:
        return ChangeKind(value)
    except ValueError:
        return None


def render_candidate_diff(candidate: EnterpriseAgentCandidate) -> str:
    """Render a deterministic, human-readable summary of candidate changes."""

    changes = [
        {
            "change_id": change.change_id,
            "change_kind": change.change_kind.value,
            "affected_agent_id": change.affected_agent_id,
            "affected_artifact_id": change.affected_artifact_id,
            "affected_tool_id": change.affected_tool_id,
            "affected_skill_id": change.affected_skill_id,
            "affected_policy_id": change.affected_policy_id,
            "changed_paths": change.changed_paths,
            "rationale": change.rationale,
            "expected_effect": change.expected_effect,
            "risk": change.risk.value,
            "human_approval_required": change.human_approval_required,
            "diff": change.diff,
        }
        for change in candidate.changes
    ]
    return stable_json_dumps(
        {
            "candidate_id": candidate.candidate_id,
            "parent_candidate_id": candidate.parent_candidate_id,
            "changes": changes,
        }
    )


__all__ = [
    "ApprovalRuleCandidateBuilder",
    "BoundedCandidateBuilder",
    "CandidateBuildRequest",
    "CandidateBuildResult",
    "CandidateBuilderError",
    "SkillCandidateBuilder",
    "ModelCandidateBuilder",
    "PolicyCandidateBuilder",
    "PromptCandidateBuilder",
    "RoutingCandidateBuilder",
    "ThresholdCandidateBuilder",
    "ToolBindingCandidateBuilder",
    "WorkflowCandidateBuilder",
    "builder_for_plan",
    "render_candidate_diff",
]
