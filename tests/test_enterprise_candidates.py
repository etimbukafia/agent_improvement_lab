import json
from datetime import datetime
from hashlib import sha256

import pytest
from pydantic import ValidationError

from enterprise_agent_improvement_lab.contracts.candidates import (
    ArtifactProvenance,
    ArtifactRiskClassification,
    CandidateArtifact,
    CandidateArtifactKind,
    CandidateArtifactReference,
    CandidateComponentKind,
    CandidateComponentReference,
    EnterpriseAgentCandidate,
    EnterpriseCandidateLineage,
)
from enterprise_agent_improvement_lab.serialization import model_from_json, model_to_json
from enterprise_agent_improvement_lab.storage import RepositoryError, SQLiteStore


def _provenance(created_at: datetime, source_ref: str = "registry-1") -> ArtifactProvenance:
    return ArtifactProvenance(
        source="approved-registry",
        source_ref=source_ref,
        created_by="builder-1",
        created_at=created_at,
    )


def _artifact(
    created_at: datetime,
    artifact_id: str,
    kind: CandidateArtifactKind,
    content: str = '{"enabled":true}',
) -> CandidateArtifact:
    return CandidateArtifact(
        artifact_id=artifact_id,
        name=artifact_id,
        version="1.0.0",
        kind=kind,
        content=content,
        provenance=_provenance(created_at, artifact_id),
        owner="platform-team",
        risk_classification=ArtifactRiskClassification.MEDIUM,
        created_at=created_at,
    )


def _lineage(
    created_at: datetime,
    parent_candidate_id: str | None = "baseline-1",
) -> EnterpriseCandidateLineage:
    return EnterpriseCandidateLineage(
        parent_candidate_id=parent_candidate_id,
        source_failure_ids=("failure-1",),
        source_annotation_ids=("annotation-1",),
        improvement_scope_id="scope-1",
        generator_id="builder-1",
        environment_snapshot_ref="environment-1",
        reason="Address the observed enterprise failure.",
        created_at=created_at,
    )


def _candidate(
    created_at: datetime,
    *,
    candidate_id: str = "candidate-2",
    parent_candidate_id: str | None = "baseline-1",
    artifacts: tuple[CandidateArtifact | CandidateArtifactReference, ...] | None = None,
) -> EnterpriseAgentCandidate:
    selected = artifacts or (
        _artifact(created_at, "prompt-2", CandidateArtifactKind.SYSTEM_PROMPT),
    )
    return EnterpriseAgentCandidate(
        candidate_id=candidate_id,
        agent_id="orders-agent",
        version="2.0.0",
        parent_candidate_id=parent_candidate_id,
        artifacts=selected,
        prompt_ref=(
            selected[0].to_component_reference()
            if isinstance(selected[0], CandidateArtifact)
            and selected[0].kind == CandidateArtifactKind.SYSTEM_PROMPT
            else None
        ),
        runtime_profile="worker-profile-1",
        tool_refs=("tool:orders.read@1.0.0", "tool:orders.write@1.0.0"),
        skill_refs=("skill:order-management@1.0.0",),
        policy_refs=("policy:refund-policy-1@1.0.0",),
        model_configuration="model-config-2",
        memory_configuration="memory-config-1",
        retrieval_configuration="retrieval-config-1",
        routing_configuration="routing-config-1",
        approval_configuration="approval-config-1",
        workflow_configuration="workflow-config-1",
        lineage=_lineage(created_at, parent_candidate_id),
        created_at=created_at,
    )


def test_prompt_artifact_is_represented_by_the_general_artifact_contract(
    created_at: datetime,
):
    converted = CandidateArtifact(
        artifact_id="prompt-1",
        name="prompt-1",
        version="1.0.0",
        kind=CandidateArtifactKind.SYSTEM_PROMPT,
        content="Answer clearly.",
        provenance=_provenance(created_at, "prompt-1"),
        owner="prompt-team",
        created_at=created_at,
    )

    assert isinstance(converted, CandidateArtifact)
    assert converted.kind == CandidateArtifactKind.SYSTEM_PROMPT
    assert converted.content == "Answer clearly."
    assert converted.provenance.source_ref == "prompt-1"
    assert converted.to_reference().content_sha256 == converted.content_sha256


def test_non_prompt_enterprise_artifacts_are_typed_and_serializable(created_at: datetime):
    kinds = (
        CandidateArtifactKind.AGENT_DEFINITION,
        CandidateArtifactKind.TOOL_BINDING,
        CandidateArtifactKind.POLICY,
        CandidateArtifactKind.APPROVAL_POLICY,
        CandidateArtifactKind.WORKFLOW_CONFIGURATION,
    )
    artifacts = tuple(
        _artifact(
            created_at,
            f"artifact-{index}",
            kind,
            json.dumps({"kind": kind.value, "version": 1}, sort_keys=True),
        )
        for index, kind in enumerate(kinds)
    )

    restored = tuple(model_from_json(CandidateArtifact, model_to_json(item)) for item in artifacts)

    assert tuple(item.kind for item in restored) == kinds
    assert all(item.provenance.source == "approved-registry" for item in restored)


def test_artifact_checksum_is_stable_and_content_changes_are_rejected(created_at: datetime):
    content = '{"a":1,"b":2}'
    first = _artifact(created_at, "config-1", CandidateArtifactKind.CONFIGURATION, content)
    second = _artifact(created_at, "config-1", CandidateArtifactKind.CONFIGURATION, content)

    assert first.content_sha256 == second.content_sha256
    assert first.content_sha256 == sha256(content.encode("utf-8")).hexdigest()
    with pytest.raises(ValidationError, match="does not match content"):
        CandidateArtifact(
            artifact_id="config-2",
            name="config-2",
            version="1.0.0",
            kind=CandidateArtifactKind.CONFIGURATION,
            content=content,
            content_sha256="0" * 64,
            created_at=created_at,
        )


def test_enterprise_candidate_references_tools_policies_skills_and_runtime(
    created_at: datetime,
):
    candidate = _candidate(created_at)

    assert candidate.agent_id == "orders-agent"
    assert candidate.runtime_profile == "worker-profile-1"
    assert tuple(reference.identity for reference in candidate.tool_refs) == (
        "tool:orders.read@1.0.0",
        "tool:orders.write@1.0.0",
    )
    assert candidate.skill_refs[0].identity == "skill:order-management@1.0.0"
    assert candidate.policy_refs[0].identity == "policy:refund-policy-1@1.0.0"
    assert candidate.model_configuration == "model-config-2"
    assert candidate.memory_configuration == "memory-config-1"
    assert candidate.retrieval_configuration == "retrieval-config-1"
    assert candidate.routing_configuration == "routing-config-1"
    assert candidate.approval_configuration == "approval-config-1"
    assert candidate.workflow_configuration == "workflow-config-1"


def test_parent_and_child_candidate_ids_must_differ(created_at: datetime):
    with pytest.raises(ValidationError, match="parent_candidate_id must differ"):
        _candidate(created_at, candidate_id="baseline-1")


def test_duplicate_or_invalid_artifact_references_fail(created_at: datetime):
    artifact = _artifact(created_at, "prompt-1", CandidateArtifactKind.SYSTEM_PROMPT)
    with pytest.raises(ValidationError, match="unique artifact IDs"):
        _candidate(created_at, artifacts=(artifact, artifact.to_reference()))

    with pytest.raises(ValidationError):
        CandidateArtifactReference(artifact_id="prompt-1", content_sha256="not-a-checksum")


def test_candidate_component_references_require_exact_typed_identity(
    created_at: datetime,
):
    artifact = _artifact(created_at, "prompt-1", CandidateArtifactKind.SYSTEM_PROMPT)
    candidate = EnterpriseAgentCandidate(
        candidate_id="candidate-exact",
        agent_id="orders-agent",
        version="1.0.0",
        artifacts=(artifact.to_reference(),),
        prompt_ref=artifact.to_component_reference(),
        skill_refs=("skill:order-review@1.0.0",),
        tool_refs=("tool:orders.read@1.0.0",),
        policy_refs=("policy:orders-policy@1.0.0",),
    )

    assert isinstance(candidate.prompt_ref, CandidateComponentReference)
    assert candidate.prompt_ref.component_kind is CandidateComponentKind.PROMPT
    assert candidate.prompt_ref.source_artifact_id == artifact.artifact_id
    assert candidate.prompt_ref.source_artifact_sha256 == artifact.checksum
    assert candidate.skill_refs[0].version == "1.0.0"

    with pytest.raises(ValidationError, match="<kind>:<component_id>@<version>"):
        EnterpriseAgentCandidate(
            candidate_id="candidate-ambiguous",
            agent_id="orders-agent",
            version="1.0.0",
            artifacts=(artifact.to_reference(),),
            skill_refs=("order-review",),
        )

    with pytest.raises(ValidationError, match="skill_refs"):
        EnterpriseAgentCandidate(
            candidate_id="candidate-two-versions",
            agent_id="orders-agent",
            version="1.0.0",
            artifacts=(artifact.to_reference(),),
            skill_refs=(
                "skill:order-review@1.0.0",
                "skill:order-review@2.0.0",
            ),
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EnterpriseAgentCandidate(
            candidate_id="candidate-old-field",
            agent_id="orders-agent",
            version="1.0.0",
            artifacts=(artifact.to_reference(),),
            skills=("skill:order-review@1.0.0",),
        )

    with pytest.raises(ValidationError, match="skill references"):
        EnterpriseAgentCandidate(
            candidate_id="candidate-wrong-kind",
            agent_id="orders-agent",
            version="1.0.0",
            artifacts=(artifact.to_reference(),),
            skill_refs=("tool:orders.read@1.0.0",),
        )


def test_candidate_lineage_is_explicit_immutable_and_round_trips(created_at: datetime):
    candidate = _candidate(created_at)
    restored = model_from_json(EnterpriseAgentCandidate, model_to_json(candidate))

    assert restored == candidate
    assert restored.lineage.parent_candidate_id == "baseline-1"
    assert restored.lineage.source_failure_ids == ("failure-1",)
    with pytest.raises(ValidationError):
        candidate.lineage.parent_candidate_id = "other-candidate"


def test_new_artifact_and_candidate_repositories_preserve_immutability(
    tmp_path, created_at: datetime
):
    artifact = _artifact(created_at, "tool-binding-1", CandidateArtifactKind.TOOL_BINDING)
    candidate = _candidate(created_at, artifacts=(artifact,))

    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.candidate_artifacts.save(artifact)
        store.enterprise_candidates.save(candidate)

        assert store.candidate_artifacts.get(artifact.artifact_id) == artifact
        assert store.enterprise_candidates.get(candidate.candidate_id) == candidate
        with pytest.raises(RepositoryError, match="immutable"):
            store.candidate_artifacts.save(artifact.model_copy(update={"content": "changed"}))
