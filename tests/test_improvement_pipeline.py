from __future__ import annotations

from datetime import datetime, timezone

import pytest

from enterprise_agent_improvement_lab.candidate_builders import (
    ApprovalRuleCandidateBuilder,
    ModelCandidateBuilder,
    PolicyCandidateBuilder,
    PromptCandidateBuilder,
    RoutingCandidateBuilder,
    SkillCandidateBuilder,
    ThresholdCandidateBuilder,
    ToolBindingCandidateBuilder,
    WorkflowCandidateBuilder,
)
from enterprise_agent_improvement_lab.contracts.candidates import (
    ArtifactProvenance,
    CandidateArtifact,
    CandidateArtifactKind,
    ChangeKind,
    EnterpriseAgentCandidate,
    ImprovementScope,
)
from enterprise_agent_improvement_lab.contracts.failures import (
    EvaluationFailure,
    FailureCategory,
    FailureCluster,
    Severity,
)
from enterprise_agent_improvement_lab.contracts.improvement import (
    ImprovementDecision,
    RootCauseReviewerStatus,
)
from enterprise_agent_improvement_lab.improvement import ImprovementPlanner, RootCauseAnalyzer

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _artifact(
    artifact_id: str = "prompt-1",
    kind: CandidateArtifactKind = CandidateArtifactKind.SYSTEM_PROMPT,
    content: str = "Answer clearly.",
) -> CandidateArtifact:
    return CandidateArtifact(
        artifact_id=artifact_id,
        name=artifact_id,
        version="1.0.0",
        kind=kind,
        content=content,
        provenance=ArtifactProvenance(source="test", created_at=NOW),
        created_at=NOW,
    )


def _candidate(artifact: CandidateArtifact | None = None) -> EnterpriseAgentCandidate:
    artifact = artifact or _artifact()
    return EnterpriseAgentCandidate(
        candidate_id="candidate-1",
        agent_id="agent-1",
        version="1.0.0",
        artifacts=(artifact.to_reference(),),
        tool_refs=("tool:orders.read@1.0.0",),
        skill_refs=("skill:order-review@1.0.0",),
        policy_refs=("policy:orders-policy@1.0.0",),
    )


def _scope(*kinds: ChangeKind, **updates: object) -> ImprovementScope:
    values: dict[str, object] = {
        "scope_id": "scope-1",
        "allowed_change_kinds": kinds or tuple(ChangeKind),
        "allowed_agents": ("agent-1",),
        "allowed_tools": ("orders.read", "orders.write"),
        "allowed_skills": ("order-review", "order-write"),
        "allowed_policies": ("orders-policy",),
        "allowed_artifact_kinds": tuple(CandidateArtifactKind),
        "allowed_configuration_paths": (
            "$",
            "$.model",
            "$.routes",
            "$.thresholds",
            "$.rules",
            "$.approval",
            "$.transitions",
        ),
    }
    values.update(updates)
    return ImprovementScope(**values)


def _failure(
    failure_id: str = "failure-1",
    category: FailureCategory = FailureCategory.AUTHORIZATION,
    **updates: object,
) -> EvaluationFailure:
    values: dict[str, object] = {
        "failure_id": failure_id,
        "evaluator_id": f"{category.value}.check",
        "category": category,
        "severity": Severity.HIGH,
        "trace_id": "trace-1",
        "summary": "A typed enterprise check failed.",
        "expected_behavior": "The declared enterprise boundary is preserved.",
        "observed_behavior": "The boundary was not preserved.",
        "evidence_refs": (f"evidence:{failure_id}",),
        "created_at": NOW,
    }
    values.update(updates)
    return EvaluationFailure(**values)


def _cluster(failure: EvaluationFailure) -> FailureCluster:
    return FailureCluster(
        cluster_id="cluster-1",
        cluster_key="authorization",
        failure_ids=(failure.failure_id,),
        category=failure.category,
        title="Authorization failures",
        created_at=NOW,
        evaluator_id=failure.evaluator_id,
        affected_policy=failure.affected_policy,
    )


def test_root_cause_uses_explicit_failure_evidence_and_handles_conflict() -> None:
    first = _failure(affected_policy="orders-policy")
    second = _failure(
        "failure-2",
        affected_policy="orders-policy",
        suspected_root_cause_type="prompt",
    )
    cluster = FailureCluster(
        cluster_id="cluster-1",
        cluster_key="authorization",
        failure_ids=(first.failure_id, second.failure_id),
        category=first.category,
        title="Authorization failures",
        created_at=NOW,
        affected_policy="orders-policy",
    )

    hypothesis = RootCauseAnalyzer().analyze(cluster, (first, second))[0]

    assert hypothesis.supporting_evidence == ("failure-1",)
    assert hypothesis.conflicting_evidence == ("failure-2",)
    assert hypothesis.reviewer_status is RootCauseReviewerStatus.NEEDS_REVIEW
    assert hypothesis.effective_confidence < hypothesis.confidence


def test_planner_rejects_out_of_scope_interventions_and_returns_human_review() -> None:
    failure = _failure(affected_policy="orders-policy")
    cluster = _cluster(failure)
    hypothesis = RootCauseAnalyzer().analyze(cluster, (failure,))[0]
    scope = _scope(ChangeKind.PROMPT_CHANGE)

    plan = ImprovementPlanner().plan(cluster, (hypothesis,), _candidate(), None, scope)

    assert plan.decision is ImprovementDecision.HUMAN_REVIEW_REQUIRED
    assert plan.requires_human_review
    assert plan.candidate_builder_type == "human_review"


def test_planner_selects_the_allowed_typed_intervention() -> None:
    failure = _failure(affected_policy="orders-policy")
    cluster = _cluster(failure)
    hypothesis = RootCauseAnalyzer().analyze(cluster, (failure,))[0]
    scope = _scope(ChangeKind.POLICY_CHANGE)

    plan = ImprovementPlanner().plan(cluster, (hypothesis,), _candidate(), None, scope)

    assert plan.decision is ImprovementDecision.POLICY_CHANGE
    assert plan.candidate_builder_type == "PolicyCandidateBuilder"
    assert plan.source_failure_ids == ("failure-1",)


@pytest.mark.parametrize(
    ("builder", "kind", "artifact_kind", "target_id", "content", "paths"),
    (
        (
            PromptCandidateBuilder(),
            ChangeKind.PROMPT_CHANGE,
            CandidateArtifactKind.SYSTEM_PROMPT,
            "prompt-1",
            "Answer safely.",
            ("$",),
        ),
        (
            PolicyCandidateBuilder(),
            ChangeKind.POLICY_CHANGE,
            CandidateArtifactKind.POLICY,
            "orders-policy",
            '{"rules":{"read":true}}',
            ("$.rules",),
        ),
        (
            RoutingCandidateBuilder(),
            ChangeKind.ROUTING_CHANGE,
            CandidateArtifactKind.ROUTING_POLICY,
            "routing-2",
            '{"routes":[]}',
            ("$.routes",),
        ),
        (
            ModelCandidateBuilder(),
            ChangeKind.MODEL_CHANGE,
            CandidateArtifactKind.MODEL_CONFIGURATION,
            "model-2",
            '{"model":"safe"}',
            ("$.model",),
        ),
        (
            WorkflowCandidateBuilder(),
            ChangeKind.WORKFLOW_CHANGE,
            CandidateArtifactKind.WORKFLOW_CONFIGURATION,
            "workflow-2",
            '{"transitions":[]}',
            ("$.transitions",),
        ),
        (
            ThresholdCandidateBuilder(),
            ChangeKind.THRESHOLD_CHANGE,
            CandidateArtifactKind.CONFIGURATION,
            "threshold-2",
            '{"thresholds":{}}',
            ("$.thresholds",),
        ),
        (
            ApprovalRuleCandidateBuilder(),
            ChangeKind.APPROVAL_RULE_CHANGE,
            CandidateArtifactKind.APPROVAL_POLICY,
            "orders-policy",
            '{"approval":{}}',
            ("$.approval",),
        ),
        (
            SkillCandidateBuilder(),
            ChangeKind.SKILL_ADDITION,
            CandidateArtifactKind.SKILL_CONFIGURATION,
            "order-write",
            '{"skill_id":"order-write"}',
            ("$",),
        ),
    ),
)
def test_specialized_builders_create_reproducible_lineage(
    builder: object,
    kind: ChangeKind,
    artifact_kind: CandidateArtifactKind,
    target_id: str,
    content: str,
    paths: tuple[str, ...],
) -> None:
    base = (
        _artifact("prompt-1", artifact_kind, "Answer clearly.")
        if kind is ChangeKind.PROMPT_CHANGE
        else None
        if kind is ChangeKind.SKILL_ADDITION
        else _artifact(target_id, artifact_kind, '{"baseline":true}')
    )
    parent = _candidate(base) if base is not None else _candidate()
    scope = _scope(kind)

    result = builder.build(  # type: ignore[attr-defined]
        parent,
        scope,
        source_failure_ids=("failure-1",),
        current_artifacts=(base,) if base else (),
        base_artifact=base,
        target_id=target_id,
        target_kind=artifact_kind,
        replacement_content=content,
        target_registry_reference=(
            f"skill:{target_id}@1.0.0" if kind is ChangeKind.SKILL_ADDITION else None
        ),
        changed_paths=paths,
        created_at=NOW,
    )

    assert result.change.change_kind is kind
    assert result.change.lineage.source_failure_ids == ("failure-1",)
    assert result.candidate.parent_candidate_id == parent.candidate_id
    assert result.rendered_summary
    assert (
        result.candidate.candidate_id
        == builder.build(  # type: ignore[attr-defined]
            parent,
            scope,
            source_failure_ids=("failure-1",),
            current_artifacts=(base,) if base else (),
            base_artifact=base,
            target_id=target_id,
            target_kind=artifact_kind,
            replacement_content=content,
            target_registry_reference=(
                f"skill:{target_id}@1.0.0" if kind is ChangeKind.SKILL_ADDITION else None
            ),
            changed_paths=paths,
            created_at=NOW,
        ).candidate.candidate_id
    )


def test_tool_binding_builder_adds_tool_and_preserves_registry_evidence() -> None:
    parent = _candidate()
    scope = _scope(ChangeKind.TOOL_ADDITION)

    result = ToolBindingCandidateBuilder().build(
        parent,
        scope,
        source_failure_ids=("failure-1",),
        target_id="orders.write",
        target_kind=CandidateArtifactKind.TOOL_BINDING,
        target_registry_reference="tool:orders.write@1.0.0",
        replacement_content='{"name":"orders.write","operation":"write"}',
        created_at=NOW,
    )

    binding = next(
        artifact
        for artifact in result.artifacts
        if artifact.kind is CandidateArtifactKind.TOOL_BINDING
    )
    assert result.change.change_kind is ChangeKind.TOOL_ADDITION
    assert result.change.affected_tool_id == "orders.write"
    assert result.change.after_reference is not None
    assert result.change.after_reference.registry_reference == "tool:orders.write@1.0.0"
    assert binding.registry_reference == "tool:orders.write@1.0.0"
    assert tuple(reference.identity for reference in result.candidate.tool_refs) == (
        "tool:orders.read@1.0.0",
        "tool:orders.write@1.0.0",
    )
    assert result.change.source_failure_ids == ("failure-1",)


def test_builder_rejects_protected_target() -> None:
    scope = _scope(
        ChangeKind.PROMPT_CHANGE,
        protected_artifact_ids=("prompt-1",),
    )

    with pytest.raises(ValueError, match="Protected"):
        PromptCandidateBuilder().build(
            _candidate(),
            scope,
            source_failure_ids=("failure-1",),
            current_artifacts=(_artifact(),),
            base_artifact=_artifact(),
            target_id="prompt-1",
            replacement_content="Changed.",
            created_at=NOW,
        )
