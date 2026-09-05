"""Deterministic root-cause analysis and improvement planning.

The Lab records short causal hypotheses.  It does not expose private model
reasoning and it does not generate executable code.  Candidate construction is
left to the specialized builders in :mod:`candidate_builders`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from hashlib import sha256

from enterprise_agent_improvement_lab.contracts.candidates import (
    ArtifactRiskClassification,
    ChangeKind,
    EnterpriseAgentCandidate,
    ImprovementScope,
)
from enterprise_agent_improvement_lab.contracts.common import utc_now
from enterprise_agent_improvement_lab.contracts.environments import EnvironmentSnapshot
from enterprise_agent_improvement_lab.contracts.failures import (
    EvaluationFailure,
    FailureCategory,
    FailureCluster,
)
from enterprise_agent_improvement_lab.contracts.improvement import (
    ImprovementDecision,
    ImprovementPlan,
    PriorExperimentEvidence,
    RootCauseHypothesis,
    RootCauseReviewerStatus,
)
from enterprise_agent_improvement_lab.serialization import stable_json_dumps


class ImprovementPlanningError(ValueError):
    """Raised when evidence is not sufficient for a safe planning decision."""


class RootCauseAnalyzer:
    """Build one concise, deterministic hypothesis for a failure cluster."""

    def analyze(
        self,
        cluster: FailureCluster,
        failures: Sequence[EvaluationFailure],
        *,
        agent_id: str | None = None,
        created_at: datetime | None = None,
    ) -> tuple[RootCauseHypothesis, ...]:
        """Analyze one cluster using typed failure evidence.

        The returned hypothesis is deterministic for the same cluster and
        failure records.  Failure IDs are retained as evidence references;
        raw prompts, tool arguments, and tool results are never copied.
        """

        failure_by_id = {failure.failure_id: failure for failure in failures}
        missing = sorted(set(cluster.failure_ids) - set(failure_by_id))
        if missing:
            raise ImprovementPlanningError(
                "Root-cause analysis is missing cluster evidence: " + ", ".join(missing)
            )
        if len(failure_by_id) != len(failures):
            raise ImprovementPlanningError("Failure evidence IDs must be unique")

        evidence = tuple(
            failure_id for failure_id in cluster.failure_ids if failure_id in failure_by_id
        )
        grouped = tuple(failure_by_id[failure_id] for failure_id in evidence)
        category = cluster.category
        rule_id, cause, interventions, base_confidence = _rule_for(category, grouped)

        explicit_types = {
            failure.suspected_root_cause_type
            for failure in grouped
            if failure.suspected_root_cause_type
        }
        conflicting: tuple[str, ...] = ()
        if len(explicit_types) > 1 or any(
            explicit_type not in {rule_id, category.value} for explicit_type in explicit_types
        ):
            conflicting = tuple(
                failure.failure_id for failure in grouped if failure.suspected_root_cause_type
            )
            base_confidence = min(base_confidence, 0.55)

        target_component = (
            _common_value(tuple(failure.affected_component for failure in grouped))
            or cluster.affected_component
        )
        target_skill = (
            _common_value(tuple(failure.affected_skill for failure in grouped))
            or cluster.affected_skill
        )
        target_tool = (
            _common_value(tuple(failure.affected_tool for failure in grouped))
            or cluster.affected_tool
        )
        target_policy = (
            _common_value(tuple(failure.affected_policy for failure in grouped))
            or cluster.affected_policy
        )
        target_workflow = (
            _common_value(tuple(failure.affected_workflow for failure in grouped))
            or cluster.affected_workflow
        )

        explicit_target_count = sum(
            value is not None
            for value in (
                target_component,
                target_skill,
                target_tool,
                target_policy,
                target_workflow,
            )
        )
        confidence = min(1.0, base_confidence + (0.05 if explicit_target_count else 0.0))
        status = (
            RootCauseReviewerStatus.NEEDS_REVIEW
            if confidence < 0.65 or conflicting
            else RootCauseReviewerStatus.UNREVIEWED
        )
        hypothesis_id = f"hypothesis:{cluster.cluster_id}:{rule_id}"
        supporting_evidence = tuple(
            failure_id for failure_id in evidence if failure_id not in conflicting
        )
        if not supporting_evidence:
            supporting_evidence = evidence[:1]
        return (
            RootCauseHypothesis(
                hypothesis_id=hypothesis_id,
                source_cluster_id=cluster.cluster_id,
                affected_agent_id=agent_id,
                affected_component=target_component,
                affected_skill=target_skill,
                affected_tool=target_tool,
                affected_policy=target_policy,
                affected_workflow=target_workflow,
                suspected_cause=cause,
                supporting_evidence=supporting_evidence,
                conflicting_evidence=conflicting,
                confidence=confidence,
                suggested_intervention_classes=interventions,
                reviewer_status=status,
                created_at=created_at or cluster.created_at,
                metadata={
                    "rule_id": rule_id,
                    "failure_category": category.value,
                },
            ),
        )


def analyze_root_cause(
    cluster: FailureCluster,
    failures: Sequence[EvaluationFailure],
    *,
    agent_id: str | None = None,
    created_at: datetime | None = None,
) -> tuple[RootCauseHypothesis, ...]:
    """Functional entry point for deterministic root-cause analysis."""

    return RootCauseAnalyzer().analyze(
        cluster,
        failures,
        agent_id=agent_id,
        created_at=created_at,
    )


class ImprovementPlanner:
    """Select a bounded intervention before any candidate is constructed."""

    def plan(
        self,
        cluster: FailureCluster,
        root_cause_hypotheses: Sequence[RootCauseHypothesis],
        current_candidate: EnterpriseAgentCandidate,
        registry_snapshot: EnvironmentSnapshot | None,
        scope: ImprovementScope,
        prior_experiments: Sequence[PriorExperimentEvidence] = (),
        *,
        approved_tools: Sequence[str] = (),
        approved_skills: Sequence[str] = (),
        created_at: datetime | None = None,
    ) -> ImprovementPlan:
        """Return a scope-checked and evidence-linked improvement plan."""

        hypotheses = tuple(
            hypothesis
            for hypothesis in root_cause_hypotheses
            if hypothesis.source_cluster_id == cluster.cluster_id
            and hypothesis.reviewer_status != RootCauseReviewerStatus.REJECTED
        )
        if not hypotheses:
            return self._human_review_plan(
                cluster,
                current_candidate,
                scope,
                registry_snapshot,
                prior_experiments,
                reason="No accepted root-cause hypothesis is available.",
                created_at=created_at,
            )

        hypothesis = max(
            hypotheses,
            key=lambda item: (item.effective_confidence, item.confidence, item.hypothesis_id),
        )
        if (
            hypothesis.effective_confidence < 0.65
            or hypothesis.reviewer_status == RootCauseReviewerStatus.NEEDS_REVIEW
        ):
            return self._human_review_plan(
                cluster,
                current_candidate,
                scope,
                registry_snapshot,
                prior_experiments,
                reason="The causal evidence is ambiguous or needs human review.",
                hypothesis=hypothesis,
                created_at=created_at,
            )

        decision = self._select_decision(
            hypothesis,
            scope,
            current_candidate,
            approved_tools=approved_tools,
            approved_skills=approved_skills,
        )
        if decision is None:
            return self._human_review_plan(
                cluster,
                current_candidate,
                scope,
                registry_snapshot,
                prior_experiments,
                reason="The suggested intervention is outside the improvement scope.",
                hypothesis=hypothesis,
                created_at=created_at,
            )

        change_kind = _change_kind(decision)
        if change_kind is not None and _prior_decision_failed(prior_experiments, decision):
            return self._human_review_plan(
                cluster,
                current_candidate,
                scope,
                registry_snapshot,
                prior_experiments,
                reason="Prior experiment evidence does not support repeating this intervention.",
                hypothesis=hypothesis,
                created_at=created_at,
            )

        risk = _risk_for_cluster(cluster)
        required_approvals = _required_approvals(risk, decision)
        builder_type = _builder_type(decision)
        metadata = {
            "hypothesis_confidence": f"{hypothesis.effective_confidence:.3f}",
            "rule_id": hypothesis.metadata.get("rule_id", "deterministic"),
        }
        if decision == ImprovementDecision.TOOL_ADDITION and hypothesis.affected_tool:
            metadata["approved_tool"] = str(hypothesis.affected_tool in set(approved_tools))
        if decision == ImprovementDecision.SKILL_ADDITION and hypothesis.affected_skill:
            metadata["approved_skill"] = str(hypothesis.affected_skill in set(approved_skills))

        return ImprovementPlan(
            plan_id=_plan_id(
                cluster,
                current_candidate,
                scope,
                decision,
                (hypothesis,),
                registry_snapshot,
            ),
            source_cluster_id=cluster.cluster_id,
            root_cause_hypothesis_ids=(hypothesis.hypothesis_id,),
            decision=decision,
            rationale=(
                f"Apply the bounded {decision.value} intervention to address the recorded "
                "failure cluster."
            ),
            expected_affected_metrics=_expected_metrics(cluster.category),
            risk=risk,
            required_approvals=required_approvals,
            candidate_builder_type=builder_type,
            source_failure_ids=cluster.failure_ids,
            evidence_refs=_hypothesis_evidence(hypothesis, cluster),
            scope_id=scope.scope_id,
            current_candidate_id=current_candidate.candidate_id,
            registry_snapshot_id=registry_snapshot.identity if registry_snapshot else None,
            prior_experiment_ids=tuple(_prior_experiment_id(item) for item in prior_experiments),
            requires_human_review=False,
            created_at=created_at or utc_now(),
            metadata=metadata,
        )

    def _select_decision(
        self,
        hypothesis: RootCauseHypothesis,
        scope: ImprovementScope,
        candidate: EnterpriseAgentCandidate,
        *,
        approved_tools: Sequence[str],
        approved_skills: Sequence[str],
    ) -> ImprovementDecision | None:
        for kind in hypothesis.suggested_intervention_classes:
            decision = ImprovementDecision.from_change_kind(kind)
            if not _scope_allows(
                scope,
                kind,
                hypothesis,
                agent_id=candidate.agent_id,
            ):
                continue
            if kind == ChangeKind.TOOL_ADDITION and hypothesis.affected_tool in {
                reference.component_id for reference in candidate.tool_refs
            }:
                continue
            if kind == ChangeKind.SKILL_ADDITION and hypothesis.affected_skill in set(
                reference.component_id for reference in candidate.skill_refs
            ):
                continue
            if kind == ChangeKind.TOOL_ADDITION and hypothesis.affected_tool:
                if approved_tools and hypothesis.affected_tool not in approved_tools:
                    continue
            if kind == ChangeKind.SKILL_ADDITION and hypothesis.affected_skill:
                if approved_skills and hypothesis.affected_skill not in approved_skills:
                    continue
            return decision
        return None

    @staticmethod
    def _human_review_plan(
        cluster: FailureCluster,
        candidate: EnterpriseAgentCandidate,
        scope: ImprovementScope,
        registry_snapshot: EnvironmentSnapshot | None,
        prior_experiments: Sequence[PriorExperimentEvidence],
        *,
        reason: str,
        hypothesis: RootCauseHypothesis | None = None,
        created_at: datetime | None,
    ) -> ImprovementPlan:
        hypothesis_ids = (
            (hypothesis.hypothesis_id,) if hypothesis else (f"unresolved:{cluster.cluster_id}",)
        )
        evidence = _hypothesis_evidence(hypothesis, cluster) if hypothesis else cluster.failure_ids
        return ImprovementPlan(
            plan_id=_plan_id(
                cluster,
                candidate,
                scope,
                ImprovementDecision.HUMAN_REVIEW_REQUIRED,
                (hypothesis,) if hypothesis else (),
                registry_snapshot,
            ),
            source_cluster_id=cluster.cluster_id,
            root_cause_hypothesis_ids=hypothesis_ids,
            decision=ImprovementDecision.HUMAN_REVIEW_REQUIRED,
            rationale=reason,
            expected_affected_metrics=_expected_metrics(cluster.category),
            risk=_risk_for_cluster(cluster),
            required_approvals=("improvement_reviewer",),
            candidate_builder_type="human_review",
            source_failure_ids=cluster.failure_ids,
            evidence_refs=evidence,
            scope_id=scope.scope_id,
            current_candidate_id=candidate.candidate_id,
            registry_snapshot_id=registry_snapshot.identity if registry_snapshot else None,
            prior_experiment_ids=tuple(_prior_experiment_id(item) for item in prior_experiments),
            requires_human_review=True,
            created_at=created_at or utc_now(),
            metadata={"decision_reason": reason},
        )


def build_improvement_plan(
    cluster: FailureCluster,
    root_cause_hypotheses: Sequence[RootCauseHypothesis],
    current_candidate: EnterpriseAgentCandidate,
    registry_snapshot: EnvironmentSnapshot | None,
    scope: ImprovementScope,
    prior_experiments: Sequence[PriorExperimentEvidence] = (),
    **kwargs: object,
) -> ImprovementPlan:
    """Functional entry point for :class:`ImprovementPlanner`."""

    return ImprovementPlanner().plan(
        cluster,
        root_cause_hypotheses,
        current_candidate,
        registry_snapshot,
        scope,
        prior_experiments,
        **kwargs,  # type: ignore[arg-type]
    )


def _rule_for(
    category: FailureCategory,
    failures: Sequence[EvaluationFailure],
) -> tuple[str, str, tuple[ChangeKind, ...], float]:
    evaluator_ids = " ".join(failure.evaluator_id.casefold() for failure in failures)
    if category in {
        FailureCategory.AUTHORIZATION,
        FailureCategory.POLICY,
        FailureCategory.COMPLIANCE,
    }:
        return (
            "authorization-boundary",
            "The recorded action crossed or lacked an explicit authorization or policy boundary.",
            (ChangeKind.PERMISSION_CHANGE, ChangeKind.POLICY_CHANGE),
            0.88,
        )
    if category in {FailureCategory.PRIVACY, FailureCategory.INTEGRATION}:
        return (
            "boundary-integration",
            "The execution crossed a privacy or external integration boundary that needs "
            "an explicit control.",
            (ChangeKind.POLICY_CHANGE, ChangeKind.PERMISSION_CHANGE),
            0.82,
        )
    if category in {FailureCategory.APPROVAL}:
        return (
            "approval-gate",
            "The required approval boundary was absent, incorrect, or not enforced before "
            "the action.",
            (ChangeKind.APPROVAL_RULE_CHANGE, ChangeKind.POLICY_CHANGE),
            0.88,
        )
    if category in {FailureCategory.STATE, FailureCategory.DATA_INTEGRITY}:
        return (
            "state-transition",
            "The execution used an unsupported state mutation or workflow transition.",
            (ChangeKind.WORKFLOW_CHANGE, ChangeKind.THRESHOLD_CHANGE),
            0.82,
        )
    if category == FailureCategory.TOOL_SIDE_EFFECT:
        return (
            "tool-side-effect",
            "The tool side effect did not match the declared operational boundary or "
            "compensation rule.",
            (ChangeKind.TOOL_CONFIGURATION_CHANGE, ChangeKind.POLICY_CHANGE),
            0.80,
        )
    if category in {
        FailureCategory.TOOL_SELECTION,
        FailureCategory.TOOL_EXECUTION,
        FailureCategory.INTEGRATION,
    }:
        if any(failure.affected_skill for failure in failures):
            return (
                "missing-skill",
                "The required skill was not available or was not selected for the observed task.",
                (ChangeKind.SKILL_ADDITION, ChangeKind.TOOL_ADDITION),
                0.82,
            )
        return (
            "tool-availability",
            "The required tool was unavailable, incorrectly selected, or failed at its "
            "execution boundary.",
            (ChangeKind.TOOL_ADDITION, ChangeKind.TOOL_CONFIGURATION_CHANGE),
            0.78,
        )
    if category == FailureCategory.GROUNDING or "retrieval" in evaluator_ids:
        return (
            "retrieval-source",
            "The required retrieval source or retrieval configuration did not provide "
            "usable evidence.",
            (ChangeKind.RETRIEVAL_CHANGE, ChangeKind.POLICY_CHANGE),
            0.80,
        )
    if category in {FailureCategory.RELIABILITY, FailureCategory.EFFICIENCY} or any(
        marker in evaluator_ids for marker in ("timeout", "latency", "retry", "cost")
    ):
        return (
            "runtime-reliability",
            "The observed execution shows a repeatable runtime, routing, or threshold "
            "reliability pattern.",
            (ChangeKind.ROUTING_CHANGE, ChangeKind.MODEL_CHANGE, ChangeKind.THRESHOLD_CHANGE),
            0.72,
        )
    if category == FailureCategory.DELEGATION:
        return (
            "delegation-routing",
            "The delegation path or delegated skill did not satisfy the recorded "
            "interaction boundary.",
            (ChangeKind.ROUTING_CHANGE, ChangeKind.SKILL_ADDITION),
            0.75,
        )
    return (
        "agent-quality",
        "The recorded output or action quality does not meet the declared evaluation expectation.",
        (ChangeKind.PROMPT_CHANGE, ChangeKind.MODEL_CHANGE),
        0.67,
    )


def _common_value(values: Sequence[str | None]) -> str | None:
    non_empty = tuple(value for value in values if value)
    if not non_empty:
        return None
    value, count = Counter(non_empty).most_common(1)[0]
    return value if count >= len(non_empty) / 2 else None


def _scope_allows(
    scope: ImprovementScope,
    kind: ChangeKind,
    hypothesis: RootCauseHypothesis,
    *,
    agent_id: str,
) -> bool:
    if kind not in scope.allowed_change_kinds:
        return False
    if agent_id in scope.protected_agents:
        return False
    if scope.allowed_agents and agent_id not in scope.allowed_agents:
        return False
    if hypothesis.affected_agent_id and hypothesis.affected_agent_id != agent_id:
        return False
    if hypothesis.affected_skill and hypothesis.affected_skill in scope.protected_skills:
        return False
    if hypothesis.affected_tool and hypothesis.affected_tool in scope.protected_tools:
        return False
    if hypothesis.affected_policy and hypothesis.affected_policy in scope.protected_policies:
        return False
    if (
        scope.allowed_skills
        and hypothesis.affected_skill
        and hypothesis.affected_skill not in scope.allowed_skills
    ):
        return False
    if (
        scope.allowed_tools
        and hypothesis.affected_tool
        and hypothesis.affected_tool not in scope.allowed_tools
    ):
        return False
    if (
        scope.allowed_policies
        and hypothesis.affected_policy
        and hypothesis.affected_policy not in scope.allowed_policies
    ):
        return False
    return True


def _change_kind(decision: ImprovementDecision) -> ChangeKind | None:
    try:
        return ChangeKind(decision.value)
    except ValueError:
        return None


def _builder_type(decision: ImprovementDecision) -> str:
    return {
        ImprovementDecision.PROMPT_CHANGE: "PromptCandidateBuilder",
        ImprovementDecision.TOOL_ADDITION: "ToolBindingCandidateBuilder",
        ImprovementDecision.TOOL_REMOVAL: "ToolBindingCandidateBuilder",
        ImprovementDecision.TOOL_CONFIGURATION_CHANGE: "ToolBindingCandidateBuilder",
        ImprovementDecision.POLICY_CHANGE: "PolicyCandidateBuilder",
        ImprovementDecision.PERMISSION_CHANGE: "PolicyCandidateBuilder",
        ImprovementDecision.ROUTING_CHANGE: "RoutingCandidateBuilder",
        ImprovementDecision.MODEL_CHANGE: "ModelCandidateBuilder",
        ImprovementDecision.RETRIEVAL_CHANGE: "PolicyCandidateBuilder",
        ImprovementDecision.MEMORY_CHANGE: "PolicyCandidateBuilder",
        ImprovementDecision.THRESHOLD_CHANGE: "ThresholdCandidateBuilder",
        ImprovementDecision.WORKFLOW_CHANGE: "WorkflowCandidateBuilder",
        ImprovementDecision.SKILL_ADDITION: "SkillCandidateBuilder",
        ImprovementDecision.SKILL_REMOVAL: "SkillCandidateBuilder",
        ImprovementDecision.APPROVAL_RULE_CHANGE: "ApprovalRuleCandidateBuilder",
    }.get(decision, "none")


def _risk_for_cluster(cluster: FailureCluster) -> ArtifactRiskClassification:
    if cluster.category in {
        FailureCategory.AUTHORIZATION,
        FailureCategory.POLICY,
        FailureCategory.PRIVACY,
        FailureCategory.COMPLIANCE,
    }:
        return ArtifactRiskClassification.CRITICAL
    first = cluster.metadata.get("risk", "")
    if first in {"critical", "high"}:
        return ArtifactRiskClassification(first)
    if cluster.category in {
        FailureCategory.APPROVAL,
        FailureCategory.STATE,
        FailureCategory.DATA_INTEGRITY,
    }:
        return ArtifactRiskClassification.HIGH
    return ArtifactRiskClassification.MEDIUM


def _required_approvals(
    risk: ArtifactRiskClassification,
    decision: ImprovementDecision,
) -> tuple[str, ...]:
    if risk == ArtifactRiskClassification.CRITICAL:
        return ("security_reviewer", "agent_owner")
    if risk == ArtifactRiskClassification.HIGH or decision in {
        ImprovementDecision.PERMISSION_CHANGE,
        ImprovementDecision.APPROVAL_RULE_CHANGE,
    }:
        return ("agent_owner",)
    return ()


def _expected_metrics(category: FailureCategory) -> tuple[str, ...]:
    values = {
        FailureCategory.AUTHORIZATION: ("authorization", "security"),
        FailureCategory.POLICY: ("policy", "security"),
        FailureCategory.APPROVAL: ("approvals", "security"),
        FailureCategory.STATE: ("state_integrity", "workflow_completion"),
        FailureCategory.DATA_INTEGRITY: ("state_integrity",),
        FailureCategory.DELEGATION: ("delegation", "reliability"),
        FailureCategory.GROUNDING: ("retrieval", "business_outcomes"),
        FailureCategory.RELIABILITY: ("reliability", "latency"),
        FailureCategory.EFFICIENCY: ("cost", "latency"),
        FailureCategory.TOOL_SIDE_EFFECT: ("tool_side_effects", "state_integrity"),
    }
    return values.get(category, ("quality", "business_outcomes"))


def _hypothesis_evidence(
    hypothesis: RootCauseHypothesis,
    cluster: FailureCluster,
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*hypothesis.supporting_evidence, *cluster.failure_ids)))


def _prior_decision_failed(
    prior_experiments: Sequence[object],
    decision: ImprovementDecision,
) -> bool:
    marker = decision.value
    for item in prior_experiments:
        verdict = str(getattr(item, "verdict", "")).casefold()
        summary = str(getattr(item, "summary", getattr(item, "notes", ""))).casefold()
        if verdict in {"regressed", "rejected", "failed"} and marker in summary:
            return True
    return False


def _prior_experiment_id(item: object) -> str:
    """Return a stable ID for typed or legacy prior experiment evidence."""

    experiment_id = getattr(item, "experiment_id", None)
    if experiment_id:
        return str(experiment_id)
    comparison_id = getattr(item, "comparison_id", None)
    if comparison_id:
        return str(comparison_id)
    return "prior:" + sha256(str(item).encode("utf-8")).hexdigest()[:16]


def _plan_id(
    cluster: FailureCluster,
    candidate: EnterpriseAgentCandidate,
    scope: ImprovementScope,
    decision: ImprovementDecision,
    hypotheses: Sequence[RootCauseHypothesis],
    snapshot: EnvironmentSnapshot | None,
) -> str:
    payload = {
        "cluster": cluster.cluster_id,
        "failures": cluster.failure_ids,
        "candidate": candidate.candidate_id,
        "scope": scope.scope_id,
        "decision": decision.value,
        "hypotheses": tuple(hypothesis.hypothesis_id for hypothesis in hypotheses),
        "snapshot": snapshot.identity if snapshot else None,
    }
    digest = sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()[:20]
    return f"plan:{digest}"


__all__ = [
    "ImprovementPlanner",
    "ImprovementPlanningError",
    "RootCauseAnalyzer",
    "analyze_root_cause",
    "build_improvement_plan",
]
