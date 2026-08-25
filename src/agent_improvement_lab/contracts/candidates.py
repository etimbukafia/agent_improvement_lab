"""Candidate and prompt-artifact contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import ConfigDict, Field, field_validator, model_validator

from agent_improvement_lab.contracts.common import ContractModel, VersionString, require_aware_utc
from agent_improvement_lab.contracts.failures import EvaluationFailure, HumanAnnotation


class CandidateStatus(StrEnum):
    """Lifecycle state for a candidate."""

    DRAFT = "draft"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETIRED = "retired"


class PromptArtifactKind(StrEnum):
    """Type of immutable candidate artifact."""

    SYSTEM_PROMPT = "system_prompt"
    DEVELOPER_PROMPT = "developer_prompt"
    USER_TEMPLATE = "user_template"
    CONFIGURATION = "configuration"


class CandidateScope(ContractModel):
    """Allowlist for the artifacts and configuration paths a generator may change."""

    scope_id: str = Field(min_length=1)
    allowed_artifact_ids: tuple[str, ...] = ()
    allowed_artifact_kinds: tuple[PromptArtifactKind, ...] = ()
    protected_artifact_ids: tuple[str, ...] = ()
    allowed_configuration_paths: tuple[str, ...] = ()
    protected_configuration_paths: tuple[str, ...] = (
        "$.dataset",
        "$.datasets",
        "$.labels",
        "$.case_labels",
        "$.evaluator",
        "$.evaluators",
        "$.evaluator_code",
        "$.promotion",
        "$.promotion_rules",
    )
    max_prompt_chars: int | None = Field(default=None, gt=0)
    max_configuration_chars: int | None = Field(default=None, gt=0)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope(self) -> "CandidateScope":
        for name, values in (
            ("allowed_artifact_ids", self.allowed_artifact_ids),
            ("protected_artifact_ids", self.protected_artifact_ids),
            ("allowed_configuration_paths", self.allowed_configuration_paths),
            ("protected_configuration_paths", self.protected_configuration_paths),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique values")
        if not self.allowed_artifact_ids and not self.allowed_artifact_kinds:
            raise ValueError("A candidate scope needs an artifact ID or artifact kind allowlist")
        if (
            PromptArtifactKind.CONFIGURATION in self.allowed_artifact_kinds
            and not self.allowed_configuration_paths
        ):
            raise ValueError("Configuration scopes need allowed_configuration_paths")
        overlap = set(self.allowed_artifact_ids) & set(self.protected_artifact_ids)
        if overlap:
            raise ValueError("An artifact cannot be both allowed and protected")
        return self

    def allows_artifact(self, artifact: "PromptArtifact") -> bool:
        """Return whether this scope allows edits to an artifact."""

        if artifact.artifact_id in self.protected_artifact_ids:
            return False
        return (
            artifact.artifact_id in self.allowed_artifact_ids
            or artifact.kind in self.allowed_artifact_kinds
        )

    def allows_paths(self, artifact: "PromptArtifact", paths: tuple[str, ...]) -> bool:
        """Return whether all changed paths are within the scope."""

        if not self.allows_artifact(artifact):
            return False
        if artifact.kind != PromptArtifactKind.CONFIGURATION:
            return True
        for path in paths:
            if any(
                _path_is_within(path, protected) for protected in self.protected_configuration_paths
            ):
                return False
            if not any(
                _path_is_within(path, allowed) for allowed in self.allowed_configuration_paths
            ):
                return False
        return True


def _path_is_within(path: str, parent: str) -> bool:
    """Return whether a JSON path is equal to or below another JSON path."""

    normalized_path = path if path.startswith("$") else f"$.{path.lstrip('.')}"
    normalized_parent = parent if parent.startswith("$") else f"$.{parent.lstrip('.')}"
    return normalized_path == normalized_parent or normalized_path.startswith(
        normalized_parent + "."
    )


class PromptArtifact(ContractModel):
    """An immutable text or JSON artifact with a content checksum."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=False,
        validate_assignment=True,
    )

    artifact_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: VersionString
    kind: PromptArtifactKind
    content: str = Field(min_length=1)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_checksum(self) -> "PromptArtifact":
        digest = sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_sha256 is not None and self.content_sha256 != digest:
            raise ValueError("content_sha256 does not match content")
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class ArtifactEdit(ContractModel):
    """One proposed replacement for an existing immutable artifact."""

    base_artifact_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    change_paths: tuple[str, ...] = Field(min_length=1)

    @field_validator("change_paths")
    @classmethod
    def validate_change_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("change_paths must contain unique paths")
        return value


class CandidateChange(ContractModel):
    """One immutable artifact change in a generated candidate."""

    base_artifact_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    kind: PromptArtifactKind
    changed_paths: tuple[str, ...] = Field(min_length=1)
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unified_diff: str = ""

    @model_validator(mode="after")
    def validate_change(self) -> "CandidateChange":
        if self.before_sha256 == self.after_sha256:
            raise ValueError("A candidate change must alter artifact content")
        return self


class CandidateChangeSummary(ContractModel):
    """Machine-readable summary of all changes in a candidate."""

    summary_version: VersionString = "1.0.0"
    changes: tuple[CandidateChange, ...] = Field(min_length=1)
    generator_summary: dict[str, str] = Field(default_factory=dict)
    protected_targets_unchanged: bool = True

    @model_validator(mode="after")
    def validate_summary(self) -> "CandidateChangeSummary":
        artifact_ids = [change.artifact_id for change in self.changes]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("Candidate changes must have unique artifact IDs")
        if not self.protected_targets_unchanged:
            raise ValueError("Candidate changes must leave protected targets unchanged")
        return self


class CandidateLineage(ContractModel):
    """Source records and constraints for one generated candidate."""

    parent_candidate_id: str = Field(min_length=1)
    source_failure_ids: tuple[str, ...] = Field(min_length=1)
    source_annotation_ids: tuple[str, ...] = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    generator_id: str = Field(min_length=1)
    constraints: tuple[str, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "CandidateLineage":
        for name, values in (
            ("source_failure_ids", self.source_failure_ids),
            ("source_annotation_ids", self.source_annotation_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique values")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class AgentCandidate(ContractModel):
    """A versioned candidate made from immutable artifacts."""

    candidate_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: VersionString
    status: CandidateStatus = CandidateStatus.DRAFT
    parent_candidate_id: str | None = None
    prompt_artifact_ids: tuple[str, ...] = ()
    configuration_artifact_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)
    created_at: datetime
    change_summary: CandidateChangeSummary | None = None
    lineage: CandidateLineage | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_candidate(self) -> "AgentCandidate":
        if self.parent_candidate_id == self.candidate_id:
            raise ValueError("parent_candidate_id must differ from candidate_id")
        if not self.prompt_artifact_ids and not self.configuration_artifact_ids:
            raise ValueError("Candidate must reference at least one artifact")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        if self.lineage is not None and self.lineage.parent_candidate_id == self.candidate_id:
            raise ValueError("lineage.parent_candidate_id must differ from candidate_id")
        return self


class CandidateGenerationRequest(ContractModel):
    """Inputs supplied to a constrained candidate generator."""

    candidate_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: VersionString
    parent_candidate: AgentCandidate
    current_artifacts: tuple[PromptArtifact, ...] = Field(min_length=1)
    selected_failures: tuple[EvaluationFailure, ...] = Field(min_length=1)
    confirmed_annotations: tuple[HumanAnnotation, ...] = Field(min_length=1)
    scope: CandidateScope
    constraints: tuple[str, ...] = ()
    generator_id: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_request(self) -> "CandidateGenerationRequest":
        if self.candidate_id == self.parent_candidate.candidate_id:
            raise ValueError("candidate_id must differ from the parent candidate")
        artifact_ids = [artifact.artifact_id for artifact in self.current_artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("current_artifacts must have unique IDs")
        missing = sorted(
            (
                set(self.parent_candidate.prompt_artifact_ids)
                | set(self.parent_candidate.configuration_artifact_ids)
            )
            - set(artifact_ids)
        )
        if missing:
            raise ValueError(f"Parent candidate references missing artifacts: {', '.join(missing)}")
        failure_ids = [failure.failure_id for failure in self.selected_failures]
        if len(failure_ids) != len(set(failure_ids)):
            raise ValueError("selected_failures must have unique IDs")
        annotation_ids = [annotation.annotation_id for annotation in self.confirmed_annotations]
        if len(annotation_ids) != len(set(annotation_ids)):
            raise ValueError("confirmed_annotations must have unique IDs")
        if any(
            annotation.status.value not in {"confirmed", "regression_candidate", "golden"}
            for annotation in self.confirmed_annotations
        ):
            raise ValueError("Candidate generation needs confirmed annotations")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class GeneratedCandidatePlan(ContractModel):
    """Generator output before the Lab validates and materializes artifacts."""

    rationale: str = Field(min_length=1)
    change_summary: dict[str, str] = Field(min_length=1)
    artifact_edits: tuple[ArtifactEdit, ...] = Field(min_length=1)
    generator_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_plan(self) -> "GeneratedCandidatePlan":
        artifact_ids = [edit.base_artifact_id for edit in self.artifact_edits]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact_edits must contain one edit per base artifact")
        return self
