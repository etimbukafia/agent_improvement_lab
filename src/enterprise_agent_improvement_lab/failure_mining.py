"""Normalize evaluator failures and build deterministic failure clusters."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from typing import Protocol, Sequence

from enterprise_agent_improvement_lab.contracts.cases import (
    DatasetVersion,
    EnterpriseEvaluationCase,
    RiskLevel,
)
from enterprise_agent_improvement_lab.contracts.common import require_aware_utc
from enterprise_agent_improvement_lab.contracts.evaluation import EnterpriseEvaluationReport
from enterprise_agent_improvement_lab.contracts.failures import (
    EvaluationFailure,
    FailureCategory,
    FailureCluster,
    Severity,
)
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTrace
from enterprise_agent_improvement_lab.serialization import stable_json_dumps


class ClusterTitleGenerator(Protocol):
    """Optional title-only generator for a failure cluster."""

    def __call__(self, failures: Sequence[EvaluationFailure]) -> str:
        """Return a human-readable title for the supplied failures."""


def normalize_failures(
    report: EnterpriseEvaluationReport,
    dataset: DatasetVersion,
    *,
    traces: Sequence[ExecutionTrace] = (),
    runtime_component: str | None = None,
    created_at: datetime | None = None,
) -> tuple[EvaluationFailure, ...]:
    """Convert failed report scores into stable failure records.

    The function uses only safe case metadata and evaluator explanations.
    It never copies raw prompts or raw tool results into a failure record.
    """

    case_by_id = {case.case_id: case for case in dataset.cases}
    score_by_id = {score.score_id: score for score in report.scores}
    trace_by_id = {trace.execution_id: trace for trace in (*report.traces, *traces)}
    failures: list[EvaluationFailure] = []

    for case_result in sorted(
        report.case_results, key=lambda item: (item.case_id, item.repeat_index)
    ):
        case = case_by_id.get(case_result.case_id)
        if case is None:
            raise ValueError(f"Report references unknown case {case_result.case_id!r}")
        trace = trace_by_id.get(case_result.trace_id or "")
        for score_id in case_result.score_ids:
            score = score_by_id.get(score_id)
            if score is None:
                raise ValueError(f"Report references unknown score {score_id!r}")
            if score.passed:
                continue
            category = score.failure_category or infer_failure_category(score.evaluator_id)
            component = _runtime_component(case, trace, runtime_component)
            intent = _intent(case)
            failures.append(
                EvaluationFailure(
                    failure_id=f"failure:{score.score_id}",
                    evaluator_id=score.evaluator_id,
                    category=category,
                    severity=_severity(case.risk, category),
                    case_id=case.case_id,
                    trace_id=case_result.trace_id,
                    score_id=score.score_id,
                    summary=f"{score.evaluator_id} failed for case {case.case_id}.",
                    expected_behavior=_expected_behavior(case),
                    observed_behavior=score.explanation,
                    evidence_refs=score.evidence_refs,
                    created_at=require_aware_utc(
                        created_at or score.created_at or report.created_at
                    ),
                    score=score.score,
                    confidence=score.confidence,
                    runtime_component=component,
                    tags=case.tags,
                    intent=intent,
                    metadata={
                        "dataset_id": dataset.dataset_id,
                        "dataset_version": dataset.version,
                        "run_id": report.run_id,
                        "repeat_index": str(case_result.repeat_index),
                        "risk": case.risk.value,
                        "workflow": str(case.metadata.get("workflow", "unspecified")),
                    },
                )
            )

    return tuple(sorted(failures, key=lambda failure: failure.failure_id))


def infer_failure_category(evaluator_id: str) -> FailureCategory:
    """Map an evaluator ID to the generic failure taxonomy."""

    normalized = evaluator_id.casefold()
    if "planning" in normalized:
        return FailureCategory.PLANNING
    if "tool.selection" in normalized:
        return FailureCategory.TOOL_SELECTION
    if "argument" in normalized:
        return FailureCategory.ARGUMENTS
    if "trajectory" in normalized:
        return FailureCategory.TRAJECTORY
    if "ground" in normalized:
        return FailureCategory.GROUNDING
    if normalized == "state.transaction_integrity":
        return FailureCategory.DATA_INTEGRITY
    if "data_integrity" in normalized or "transaction" in normalized:
        return FailureCategory.DATA_INTEGRITY
    if "privacy" in normalized:
        return FailureCategory.PRIVACY
    if "compliance" in normalized or "regulated" in normalized:
        return FailureCategory.COMPLIANCE
    if "integration" in normalized or "external_service" in normalized:
        return FailureCategory.INTEGRATION
    if "policy" in normalized:
        return FailureCategory.POLICY
    if "authorization" in normalized:
        return FailureCategory.AUTHORIZATION
    if "approval" in normalized:
        return FailureCategory.APPROVAL
    if "delegation" in normalized:
        return FailureCategory.DELEGATION
    if normalized.startswith("business."):
        return FailureCategory.BUSINESS_OUTCOME
    if normalized == "tool.side_effect_correctness" or "compensation" in normalized:
        return FailureCategory.TOOL_SIDE_EFFECT
    if any(token in normalized for token in ("idempotency", "retry", "timeout")):
        return FailureCategory.RELIABILITY
    if "state" in normalized or "workflow" in normalized:
        return FailureCategory.STATE
    if "tool." in normalized:
        return FailureCategory.TOOL_EXECUTION
    if "context" in normalized or "repeated_question" in normalized:
        return FailureCategory.CONTEXT
    if "safety" in normalized or "verification" in normalized:
        return FailureCategory.SAFETY
    if "latency" in normalized or "token" in normalized or "cost" in normalized:
        return FailureCategory.EFFICIENCY
    if "error_rate" in normalized or "redundant" in normalized or "loop" in normalized:
        return FailureCategory.EFFICIENCY
    return FailureCategory.QUALITY


def cluster_failures(
    failures: Sequence[EvaluationFailure],
    *,
    title_generator: ClusterTitleGenerator | None = None,
    created_at: datetime | None = None,
) -> tuple[FailureCluster, ...]:
    """Group failures by evaluator, component, tags, and intent.

    A title generator can suggest text only. It cannot change scores,
    categories, or failure membership.
    """

    groups: defaultdict[
        tuple[str, str, str, tuple[str, ...], str, str, str, str, str, str, str],
        list[EvaluationFailure],
    ] = defaultdict(list)
    for failure in failures:
        key = (
            failure.category.value,
            failure.evaluator_id,
            failure.runtime_component,
            tuple(sorted(failure.tags)),
            failure.intent or "",
            failure.affected_component or "",
            failure.affected_skill or "",
            failure.affected_tool or "",
            failure.affected_policy or "",
            failure.affected_workflow or "",
            failure.affected_business_outcome or "",
        )
        groups[key].append(failure)

    clusters: list[FailureCluster] = []
    for key, grouped in sorted(groups.items(), key=lambda item: item[0]):
        ordered = tuple(sorted(grouped, key=lambda failure: failure.failure_id))
        cluster_key = stable_json_dumps(
            {
                "category": key[0],
                "evaluator_id": key[1],
                "runtime_component": key[2],
                "tags": key[3],
                "intent": key[4],
                "affected_component": key[5],
                "affected_skill": key[6],
                "affected_tool": key[7],
                "affected_policy": key[8],
                "affected_workflow": key[9],
                "affected_business_outcome": key[10],
            }
        )
        cluster_id = f"cluster:{sha256(cluster_key.encode('utf-8')).hexdigest()[:20]}"
        title = _default_cluster_title(ordered[0])
        title_source = "deterministic"
        if title_generator is not None:
            try:
                title_inputs = tuple(failure.model_copy(deep=True) for failure in ordered)
                suggested = title_generator(title_inputs).strip()
            except Exception:
                suggested = ""
            if suggested:
                title = suggested
                title_source = "assisted"

        first = ordered[0]
        clusters.append(
            FailureCluster(
                cluster_id=cluster_id,
                cluster_key=cluster_key,
                failure_ids=tuple(failure.failure_id for failure in ordered),
                category=first.category,
                title=title,
                created_at=require_aware_utc(
                    created_at or max(failure.created_at for failure in ordered)
                ),
                evaluator_id=first.evaluator_id,
                runtime_component=first.runtime_component,
                affected_component=first.affected_component,
                affected_skill=first.affected_skill,
                affected_tool=first.affected_tool,
                affected_policy=first.affected_policy,
                affected_workflow=first.affected_workflow,
                affected_business_outcome=first.affected_business_outcome,
                tags=tuple(sorted(first.tags)),
                intent=first.intent,
                title_source=title_source,
                metadata={"failure_count": str(len(ordered))},
            )
        )

    return tuple(sorted(clusters, key=lambda cluster: cluster.cluster_id))


def _expected_behavior(case: EnterpriseEvaluationCase) -> str:
    declared = case.metadata.get("expected_behavior")
    if isinstance(declared, str) and declared.strip():
        return declared
    if case.expected_outputs or case.expected_actions or case.required_actions:
        return stable_json_dumps(
            {
                "outputs": case.expected_outputs,
                "expected_actions": case.expected_actions,
                "required_actions": case.required_actions,
                "prohibited_actions": case.prohibited_actions,
                "final_state": case.expected_final_state,
                "invariants": case.state_invariants,
            }
        )
    return "The case should satisfy its declared evaluation criteria."


def _intent(case: EnterpriseEvaluationCase) -> str | None:
    declared = case.metadata.get("intent")
    if isinstance(declared, str) and declared.strip():
        return declared
    if isinstance(case.input, dict):
        input_intent = case.input.get("intent")
        if isinstance(input_intent, str) and input_intent.strip():
            return input_intent
    return None


def _runtime_component(
    case: EnterpriseEvaluationCase,
    trace: ExecutionTrace | None,
    fallback: str | None,
) -> str:
    if trace is not None:
        for key in ("runtime_component", "component", "node", "node_name"):
            value = trace.metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
    value = case.metadata.get("runtime_component")
    if isinstance(value, str) and value.strip():
        return value
    return fallback.strip() if isinstance(fallback, str) and fallback.strip() else "unknown"


def _severity(risk: RiskLevel, category: FailureCategory) -> Severity:
    if category in {FailureCategory.SAFETY, FailureCategory.AUTHORIZATION, FailureCategory.PRIVACY}:
        return Severity.CRITICAL if risk == RiskLevel.CRITICAL else Severity.HIGH
    if risk == RiskLevel.CRITICAL:
        return Severity.CRITICAL
    if risk == RiskLevel.HIGH:
        return Severity.HIGH
    if risk == RiskLevel.LOW:
        return Severity.LOW
    return Severity.MEDIUM


def _default_cluster_title(failure: EvaluationFailure) -> str:
    component = failure.runtime_component
    intent = f" for {failure.intent}" if failure.intent else ""
    return f"{failure.evaluator_id} ({failure.category.value}) in {component}{intent}"
