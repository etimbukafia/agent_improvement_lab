"""Constrained candidate generation and artifact diff rendering."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Protocol

from agent_improvement_lab.contracts.candidates import (
    AgentCandidate,
    CandidateChange,
    CandidateChangeSummary,
    CandidateGenerationRequest,
    CandidateLineage,
    GeneratedCandidatePlan,
    PromptArtifact,
    PromptArtifactKind,
)
from agent_improvement_lab.contracts.common import utc_now


class CandidateGenerationError(ValueError):
    """Raised when a generated candidate violates a scope or contract."""


class CandidateGenerator(Protocol):
    """Provider-neutral boundary for an LLM or another candidate generator."""

    def generate(self, request: CandidateGenerationRequest) -> GeneratedCandidatePlan:
        """Return a rationale and bounded artifact edits."""


@dataclass(frozen=True)
class CandidateBuildResult:
    """Generated candidate plus its immutable artifacts."""

    candidate: AgentCandidate
    artifacts: tuple[PromptArtifact, ...]

    @property
    def rendered_diff(self) -> str:
        """Return a human-readable diff for all changed artifacts."""

        return render_candidate_diff(self.candidate)


def create_candidate(
    request: CandidateGenerationRequest,
    generator: CandidateGenerator,
    *,
    created_at: datetime | None = None,
) -> CandidateBuildResult:
    """Create one candidate after validating every generated edit."""

    plan = generator.generate(request)
    if plan.generator_id != request.generator_id:
        raise CandidateGenerationError("Generator ID does not match the request")

    artifacts_by_id = {artifact.artifact_id: artifact for artifact in request.current_artifacts}
    parent_ids = (
        *request.parent_candidate.prompt_artifact_ids,
        *request.parent_candidate.configuration_artifact_ids,
    )
    parent_artifacts = {artifact_id: artifacts_by_id[artifact_id] for artifact_id in parent_ids}
    changes: list[CandidateChange] = []
    new_artifacts_by_base: dict[str, PromptArtifact] = {}
    timestamp = created_at or request.created_at or utc_now()

    for edit in plan.artifact_edits:
        base = parent_artifacts.get(edit.base_artifact_id)
        if base is None:
            raise CandidateGenerationError(
                f"Artifact edit targets an artifact outside the parent candidate: "
                f"{edit.base_artifact_id}"
            )
        if not request.scope.allows_artifact(base):
            raise CandidateGenerationError(
                f"Artifact is outside the candidate scope: {base.artifact_id}"
            )
        if base.content == edit.content:
            raise CandidateGenerationError(
                f"Artifact edit does not change content: {base.artifact_id}"
            )
        if base.kind == PromptArtifactKind.CONFIGURATION:
            changed_paths = _configuration_changed_paths(base.content, edit.content)
            if not changed_paths:
                raise CandidateGenerationError(
                    f"Configuration edit has no changed paths: {base.artifact_id}"
                )
        else:
            changed_paths = ("$",)
        if not request.scope.allows_paths(base, changed_paths):
            raise CandidateGenerationError(
                f"Artifact edit exceeds the candidate scope: {base.artifact_id}"
            )
        _validate_size(request, base.kind, edit.content, base.artifact_id)

        artifact_id = _new_artifact_id(request.candidate_id, base.artifact_id)
        if artifact_id in artifacts_by_id:
            raise CandidateGenerationError(f"Generated artifact ID already exists: {artifact_id}")
        artifact = PromptArtifact(
            artifact_id=artifact_id,
            name=base.name,
            version=request.version,
            kind=base.kind,
            content=edit.content,
            created_at=timestamp,
            metadata={
                **base.metadata,
                "parent_artifact_id": base.artifact_id,
                "candidate_id": request.candidate_id,
                "generator_id": request.generator_id,
            },
        )
        changes.append(
            CandidateChange(
                base_artifact_id=base.artifact_id,
                artifact_id=artifact.artifact_id,
                kind=base.kind,
                changed_paths=changed_paths,
                before_sha256=base.content_sha256 or _content_sha256(base.content),
                after_sha256=artifact.content_sha256 or _content_sha256(artifact.content),
                unified_diff=render_artifact_diff(base, artifact),
            )
        )
        new_artifacts_by_base[base.artifact_id] = artifact

    summary = CandidateChangeSummary(
        changes=tuple(changes),
        generator_summary=plan.change_summary,
    )
    lineage = CandidateLineage(
        parent_candidate_id=request.parent_candidate.candidate_id,
        source_failure_ids=tuple(failure.failure_id for failure in request.selected_failures),
        source_annotation_ids=tuple(
            annotation.annotation_id for annotation in request.confirmed_annotations
        ),
        scope_id=request.scope.scope_id,
        generator_id=request.generator_id,
        constraints=request.constraints,
        created_at=timestamp,
    )
    candidate = AgentCandidate(
        candidate_id=request.candidate_id,
        name=request.name,
        version=request.version,
        parent_candidate_id=request.parent_candidate.candidate_id,
        prompt_artifact_ids=_replace_artifact_ids(
            request.parent_candidate.prompt_artifact_ids, new_artifacts_by_base
        ),
        configuration_artifact_ids=_replace_artifact_ids(
            request.parent_candidate.configuration_artifact_ids, new_artifacts_by_base
        ),
        rationale=plan.rationale,
        created_at=timestamp,
        change_summary=summary,
        lineage=lineage,
        metadata={
            "scope_id": request.scope.scope_id,
            "generator_id": request.generator_id,
        },
    )
    return CandidateBuildResult(
        candidate=candidate, artifacts=tuple(new_artifacts_by_base.values())
    )


def render_candidate_diff(candidate: AgentCandidate) -> str:
    """Render all candidate artifact changes as one human-readable diff."""

    if candidate.change_summary is None:
        return ""
    return "\n\n".join(change.unified_diff for change in candidate.change_summary.changes)


def render_artifact_diff(before: PromptArtifact, after: PromptArtifact) -> str:
    """Render a stable unified diff for one prompt or configuration artifact."""

    before_lines = _diff_lines(before.content)
    after_lines = _diff_lines(after.content)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"{before.artifact_id}@{before.version}",
            tofile=f"{after.artifact_id}@{after.version}",
        )
    )


def _replace_artifact_ids(
    artifact_ids: tuple[str, ...], replacements: dict[str, PromptArtifact]
) -> tuple[str, ...]:
    result: list[str] = []
    for artifact_id in artifact_ids:
        replacement = replacements.get(artifact_id)
        result.append(replacement.artifact_id if replacement is not None else artifact_id)
    return tuple(result)


def _new_artifact_id(candidate_id: str, base_artifact_id: str) -> str:
    digest = sha256(f"{candidate_id}:{base_artifact_id}".encode("utf-8")).hexdigest()[:16]
    return f"artifact-{digest}"


def _content_sha256(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _diff_lines(content: str) -> list[str]:
    lines = content.splitlines(keepends=True)
    if content and (not lines or not lines[-1].endswith(("\n", "\r"))):
        lines[-1] += "\n"
    return lines


def _validate_size(
    request: CandidateGenerationRequest,
    kind: PromptArtifactKind,
    content: str,
    artifact_id: str,
) -> None:
    limit = (
        request.scope.max_configuration_chars
        if kind == PromptArtifactKind.CONFIGURATION
        else request.scope.max_prompt_chars
    )
    if limit is not None and len(content) > limit:
        raise CandidateGenerationError(
            f"Artifact exceeds the {kind.value} size limit: {artifact_id}"
        )


def _configuration_changed_paths(before: str, after: str) -> tuple[str, ...]:
    try:
        before_value = json.loads(before)
        after_value = json.loads(after)
    except json.JSONDecodeError as exc:
        raise CandidateGenerationError(f"Configuration content must be valid JSON: {exc}") from exc
    if not isinstance(before_value, dict) or not isinstance(after_value, dict):
        raise CandidateGenerationError("Configuration content must be a JSON object")
    paths: list[str] = []
    _collect_changed_paths(before_value, after_value, "$", paths)
    return tuple(paths)


def _collect_changed_paths(before: object, after: object, path: str, output: list[str]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}"
            if key not in before or key not in after:
                output.append(child_path)
            else:
                _collect_changed_paths(before[key], after[key], child_path, output)
        return
    if before != after:
        output.append(path)
