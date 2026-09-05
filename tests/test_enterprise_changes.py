from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifactKind,
    CandidateArtifactReference,
    ChangeKind,
    EnterpriseAgentCandidate,
    EnterpriseCandidateChange,
    EnterpriseCandidateLineage,
    EnterpriseChangeLineage,
    ImprovementScope,
)

_CHANGE_KINDS = tuple(ChangeKind)


def _reference(
    reference_id: str,
    kind: CandidateArtifactKind = CandidateArtifactKind.CONFIGURATION,
) -> CandidateArtifactReference:
    return CandidateArtifactReference(
        artifact_id=reference_id,
        version="1.0.0",
        kind=kind,
        content_sha256=sha256(reference_id.encode("utf-8")).hexdigest(),
    )


def _lineage(
    scope_id: str = "scope-1",
    source_failure_ids: tuple[str, ...] = ("failure-1",),
) -> EnterpriseChangeLineage:
    return EnterpriseChangeLineage(
        scope_id=scope_id,
        source_failure_ids=source_failure_ids,
        parent_candidate_id="candidate-1",
        generator_id="planner-1",
    )


def _change(
    kind: ChangeKind,
    *,
    affected_agent_id: str = "agent-1",
    affected_artifact_id: str | None = None,
    affected_tool_id: str | None = None,
    affected_skill_id: str | None = None,
    affected_policy_id: str | None = None,
    before_reference: CandidateArtifactReference | None = None,
    after_reference: CandidateArtifactReference | None = None,
    changed_paths: tuple[str, ...] = ("$",),
    source_failure_ids: tuple[str, ...] = ("failure-1",),
    scope_id: str = "scope-1",
) -> EnterpriseCandidateChange:
    return EnterpriseCandidateChange(
        change_id=f"change-{kind.value}",
        change_kind=kind,
        affected_agent_id=affected_agent_id,
        affected_artifact_id=affected_artifact_id,
        affected_tool_id=affected_tool_id,
        affected_skill_id=affected_skill_id,
        affected_policy_id=affected_policy_id,
        before_reference=before_reference,
        after_reference=after_reference,
        changed_paths=changed_paths,
        rationale="Address the observed enterprise failure.",
        source_failure_ids=source_failure_ids,
        expected_effect="The targeted behavior improves without a protected regression.",
        risk="medium",
        human_approval_required=True,
        lineage=_lineage(scope_id, source_failure_ids),
    )


def _kind_change(kind: ChangeKind) -> EnterpriseCandidateChange:
    if kind == ChangeKind.PROMPT_CHANGE:
        return _change(
            kind,
            affected_artifact_id="prompt-after",
            before_reference=_reference("prompt-before", CandidateArtifactKind.SYSTEM_PROMPT),
            after_reference=_reference("prompt-after", CandidateArtifactKind.SYSTEM_PROMPT),
        )
    if kind == ChangeKind.TOOL_ADDITION:
        return _change(
            kind,
            affected_tool_id="tool-new",
            after_reference=_reference("tool-binding-new", CandidateArtifactKind.TOOL_BINDING),
        )
    if kind == ChangeKind.TOOL_REMOVAL:
        return _change(
            kind,
            affected_tool_id="tool-old",
            before_reference=_reference("tool-binding-old", CandidateArtifactKind.TOOL_BINDING),
        )
    if kind == ChangeKind.TOOL_CONFIGURATION_CHANGE:
        return _change(
            kind,
            affected_tool_id="tool-1",
            affected_artifact_id="tool-config-after",
            before_reference=_reference(
                "tool-config-before", CandidateArtifactKind.TOOL_CONFIGURATION
            ),
            after_reference=_reference(
                "tool-config-after", CandidateArtifactKind.TOOL_CONFIGURATION
            ),
            changed_paths=("$.timeout",),
        )
    if kind == ChangeKind.PERMISSION_CHANGE:
        return _change(
            kind,
            affected_tool_id="tool-1",
            affected_artifact_id="permission-after",
            before_reference=_reference("permission-before", CandidateArtifactKind.POLICY),
            after_reference=_reference("permission-after", CandidateArtifactKind.POLICY),
            changed_paths=("$.permissions.orders.read",),
        )
    if kind == ChangeKind.POLICY_CHANGE:
        return _change(
            kind,
            affected_policy_id="policy-after",
            before_reference=_reference("policy-before", CandidateArtifactKind.POLICY),
            after_reference=_reference("policy-after", CandidateArtifactKind.POLICY),
            changed_paths=("$.rules.refund",),
        )
    if kind == ChangeKind.MODEL_CHANGE:
        return _change(
            kind,
            affected_artifact_id="model-after",
            before_reference=_reference("model-before", CandidateArtifactKind.MODEL_CONFIGURATION),
            after_reference=_reference("model-after", CandidateArtifactKind.MODEL_CONFIGURATION),
            changed_paths=("$.model",),
        )
    if kind == ChangeKind.ROUTING_CHANGE:
        return _change(
            kind,
            affected_artifact_id="routing-after",
            before_reference=_reference("routing-before", CandidateArtifactKind.ROUTING_POLICY),
            after_reference=_reference("routing-after", CandidateArtifactKind.ROUTING_POLICY),
            changed_paths=("$.routes",),
        )
    if kind == ChangeKind.RETRIEVAL_CHANGE:
        return _change(
            kind,
            affected_artifact_id="retrieval-after",
            before_reference=_reference(
                "retrieval-before", CandidateArtifactKind.RETRIEVAL_CONFIGURATION
            ),
            after_reference=_reference(
                "retrieval-after", CandidateArtifactKind.RETRIEVAL_CONFIGURATION
            ),
            changed_paths=("$.sources",),
        )
    if kind == ChangeKind.MEMORY_CHANGE:
        return _change(
            kind,
            affected_artifact_id="memory-after",
            before_reference=_reference(
                "memory-before", CandidateArtifactKind.MEMORY_CONFIGURATION
            ),
            after_reference=_reference("memory-after", CandidateArtifactKind.MEMORY_CONFIGURATION),
            changed_paths=("$.retention",),
        )
    if kind == ChangeKind.THRESHOLD_CHANGE:
        return _change(
            kind,
            affected_artifact_id="threshold-after",
            before_reference=_reference("threshold-before"),
            after_reference=_reference("threshold-after"),
            changed_paths=("$.thresholds.confidence",),
        )
    if kind == ChangeKind.WORKFLOW_CHANGE:
        return _change(
            kind,
            affected_artifact_id="workflow-after",
            before_reference=_reference(
                "workflow-before", CandidateArtifactKind.WORKFLOW_CONFIGURATION
            ),
            after_reference=_reference(
                "workflow-after", CandidateArtifactKind.WORKFLOW_CONFIGURATION
            ),
            changed_paths=("$.transitions",),
        )
    if kind == ChangeKind.SKILL_ADDITION:
        return _change(
            kind,
            affected_skill_id="skill-new",
            after_reference=_reference(
                "skill-config-new", CandidateArtifactKind.SKILL_CONFIGURATION
            ),
        )
    if kind == ChangeKind.SKILL_REMOVAL:
        return _change(
            kind,
            affected_skill_id="skill-old",
            before_reference=_reference(
                "skill-config-old", CandidateArtifactKind.SKILL_CONFIGURATION
            ),
        )
    return _change(
        kind,
        affected_policy_id="approval-policy-after",
        before_reference=_reference(
            "approval-policy-before", CandidateArtifactKind.APPROVAL_POLICY
        ),
        after_reference=_reference("approval-policy-after", CandidateArtifactKind.APPROVAL_POLICY),
        changed_paths=("$.approval",),
    )


def _scope(**updates: object) -> ImprovementScope:
    values: dict[str, object] = {
        "scope_id": "scope-1",
        "allowed_change_kinds": _CHANGE_KINDS,
        "allowed_agents": ("agent-1",),
        "allowed_configuration_paths": (
            "$.timeout",
            "$.permissions",
            "$.rules",
            "$.model",
            "$.routes",
            "$.sources",
            "$.retention",
            "$.thresholds",
            "$.transitions",
            "$.approval",
        ),
    }
    values.update(updates)
    return ImprovementScope(**values)


@pytest.mark.parametrize("kind", _CHANGE_KINDS)
def test_each_supported_change_kind_is_typed(kind: ChangeKind):
    change = _kind_change(kind)

    assert change.change_kind is kind
    assert change.source_failure_ids == ("failure-1",)
    assert change.lineage.scope_id == "scope-1"


def test_protected_agents_tools_policies_and_permission_boundaries_are_rejected():
    scope = _scope(
        protected_agents=("agent-protected",),
        protected_tools=("tool-protected",),
        protected_policies=("policy-protected",),
        protected_permission_boundaries=("permission-protected",),
    )

    protected_changes = (
        _kind_change(ChangeKind.PROMPT_CHANGE).model_copy(
            update={"affected_agent_id": "agent-protected"}
        ),
        _kind_change(ChangeKind.TOOL_CONFIGURATION_CHANGE).model_copy(
            update={"affected_tool_id": "tool-protected"}
        ),
        _kind_change(ChangeKind.POLICY_CHANGE).model_copy(
            update={"affected_policy_id": "policy-protected"}
        ),
        _kind_change(ChangeKind.PERMISSION_CHANGE).model_copy(
            update={"affected_permission_boundary": "permission-protected"}
        ),
    )

    for change in protected_changes:
        with pytest.raises(ValueError, match="Protected"):
            scope.validate_change(change)


def test_disallowed_change_kind_is_rejected():
    scope = _scope(allowed_change_kinds=(ChangeKind.PROMPT_CHANGE,))

    with pytest.raises(ValueError, match="not allowed"):
        scope.validate_change(_kind_change(ChangeKind.TOOL_ADDITION))


def test_scope_metadata_cannot_expand_typed_permissions():
    scope = _scope(
        allowed_change_kinds=(ChangeKind.PROMPT_CHANGE,),
        metadata={"allowed_change_kinds": "tool_addition"},
    )

    assert not scope.allows_change(_kind_change(ChangeKind.TOOL_ADDITION))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("protected_datasets", "dataset-1"),
        ("protected_evaluators", "evaluator-1"),
        ("protected_promotion_rules", "promotion-rule-1"),
    ),
)
def test_evaluation_and_promotion_assets_are_always_protected(field: str, value: str):
    scope = _scope(**{field: (value,)})
    change = _kind_change(ChangeKind.PROMPT_CHANGE).model_copy(
        update={"affected_artifact_id": value}
    )

    with pytest.raises(ValueError, match="Protected artifact"):
        scope.validate_change(change)


def test_changed_paths_must_stay_inside_the_allowed_scope():
    scope = _scope(
        allowed_change_kinds=(ChangeKind.MODEL_CHANGE,),
        allowed_configuration_paths=("$.model",),
    )
    valid = _kind_change(ChangeKind.MODEL_CHANGE)
    invalid = valid.model_copy(update={"changed_paths": ("$.routing",)})

    scope.validate_change(valid)
    with pytest.raises(ValueError, match="outside the improvement scope"):
        scope.validate_change(invalid)

    broad = _scope(
        allowed_change_kinds=(ChangeKind.MODEL_CHANGE,),
        allowed_configuration_paths=("$",),
    )
    with pytest.raises(ValueError, match="protected"):
        broad.validate_change(valid.model_copy(update={"changed_paths": ("$",)}))


def test_change_requires_source_evidence_and_matching_lineage():
    with pytest.raises(ValidationError):
        _change(ChangeKind.PROMPT_CHANGE, source_failure_ids=())

    values = _kind_change(ChangeKind.PROMPT_CHANGE).model_dump()
    values["lineage"] = _lineage(source_failure_ids=("failure-2",))
    with pytest.raises(ValidationError, match="must match"):
        EnterpriseCandidateChange(**values)


def test_duplicate_paths_targets_and_unchanged_references_fail():
    values = _kind_change(ChangeKind.MODEL_CHANGE).model_dump()
    values["changed_paths"] = ("$.model", "model")
    with pytest.raises(ValidationError, match="unique paths"):
        EnterpriseCandidateChange(**values)

    with pytest.raises(ValidationError, match="affected target"):
        _change(ChangeKind.TOOL_ADDITION, affected_tool_id=None, after_reference=None)

    reference = _reference("same", CandidateArtifactKind.SYSTEM_PROMPT)
    with pytest.raises(ValidationError, match="different artifacts"):
        _change(
            ChangeKind.PROMPT_CHANGE,
            affected_artifact_id="same",
            before_reference=reference,
            after_reference=reference,
        )

    change = _kind_change(ChangeKind.PROMPT_CHANGE)
    with pytest.raises(ValueError, match="change_ids"):
        _scope().validate_changes((change, change))


def test_typed_changes_can_be_attached_to_the_enterprise_candidate():
    change = _kind_change(ChangeKind.PROMPT_CHANGE)
    candidate = EnterpriseAgentCandidate(
        candidate_id="candidate-2",
        agent_id="agent-1",
        version="2.0.0",
        parent_candidate_id="candidate-1",
        artifacts=(change.after_reference,),
        changes=(change,),
        lineage=EnterpriseCandidateLineage(
            parent_candidate_id="candidate-1",
            source_failure_ids=("failure-1",),
            improvement_scope_id="scope-1",
        ),
    )

    assert candidate.candidate_changes == (change,)


def test_improvement_scope_keeps_change_permissions_explicit_and_immutable():
    scope = _scope(allowed_artifact_ids=("artifact-1",))

    assert scope.allowed_change_kinds == _CHANGE_KINDS
    assert scope.allowed_artifact_ids == ("artifact-1",)
    with pytest.raises(ValidationError):
        scope.allowed_artifact_ids = ("artifact-2",)
