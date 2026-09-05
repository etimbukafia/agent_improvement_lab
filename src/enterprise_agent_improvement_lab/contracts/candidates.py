"""Candidate and prompt-artifact contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    VersionString,
    require_aware_utc,
    utc_now,
)


class CandidateStatus(StrEnum):
    """Lifecycle state for a candidate."""

    DRAFT = "draft"
    OFFLINE_EVALUATED = "offline_evaluated"
    SHADOW = "shadow"
    CANARY = "canary"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    ACTIVE = "active"
    REJECTED = "rejected"
    RETIRED = "retired"


class CandidateArtifactKind(StrEnum):
    """Kinds of artifact that can make up an enterprise agent candidate."""

    SYSTEM_PROMPT = "system_prompt"
    DEVELOPER_PROMPT = "developer_prompt"
    USER_TEMPLATE = "user_template"
    CONFIGURATION = "configuration"
    AGENT_DEFINITION = "agent_definition"
    TOOL_BINDING = "tool_binding"
    TOOL_CONFIGURATION = "tool_configuration"
    SKILL_CONFIGURATION = "skill_configuration"
    POLICY = "policy"
    ROUTING_POLICY = "routing_policy"
    APPROVAL_POLICY = "approval_policy"
    MODEL_CONFIGURATION = "model_configuration"
    MEMORY_CONFIGURATION = "memory_configuration"
    RETRIEVAL_CONFIGURATION = "retrieval_configuration"
    WORKFLOW_CONFIGURATION = "workflow_configuration"


class CandidateComponentKind(StrEnum):
    """Provider-neutral component kinds referenced by a Lab candidate."""

    PROMPT = "prompt"
    SKILL = "skill"
    TOOL = "tool"
    POLICY = "policy"


class ChangeKind(StrEnum):
    """Typed kinds of change that an improvement may propose."""

    PROMPT_CHANGE = "prompt_change"
    TOOL_ADDITION = "tool_addition"
    TOOL_REMOVAL = "tool_removal"
    TOOL_CONFIGURATION_CHANGE = "tool_configuration_change"
    PERMISSION_CHANGE = "permission_change"
    POLICY_CHANGE = "policy_change"
    MODEL_CHANGE = "model_change"
    ROUTING_CHANGE = "routing_change"
    RETRIEVAL_CHANGE = "retrieval_change"
    MEMORY_CHANGE = "memory_change"
    THRESHOLD_CHANGE = "threshold_change"
    WORKFLOW_CHANGE = "workflow_change"
    SKILL_ADDITION = "skill_addition"
    SKILL_REMOVAL = "skill_removal"
    APPROVAL_RULE_CHANGE = "approval_rule_change"


class ArtifactRiskClassification(StrEnum):
    """Risk classification for one candidate artifact."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ArtifactProvenance(ContractModel):
    """Source information for one immutable candidate artifact."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    source: str = Field(min_length=1)
    source_ref: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    parent_artifact_id: str | None = None
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_source_input(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"source": value}
        return value

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "ArtifactProvenance":
        if self.created_at is not None:
            object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


def _path_is_within(path: str, parent: str) -> bool:
    """Return whether a JSON path is equal to or below another JSON path."""

    normalized_path = path if path.startswith("$") else f"$.{path.lstrip('.')}"
    normalized_parent = parent if parent.startswith("$") else f"$.{parent.lstrip('.')}"
    return normalized_path == normalized_parent or normalized_path.startswith(
        normalized_parent + "."
    )


def _first_present(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data:
            return data[name]
    return None


def _validate_unique_ids(name: str, values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must contain unique values")
    if any(not value for value in values):
        raise ValueError(f"{name} must contain non-empty values")


def _normalize_change_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise ValueError("changed_paths must contain non-empty paths")
    if normalized.startswith("$"):
        return normalized
    return f"$.{normalized.lstrip('.')}"


class CandidateArtifact(ContractModel):
    """An immutable versioned artifact used by an enterprise candidate."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=False,
        validate_assignment=True,
    )

    artifact_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: VersionString
    kind: CandidateArtifactKind
    content: str = Field(min_length=1)
    content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        validation_alias=AliasChoices("content_sha256", "checksum", "sha256"),
    )
    provenance: ArtifactProvenance = Field(
        default_factory=lambda: ArtifactProvenance(source="unknown")
    )
    owner: str = Field(
        default="unknown",
        min_length=1,
        validation_alias=AliasChoices("owner", "artifact_owner"),
    )
    risk_classification: ArtifactRiskClassification = Field(
        default=ArtifactRiskClassification.LOW,
        validation_alias=AliasChoices("risk_classification", "risk", "risk_level"),
    )
    registry_reference: str | None = Field(
        default=None,
        validation_alias=AliasChoices("registry_reference", "registry_ref", "registry_uri"),
    )
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_checksum(self) -> "CandidateArtifact":
        digest = sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_sha256 is not None and self.content_sha256 != digest:
            raise ValueError("content_sha256 does not match content")
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self

    def to_reference(self, *, role: str | None = None) -> "CandidateArtifactReference":
        """Return an immutable reference that pins this artifact version and checksum."""

        return CandidateArtifactReference(
            artifact_id=self.artifact_id,
            version=self.version,
            kind=self.kind,
            content_sha256=self.content_sha256,
            role=role,
            registry_reference=self.registry_reference,
        )

    def to_component_reference(
        self,
        *,
        component_kind: CandidateComponentKind | None = None,
        component_id: str | None = None,
    ) -> "CandidateComponentReference":
        """Return an exact runtime-component reference with artifact lineage."""

        return CandidateComponentReference.from_artifact(
            self,
            component_kind=component_kind,
            component_id=component_id,
        )

    @property
    def checksum(self) -> str:
        """Return the stable content checksum."""

        assert self.content_sha256 is not None
        return self.content_sha256


class CandidateArtifactReference(ContractModel):
    """A stable reference to one candidate artifact."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    artifact_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("artifact_id", "reference_id"),
    )
    version: VersionString | None = None
    kind: CandidateArtifactKind | None = None
    content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        validation_alias=AliasChoices("content_sha256", "checksum", "sha256"),
    )
    role: str | None = None
    registry_reference: str | None = Field(
        default=None,
        validation_alias=AliasChoices("registry_reference", "registry_ref", "registry_uri"),
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_reference_input(cls, value: Any) -> Any:
        if isinstance(value, CandidateArtifact):
            return value.to_reference().model_dump(mode="python")
        if isinstance(value, str):
            return {"artifact_id": value}
        return value

    @classmethod
    def from_artifact(
        cls,
        artifact: CandidateArtifact,
        *,
        role: str | None = None,
    ) -> "CandidateArtifactReference":
        """Create a pinned reference from an immutable artifact."""

        return artifact.to_reference(role=role)

    @property
    def reference_id(self) -> str:
        """Return the artifact ID using the generic reference name."""

        return self.artifact_id


_ARTIFACT_COMPONENT_KINDS = {
    CandidateArtifactKind.SYSTEM_PROMPT: CandidateComponentKind.PROMPT,
    CandidateArtifactKind.DEVELOPER_PROMPT: CandidateComponentKind.PROMPT,
    CandidateArtifactKind.USER_TEMPLATE: CandidateComponentKind.PROMPT,
    CandidateArtifactKind.SKILL_CONFIGURATION: CandidateComponentKind.SKILL,
    CandidateArtifactKind.TOOL_BINDING: CandidateComponentKind.TOOL,
    CandidateArtifactKind.TOOL_CONFIGURATION: CandidateComponentKind.TOOL,
    CandidateArtifactKind.POLICY: CandidateComponentKind.POLICY,
    CandidateArtifactKind.APPROVAL_POLICY: CandidateComponentKind.POLICY,
}


def _parse_component_identity(value: str) -> tuple[CandidateComponentKind, str, str]:
    raw = value.strip()
    prefix, separator, identity = raw.partition(":")
    component_id, version_separator, version = identity.rpartition("@")
    if not separator or not version_separator or not component_id or not version:
        raise ValueError("Component references must use '<kind>:<component_id>@<version>'")
    try:
        component_kind = CandidateComponentKind(prefix)
    except ValueError as exc:
        raise ValueError(f"Unsupported candidate component kind: {prefix!r}") from exc
    return component_kind, component_id, version


class CandidateComponentReference(ContractModel):
    """An exact provider-neutral reference to one candidate component version."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    component_kind: CandidateComponentKind
    component_id: str = Field(min_length=1)
    version: VersionString
    registry_reference: str = Field(min_length=1)
    source_artifact_id: str | None = Field(default=None, min_length=1)
    source_artifact_version: VersionString | None = None
    source_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("component_id")
    @classmethod
    def validate_component_id(cls, value: str) -> str:
        if ":" in value or "@" in value or any(char.isspace() for char in value):
            raise ValueError("component_id cannot contain separators or whitespace")
        return value

    @model_validator(mode="before")
    @classmethod
    def coerce_reference_input(cls, value: Any) -> Any:
        if isinstance(value, CandidateArtifact):
            return cls.from_artifact(value).model_dump(mode="python")
        if isinstance(value, CandidateArtifactReference):
            return cls.from_artifact_reference(value).model_dump(mode="python")
        if isinstance(value, str):
            component_kind, component_id, version = _parse_component_identity(value)
            return {
                "component_kind": component_kind,
                "component_id": component_id,
                "version": version,
                "registry_reference": value.strip(),
            }
        if isinstance(value, dict):
            data = dict(value)
            registry_reference = data.get("registry_reference")
            if isinstance(registry_reference, str):
                component_kind, component_id, version = _parse_component_identity(
                    registry_reference
                )
                data.setdefault("component_kind", component_kind)
                data.setdefault("component_id", component_id)
                data.setdefault("version", version)
            elif all(
                data.get(name) is not None for name in ("component_kind", "component_id", "version")
            ):
                kind = CandidateComponentKind(data["component_kind"])
                data["registry_reference"] = (
                    f"{kind.value}:{data['component_id']}@{data['version']}"
                )
            return data
        return value

    @model_validator(mode="after")
    def validate_exact_identity(self) -> "CandidateComponentReference":
        expected = f"{self.component_kind.value}:{self.component_id}@{self.version}"
        if self.registry_reference != expected:
            raise ValueError("registry_reference must match the exact component identity")
        provenance = (
            self.source_artifact_id,
            self.source_artifact_version,
            self.source_artifact_sha256,
        )
        if any(value is not None for value in provenance) and not all(
            value is not None for value in provenance
        ):
            raise ValueError("Artifact provenance needs an ID, version, and checksum together")
        return self

    @property
    def identity(self) -> str:
        """Return the canonical exact component identity."""

        return self.registry_reference

    @classmethod
    def from_artifact(
        cls,
        artifact: CandidateArtifact,
        *,
        component_kind: CandidateComponentKind | None = None,
        component_id: str | None = None,
    ) -> "CandidateComponentReference":
        """Create an exact component reference from an immutable artifact."""

        registry_reference = artifact.registry_reference
        if registry_reference is not None:
            parsed_kind, parsed_id, parsed_version = _parse_component_identity(registry_reference)
            if component_kind is not None and parsed_kind != component_kind:
                raise ValueError("Artifact registry reference has a different component kind")
            if component_id is not None and parsed_id != component_id:
                raise ValueError("Artifact registry reference has a different component ID")
            if parsed_version != artifact.version:
                raise ValueError("Artifact registry reference has a different version")
            component_kind = parsed_kind
            component_id = parsed_id
        else:
            component_kind = component_kind or _ARTIFACT_COMPONENT_KINDS.get(artifact.kind)
            component_id = component_id or artifact.artifact_id
            if component_kind is None:
                raise ValueError("Artifact kind does not identify a candidate component")
            registry_reference = f"{component_kind.value}:{component_id}@{artifact.version}"
        return cls(
            component_kind=component_kind,
            component_id=component_id,
            version=artifact.version,
            registry_reference=registry_reference,
            source_artifact_id=artifact.artifact_id,
            source_artifact_version=artifact.version,
            source_artifact_sha256=artifact.checksum,
        )

    @classmethod
    def from_artifact_reference(
        cls,
        reference: CandidateArtifactReference,
    ) -> "CandidateComponentReference":
        """Create an exact component reference from pinned artifact lineage."""

        if reference.version is None or reference.content_sha256 is None:
            raise ValueError("Artifact reference needs an exact version and checksum")
        component_kind: CandidateComponentKind | None
        component_id: str
        if reference.registry_reference is not None:
            component_kind, component_id, component_version = _parse_component_identity(
                reference.registry_reference
            )
            if component_version != reference.version:
                raise ValueError("Artifact registry reference has a different version")
        else:
            component_kind = (
                _ARTIFACT_COMPONENT_KINDS.get(reference.kind)
                if reference.kind is not None
                else None
            )
            if component_kind is None:
                raise ValueError("Artifact reference needs an exact registry identity")
            component_id = reference.artifact_id
        assert component_kind is not None
        return cls(
            component_kind=component_kind,
            component_id=component_id,
            version=reference.version,
            registry_reference=(
                reference.registry_reference
                or f"{component_kind.value}:{component_id}@{reference.version}"
            ),
            source_artifact_id=reference.artifact_id,
            source_artifact_version=reference.version,
            source_artifact_sha256=reference.content_sha256,
        )


class EnterpriseCandidateLineage(ContractModel):
    """Immutable provenance for one enterprise candidate version."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    parent_candidate_id: str | None = None
    source_failure_ids: tuple[str, ...] = ()
    source_annotation_ids: tuple[str, ...] = ()
    improvement_scope_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("improvement_scope_id", "scope_id"),
    )
    generator_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("generator_id", "builder_id"),
    )
    environment_snapshot_ref: str | None = Field(
        default=None,
        validation_alias=AliasChoices("environment_snapshot_ref", "environment_snapshot_id"),
    )
    reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_lineage(self) -> "EnterpriseCandidateLineage":
        for name, values in (
            ("source_failure_ids", self.source_failure_ids),
            ("source_annotation_ids", self.source_annotation_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique values")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class EnterpriseAgentCandidate(ContractModel):
    """A complete versioned enterprise agent candidate."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    candidate_id: str = Field(min_length=1)
    agent_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("agent_id", "agent_identity"),
    )
    name: str = Field(
        default="candidate", min_length=1, validation_alias=AliasChoices("name", "agent_name")
    )
    version: VersionString = Field(validation_alias=AliasChoices("version", "candidate_version"))
    agent_version: VersionString | None = None
    parent_candidate_id: str | None = None
    artifacts: tuple[CandidateArtifactReference, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "artifacts", "artifact_references", "artifact_refs", "artifact_ids"
        ),
    )
    prompt_ref: CandidateComponentReference | None = None
    skill_refs: tuple[CandidateComponentReference, ...] = ()
    tool_refs: tuple[CandidateComponentReference, ...] = ()
    policy_refs: tuple[CandidateComponentReference, ...] = ()
    runtime_profile: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("runtime_profile", "runtime_profile_id"),
    )
    model_configuration: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("model_configuration", "model_configuration_id"),
    )
    memory_configuration: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("memory_configuration", "memory_configuration_id"),
    )
    retrieval_configuration: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("retrieval_configuration", "retrieval_configuration_id"),
    )
    routing_configuration: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("routing_configuration", "routing_configuration_id"),
    )
    approval_configuration: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("approval_configuration", "approval_configuration_id"),
    )
    workflow_configuration: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("workflow_configuration", "workflow_configuration_id"),
    )
    lineage: EnterpriseCandidateLineage = Field(default_factory=EnterpriseCandidateLineage)
    changes: tuple["EnterpriseCandidateChange", ...] = Field(
        default=(), validation_alias=AliasChoices("changes", "candidate_changes")
    )
    status: CandidateStatus = Field(
        default=CandidateStatus.DRAFT,
        validation_alias=AliasChoices("status", "lifecycle_status"),
    )
    rationale: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_candidate(self) -> "EnterpriseAgentCandidate":
        if self.parent_candidate_id == self.candidate_id:
            raise ValueError("parent_candidate_id must differ from candidate_id")
        lineage_parent = self.lineage.parent_candidate_id
        if lineage_parent != self.parent_candidate_id:
            raise ValueError("lineage.parent_candidate_id must match parent_candidate_id")
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact references must contain unique artifact IDs")
        component_groups = (
            ("skill_refs", CandidateComponentKind.SKILL, self.skill_refs),
            ("tool_refs", CandidateComponentKind.TOOL, self.tool_refs),
            ("policy_refs", CandidateComponentKind.POLICY, self.policy_refs),
        )
        for name, expected_kind, references in component_groups:
            if any(reference.component_kind != expected_kind for reference in references):
                raise ValueError(f"{name} must contain only {expected_kind.value} references")
            component_ids = tuple(reference.component_id for reference in references)
            _validate_unique_ids(name, component_ids)
        if (
            self.prompt_ref is not None
            and self.prompt_ref.component_kind != CandidateComponentKind.PROMPT
        ):
            raise ValueError("prompt_ref must identify a prompt component")
        artifact_by_id = {reference.artifact_id: reference for reference in self.artifacts}
        for reference in (
            *((self.prompt_ref,) if self.prompt_ref is not None else ()),
            *self.skill_refs,
            *self.tool_refs,
            *self.policy_refs,
        ):
            if reference.source_artifact_id is None:
                continue
            artifact_reference = artifact_by_id.get(reference.source_artifact_id)
            if artifact_reference is None:
                raise ValueError("Component source artifact must be referenced by the candidate")
            if artifact_reference.version != reference.source_artifact_version:
                raise ValueError("Component source artifact version does not match")
            if artifact_reference.content_sha256 != reference.source_artifact_sha256:
                raise ValueError("Component source artifact checksum does not match")
        change_ids = tuple(change.change_id for change in self.changes)
        _validate_unique_ids("change_ids", change_ids)
        for change in self.changes:
            if change.affected_agent_id != self.agent_id:
                raise ValueError("Candidate changes must target the candidate agent")
            if change.lineage.parent_candidate_id != self.parent_candidate_id:
                raise ValueError("Candidate change lineage must match the candidate parent")
            if (
                self.lineage.improvement_scope_id is not None
                and change.lineage.scope_id != self.lineage.improvement_scope_id
            ):
                raise ValueError("Candidate change lineage must match the candidate scope")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self

    @property
    def artifact_references(self) -> tuple[CandidateArtifactReference, ...]:
        """Return the candidate's pinned artifact references."""

        return self.artifacts

    @property
    def artifact_refs(self) -> tuple[CandidateArtifactReference, ...]:
        """Return artifact references using the short compatibility name."""

        return self.artifacts

    @property
    def lifecycle_status(self) -> CandidateStatus:
        """Return the lifecycle status using the descriptive field name."""

        return self.status

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        """Return artifact IDs in declaration order."""

        return tuple(artifact.artifact_id for artifact in self.artifacts)

    @property
    def candidate_changes(self) -> tuple["EnterpriseCandidateChange", ...]:
        """Return typed changes attached to this candidate."""

        return self.changes


class EnterpriseChangeLineage(ContractModel):
    """Immutable evidence lineage for one enterprise candidate change."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    scope_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("scope_id", "improvement_scope_id"),
    )
    source_failure_ids: tuple[str, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices("source_failure_ids", "source_failures", "failure_ids"),
    )
    parent_candidate_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("parent_candidate_id", "parent_id"),
    )
    source_annotation_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("source_annotation_ids", "annotation_ids"),
    )
    generator_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("generator_id", "builder_id"),
    )
    reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_lineage(self) -> "EnterpriseChangeLineage":
        _validate_unique_ids("source_failure_ids", self.source_failure_ids)
        _validate_unique_ids("source_annotation_ids", self.source_annotation_ids)
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class EnterpriseCandidateChange(ContractModel):
    """One typed, bounded, and evidence-backed enterprise candidate change."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    change_id: str = Field(min_length=1)
    change_kind: ChangeKind = Field(
        validation_alias=AliasChoices("change_kind", "kind", "change_type")
    )
    affected_agent_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("affected_agent_id", "affected_agent", "agent_id", "agent"),
    )
    affected_artifact_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("affected_artifact_id", "affected_artifact", "artifact_id"),
    )
    affected_tool_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("affected_tool_id", "affected_tool", "tool_id"),
    )
    affected_skill_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("affected_skill_id", "affected_skill", "skill_id"),
    )
    affected_policy_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("affected_policy_id", "affected_policy", "policy_id"),
    )
    affected_permission_boundary: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "affected_permission_boundary",
            "permission_boundary",
            "permission_boundary_id",
        ),
    )
    before_reference: CandidateArtifactReference | None = Field(
        default=None,
        validation_alias=AliasChoices("before_reference", "before", "before_ref"),
    )
    after_reference: CandidateArtifactReference | None = Field(
        default=None,
        validation_alias=AliasChoices("after_reference", "after", "after_ref"),
    )
    changed_paths: tuple[str, ...] = Field(default=("$",), min_length=1)
    rationale: str = Field(min_length=1)
    source_failure_ids: tuple[str, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices("source_failure_ids", "source_failures", "failure_ids"),
    )
    expected_effect: str = Field(min_length=1)
    risk: ArtifactRiskClassification = Field(
        default=ArtifactRiskClassification.MEDIUM,
        validation_alias=AliasChoices("risk", "risk_level", "risk_classification"),
    )
    human_approval_required: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "human_approval_required",
            "requires_human_approval",
            "requires_approval",
        ),
    )
    diff: str = Field(default="", validation_alias=AliasChoices("diff", "unified_diff"))
    lineage: EnterpriseChangeLineage = Field(
        validation_alias=AliasChoices("lineage", "change_lineage")
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_change_targets(cls, value: Any) -> Any:
        """Accept IDs or typed artifact references for affected artifact targets."""

        if not isinstance(value, dict):
            return value
        data = dict(value)
        for canonical, aliases in (
            (
                "affected_artifact_id",
                ("affected_artifact_id", "affected_artifact", "artifact_id"),
            ),
            ("affected_tool_id", ("affected_tool_id", "affected_tool", "tool_id")),
            (
                "affected_skill_id",
                ("affected_skill_id", "affected_skill", "skill_id"),
            ),
            ("affected_policy_id", ("affected_policy_id", "affected_policy", "policy_id")),
        ):
            target = _first_present(data, aliases)
            if isinstance(target, (CandidateArtifact, CandidateArtifactReference)):
                data[canonical] = target.artifact_id
                for alias in aliases:
                    if alias != canonical:
                        data.pop(alias, None)
        lineage = _first_present(data, ("lineage", "change_lineage"))
        if isinstance(lineage, (EnterpriseCandidateLineage, EnterpriseChangeLineage)):
            data["lineage"] = lineage.model_dump(mode="python", exclude_none=True)
            data.pop("change_lineage", None)
        elif "change_lineage" in data:
            data["lineage"] = data.pop("change_lineage")
        return data

    @field_validator("changed_paths")
    @classmethod
    def normalize_changed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        paths = tuple(_normalize_change_path(path) for path in value)
        if len(paths) != len(set(paths)):
            raise ValueError("changed_paths must contain unique paths")
        return paths

    @model_validator(mode="after")
    def validate_change(self) -> "EnterpriseCandidateChange":
        _validate_unique_ids("source_failure_ids", self.source_failure_ids)
        if tuple(self.lineage.source_failure_ids) != tuple(self.source_failure_ids):
            raise ValueError("lineage.source_failure_ids must match source_failure_ids")

        targets = self._target_ids()
        if not targets and self.before_reference is None and self.after_reference is None:
            raise ValueError("A candidate change must identify an affected target")
        if not self._has_required_target():
            raise ValueError(f"{self.change_kind.value} needs its corresponding affected target")

        self._validate_references()
        return self

    def _target_ids(self) -> tuple[str, ...]:
        return tuple(
            target
            for target in (
                self.affected_artifact_id,
                self.affected_tool_id,
                self.affected_skill_id,
                self.affected_policy_id,
                self.affected_permission_boundary,
            )
            if target is not None
        )

    def _has_required_target(self) -> bool:
        artifact_target = bool(
            self.affected_artifact_id or self.before_reference or self.after_reference
        )
        if self.change_kind == ChangeKind.PROMPT_CHANGE:
            return artifact_target
        if self.change_kind in {
            ChangeKind.TOOL_ADDITION,
            ChangeKind.TOOL_REMOVAL,
            ChangeKind.TOOL_CONFIGURATION_CHANGE,
        }:
            return self.affected_tool_id is not None
        if self.change_kind in {
            ChangeKind.SKILL_ADDITION,
            ChangeKind.SKILL_REMOVAL,
        }:
            return self.affected_skill_id is not None
        if self.change_kind in {ChangeKind.POLICY_CHANGE, ChangeKind.APPROVAL_RULE_CHANGE}:
            return self.affected_policy_id is not None or artifact_target
        if self.change_kind == ChangeKind.PERMISSION_CHANGE:
            return bool(
                self.affected_tool_id
                or self.affected_skill_id
                or self.affected_policy_id
                or self.affected_permission_boundary
                or artifact_target
            )
        return bool(self._target_ids()) or artifact_target

    def _validate_references(self) -> None:
        if self.before_reference is not None and self.after_reference is not None:
            if self.before_reference.artifact_id == self.after_reference.artifact_id:
                raise ValueError(
                    "before_reference and after_reference must identify different artifacts"
                )
            if (
                self.before_reference.content_sha256 is not None
                and self.after_reference.content_sha256 is not None
                and self.before_reference.content_sha256 == self.after_reference.content_sha256
            ):
                raise ValueError("A candidate change must alter referenced content")

        if (
            self.change_kind
            in {
                ChangeKind.TOOL_ADDITION,
                ChangeKind.SKILL_ADDITION,
            }
            and self.before_reference is not None
        ):
            raise ValueError(f"{self.change_kind.value} cannot have a before_reference")
        if (
            self.change_kind
            in {
                ChangeKind.TOOL_REMOVAL,
                ChangeKind.SKILL_REMOVAL,
            }
            and self.after_reference is not None
        ):
            raise ValueError(f"{self.change_kind.value} cannot have an after_reference")

        if self.change_kind not in {
            ChangeKind.TOOL_ADDITION,
            ChangeKind.TOOL_REMOVAL,
            ChangeKind.SKILL_ADDITION,
            ChangeKind.SKILL_REMOVAL,
        }:
            if (self.before_reference is None) != (self.after_reference is None):
                raise ValueError("before_reference and after_reference must be supplied together")
            if (
                self.before_reference is None
                and self.after_reference is None
                and not self.diff.strip()
            ):
                raise ValueError(
                    "A candidate change needs before and after references or a non-empty diff"
                )

    def validate_in_scope(self, scope: "ImprovementScope") -> "EnterpriseCandidateChange":
        """Validate this change against one immutable improvement scope."""

        scope.validate_change(self)
        return self


class ImprovementScope(ContractModel):
    """Immutable allowlist and protected-resource boundary for improvements."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    scope_id: str = Field(min_length=1)
    allowed_change_kinds: tuple[ChangeKind, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices("allowed_change_kinds", "change_kinds"),
    )
    allowed_agents: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("allowed_agents", "allowed_agent_ids")
    )
    allowed_tools: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("allowed_tools", "allowed_tool_ids")
    )
    allowed_skills: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("allowed_skills", "allowed_skill_ids")
    )
    allowed_policies: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("allowed_policies", "allowed_policy_ids")
    )
    allowed_configuration_paths: tuple[str, ...] = ()
    protected_agents: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("protected_agents", "protected_agent_ids")
    )
    protected_tools: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("protected_tools", "protected_tool_ids")
    )
    protected_skills: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("protected_skills", "protected_skill_ids")
    )
    protected_policies: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("protected_policies", "protected_policy_ids")
    )
    protected_permission_boundaries: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices(
            "protected_permission_boundaries",
            "protected_permissions",
            "protected_permission_paths",
        ),
    )
    protected_datasets: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("protected_datasets", "protected_dataset_ids")
    )
    protected_evaluators: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("protected_evaluators", "protected_evaluator_ids"),
    )
    protected_promotion_rules: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("protected_promotion_rules", "protected_promotion_rule_ids"),
    )
    allowed_artifact_ids: tuple[str, ...] = ()
    allowed_artifact_kinds: tuple[CandidateArtifactKind, ...] = ()
    protected_artifact_ids: tuple[str, ...] = ()
    protected_configuration_paths: tuple[str, ...] = (
        "$.dataset",
        "$.datasets",
        "$.evaluation_dataset",
        "$.evaluation_datasets",
        "$.evaluator",
        "$.evaluators",
        "$.evaluator_code",
        "$.promotion",
        "$.promotion_rules",
    )
    max_prompt_chars: int | None = Field(default=None, gt=0)
    max_configuration_chars: int | None = Field(default=None, gt=0)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("allowed_configuration_paths", "protected_configuration_paths")
    @classmethod
    def normalize_configuration_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        paths = tuple(_normalize_change_path(path) for path in value)
        if len(paths) != len(set(paths)):
            raise ValueError("configuration paths must contain unique paths")
        return paths

    @model_validator(mode="after")
    def validate_scope(self) -> "ImprovementScope":
        for name, values in self._string_groups():
            _validate_unique_ids(name, values)
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty values")
        if len(self.allowed_change_kinds) != len(set(self.allowed_change_kinds)):
            raise ValueError("allowed_change_kinds must contain unique values")
        if len(self.allowed_artifact_kinds) != len(set(self.allowed_artifact_kinds)):
            raise ValueError("allowed_artifact_kinds must contain unique values")

        required_protected_paths = (
            "$.dataset",
            "$.datasets",
            "$.evaluation_dataset",
            "$.evaluation_datasets",
            "$.evaluator",
            "$.evaluators",
            "$.evaluator_code",
            "$.promotion",
            "$.promotion_rules",
        )
        protected_paths = tuple(
            dict.fromkeys((*self.protected_configuration_paths, *required_protected_paths))
        )
        object.__setattr__(self, "protected_configuration_paths", protected_paths)

        for allowed_name, protected_name in (
            ("allowed_agents", "protected_agents"),
            ("allowed_tools", "protected_tools"),
            ("allowed_skills", "protected_skills"),
            ("allowed_policies", "protected_policies"),
            ("allowed_artifact_ids", "protected_artifact_ids"),
        ):
            overlap = set(getattr(self, allowed_name)) & set(getattr(self, protected_name))
            if overlap:
                overlap_values = ", ".join(sorted(overlap))
                raise ValueError(f"{allowed_name} and {protected_name} overlap: {overlap_values}")
        return self

    def _string_groups(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("allowed_agents", self.allowed_agents),
            ("allowed_tools", self.allowed_tools),
            ("allowed_skills", self.allowed_skills),
            ("allowed_policies", self.allowed_policies),
            ("allowed_configuration_paths", self.allowed_configuration_paths),
            ("protected_agents", self.protected_agents),
            ("protected_tools", self.protected_tools),
            ("protected_skills", self.protected_skills),
            ("protected_policies", self.protected_policies),
            ("protected_permission_boundaries", self.protected_permission_boundaries),
            ("protected_datasets", self.protected_datasets),
            ("protected_evaluators", self.protected_evaluators),
            ("protected_promotion_rules", self.protected_promotion_rules),
            ("allowed_artifact_ids", self.allowed_artifact_ids),
            ("protected_artifact_ids", self.protected_artifact_ids),
            ("protected_configuration_paths", self.protected_configuration_paths),
        )

    def allows_change(self, change: EnterpriseCandidateChange) -> bool:
        """Return whether the scope accepts a typed candidate change."""

        try:
            self.validate_change(change)
        except ValueError:
            return False
        return True

    def validate_change(self, change: EnterpriseCandidateChange) -> None:
        """Raise a validation error when a change exceeds this scope."""

        if change.lineage.scope_id != self.scope_id:
            raise ValueError("Candidate change lineage does not reference this improvement scope")
        if change.change_kind not in self.allowed_change_kinds:
            raise ValueError(f"Change kind is not allowed by scope: {change.change_kind.value}")

        if change.affected_agent_id in self.protected_agents:
            raise ValueError(f"Protected agent cannot be changed: {change.affected_agent_id}")
        if self.allowed_agents and change.affected_agent_id not in self.allowed_agents:
            raise ValueError(f"Agent is outside the improvement scope: {change.affected_agent_id}")

        targets = self._change_target_values(change)
        self._reject_protected_target("tool", change.affected_tool_id, self.protected_tools)
        self._reject_protected_target("skill", change.affected_skill_id, self.protected_skills)
        self._reject_protected_target("policy", change.affected_policy_id, self.protected_policies)
        self._reject_protected_target(
            "permission boundary",
            change.affected_permission_boundary,
            self.protected_permission_boundaries,
        )
        self._reject_protected_targets("tool", targets, self.protected_tools)
        self._reject_protected_targets("skill", targets, self.protected_skills)
        self._reject_protected_targets("policy", targets, self.protected_policies)
        self._reject_protected_targets(
            "permission boundary",
            targets,
            self.protected_permission_boundaries,
        )
        self._reject_protected_targets(
            "artifact",
            targets,
            (
                *self.protected_artifact_ids,
                *self.protected_datasets,
                *self.protected_evaluators,
                *self.protected_promotion_rules,
            ),
        )

        self._validate_allowed_target(
            "tool", change.affected_tool_id, self.allowed_tools, change.change_kind
        )
        self._validate_allowed_target(
            "skill",
            change.affected_skill_id,
            self.allowed_skills,
            change.change_kind,
        )
        self._validate_allowed_target(
            "policy", change.affected_policy_id, self.allowed_policies, change.change_kind
        )
        self._validate_artifact_scope(change)
        self._validate_paths(change)

    def _change_target_values(self, change: EnterpriseCandidateChange) -> tuple[str, ...]:
        references = tuple(
            reference.artifact_id
            for reference in (change.before_reference, change.after_reference)
            if reference is not None
        )
        return tuple(dict.fromkeys((*change._target_ids(), *references)))

    @staticmethod
    def _reject_protected_target(
        label: str,
        target: str | None,
        protected: tuple[str, ...],
    ) -> None:
        if target is not None and target in protected:
            raise ValueError(f"Protected {label} cannot be changed: {target}")

    @staticmethod
    def _reject_protected_targets(
        label: str,
        targets: tuple[str, ...],
        protected: tuple[str, ...],
    ) -> None:
        overlap = sorted(set(targets) & set(protected))
        if overlap:
            values = ", ".join(overlap)
            raise ValueError(f"Protected {label} cannot be changed: {values}")

    @staticmethod
    def _validate_allowed_target(
        label: str,
        target: str | None,
        allowed: tuple[str, ...],
        change_kind: ChangeKind,
    ) -> None:
        if target is not None and allowed and target not in allowed:
            raise ValueError(f"{label.capitalize()} is outside the improvement scope: {target}")
        if (
            target is None
            and change_kind
            in {
                ChangeKind.TOOL_ADDITION,
                ChangeKind.TOOL_REMOVAL,
                ChangeKind.TOOL_CONFIGURATION_CHANGE,
            }
            and label == "tool"
        ):
            raise ValueError("Tool change needs an affected tool")

    def _validate_artifact_scope(self, change: EnterpriseCandidateChange) -> None:
        references = tuple(
            reference
            for reference in (change.before_reference, change.after_reference)
            if reference is not None
        )
        target_ids = tuple(
            dict.fromkeys(
                (
                    *(
                        (change.affected_artifact_id,)
                        if change.affected_artifact_id is not None
                        else ()
                    ),
                    *(reference.artifact_id for reference in references),
                )
            )
        )
        if self.allowed_artifact_ids and not set(target_ids).intersection(
            self.allowed_artifact_ids
        ):
            raise ValueError("Artifact target is outside the improvement scope")
        if self.allowed_artifact_kinds:
            kinds = tuple(reference.kind for reference in references if reference.kind is not None)
            if kinds and any(kind not in self.allowed_artifact_kinds for kind in kinds):
                raise ValueError("Artifact kind is outside the improvement scope")

    def _validate_paths(self, change: EnterpriseCandidateChange) -> None:
        configuration_change = change.change_kind in {
            ChangeKind.TOOL_CONFIGURATION_CHANGE,
            ChangeKind.PERMISSION_CHANGE,
            ChangeKind.POLICY_CHANGE,
            ChangeKind.MODEL_CHANGE,
            ChangeKind.ROUTING_CHANGE,
            ChangeKind.RETRIEVAL_CHANGE,
            ChangeKind.MEMORY_CHANGE,
            ChangeKind.THRESHOLD_CHANGE,
            ChangeKind.WORKFLOW_CHANGE,
            ChangeKind.APPROVAL_RULE_CHANGE,
        }
        if not configuration_change and change.change_kind == ChangeKind.PROMPT_CHANGE:
            configuration_change = any(
                reference.kind == CandidateArtifactKind.CONFIGURATION
                for reference in (change.before_reference, change.after_reference)
                if reference is not None
            )

        for path in change.changed_paths:
            if any(
                _path_is_within(path, protected)
                or (configuration_change and _path_is_within(protected, path))
                for protected in self.protected_configuration_paths
            ):
                raise ValueError(f"Changed path is protected: {path}")
            if any(
                _path_is_within(path, boundary)
                or (configuration_change and _path_is_within(boundary, path))
                for boundary in self.protected_permission_boundaries
                if boundary.startswith("$") or boundary.startswith(".")
            ):
                raise ValueError(f"Changed path is inside a protected permission boundary: {path}")

        if configuration_change and not self.allowed_configuration_paths:
            raise ValueError("Configuration changes need allowed_configuration_paths")
        if self.allowed_configuration_paths and (
            configuration_change or any(path != "$" for path in change.changed_paths)
        ):
            for path in change.changed_paths:
                if not any(
                    _path_is_within(path, allowed) for allowed in self.allowed_configuration_paths
                ):
                    raise ValueError(f"Changed path is outside the improvement scope: {path}")

    def allows_artifact(
        self,
        artifact: CandidateArtifact | CandidateArtifactReference,
    ) -> bool:
        """Return whether an artifact or artifact reference is in this scope."""

        artifact_id = artifact.artifact_id
        if artifact_id in self.protected_artifact_ids:
            return False
        if self.allowed_artifact_ids and artifact_id not in self.allowed_artifact_ids:
            return False
        kind = getattr(artifact, "kind", None)
        return not self.allowed_artifact_kinds or kind in self.allowed_artifact_kinds

    def validate_changes(
        self,
        changes: tuple[EnterpriseCandidateChange, ...],
    ) -> tuple[EnterpriseCandidateChange, ...]:
        """Validate a deterministic ordered set of changes."""

        change_ids = tuple(change.change_id for change in changes)
        _validate_unique_ids("change_ids", change_ids)
        for change in changes:
            self.validate_change(change)
        return changes


EnterpriseAgentCandidate.model_rebuild()


__all__ = [
    "ArtifactProvenance",
    "ArtifactRiskClassification",
    "CandidateArtifact",
    "CandidateArtifactKind",
    "CandidateArtifactReference",
    "CandidateComponentKind",
    "CandidateComponentReference",
    "CandidateStatus",
    "ChangeKind",
    "EnterpriseAgentCandidate",
    "EnterpriseCandidateChange",
    "EnterpriseCandidateLineage",
    "EnterpriseChangeLineage",
    "ImprovementScope",
]
