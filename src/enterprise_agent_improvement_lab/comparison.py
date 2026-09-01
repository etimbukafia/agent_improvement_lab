"""Deterministic comparison of enterprise evaluation evidence."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from hashlib import sha256

from enterprise_agent_improvement_lab.contracts.common import utc_now
from enterprise_agent_improvement_lab.contracts.evaluation import EnterpriseEvaluationReport
from enterprise_agent_improvement_lab.contracts.experiments import (
    BaselineComparison,
    ComparisonDimensionWeight,
    ComparisonMetric,
    ComparisonVerdict,
    EnterpriseComparisonDimension,
    EnterpriseComparisonMetric,
    EnterpriseComparisonPolicy,
    EvaluatorFamilyAggregate,
)
from enterprise_agent_improvement_lab.contracts.failures import EvaluationScore, FailureCategory


class ComparisonError(ValueError):
    """Raised when a comparison cannot be reproduced safely."""


class EnterpriseComparisonRunner:
    """Compare typed enterprise reports or already-paired enterprise metrics."""

    def __init__(self, *, policy: EnterpriseComparisonPolicy | None = None) -> None:
        self.policy = policy or EnterpriseComparisonPolicy(policy_id="enterprise-default")

    def compare(
        self,
        baseline: EnterpriseEvaluationReport
        | Sequence[EnterpriseComparisonMetric | ComparisonMetric],
        candidate: EnterpriseEvaluationReport
        | Sequence[EnterpriseComparisonMetric | ComparisonMetric]
        | None = None,
        *,
        baseline_run_id: str = "baseline",
        candidate_run_id: str = "candidate",
        baseline_snapshot: object | None = None,
        candidate_snapshot: object | None = None,
        target_metric_ids: Sequence[str] = (),
        target_failure_ids: Sequence[str] = (),
        created_at: datetime | None = None,
    ) -> BaselineComparison:
        """Compare two enterprise reports or two paired metric collections."""

        if isinstance(baseline, EnterpriseEvaluationReport):
            if not isinstance(candidate, EnterpriseEvaluationReport):
                raise ComparisonError("An enterprise report comparison needs two reports")
            return compare_enterprise_reports(
                baseline,
                candidate,
                policy=self.policy,
                baseline_snapshot=baseline_snapshot,
                candidate_snapshot=candidate_snapshot,
                target_metric_ids=target_metric_ids,
                target_failure_ids=target_failure_ids,
                created_at=created_at,
            )
        if candidate is None or isinstance(candidate, EnterpriseEvaluationReport):
            raise ComparisonError("An enterprise metric comparison needs two metric inputs")
        return compare_enterprise_metrics(
            baseline,
            candidate,
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            policy=self.policy,
            baseline_snapshot=baseline_snapshot,
            candidate_snapshot=candidate_snapshot,
            target_metric_ids=target_metric_ids,
            targeted_failure_ids=target_failure_ids,
            created_at=created_at,
        )


def compare_enterprise_metrics(
    baseline_metrics: Sequence[EnterpriseComparisonMetric | ComparisonMetric],
    candidate_metrics: Sequence[EnterpriseComparisonMetric | ComparisonMetric] | None = None,
    *,
    baseline_run_id: str = "baseline",
    candidate_run_id: str = "candidate",
    policy: EnterpriseComparisonPolicy | None = None,
    baseline_snapshot: object | None = None,
    candidate_snapshot: object | None = None,
    target_metric_ids: Sequence[str] = (),
    pass_to_fail_transitions: Sequence[str] = (),
    targeted_failure_ids: Sequence[str] = (),
    target_cluster_id: str | None = None,
    created_at: datetime | None = None,
) -> BaselineComparison:
    """Compare enterprise dimensions with deterministic risk-aware gates."""

    if baseline_run_id == candidate_run_id:
        raise ComparisonError("Baseline and candidate run IDs must differ")
    resolved_policy = policy or EnterpriseComparisonPolicy(policy_id="enterprise-default")
    metrics = tuple(
        sorted(_pair_metrics(baseline_metrics, candidate_metrics), key=lambda x: x.metric_id)
    )
    incompatible = _snapshots_incompatible(
        baseline_snapshot,
        candidate_snapshot,
        require=resolved_policy.require_environment_compatibility,
    )
    metrics = _apply_policy(metrics, resolved_policy)
    regressions = tuple(
        metric.metric_id
        for metric in metrics
        if _metric_regressed(metric, resolved_policy.metric_tolerance)
    )
    hard_regressions = _unique(
        (
            *(
                metric.metric_id
                for metric in metrics
                if metric.metric_id in regressions and metric.hard
            ),
            *pass_to_fail_transitions,
            "environment_incompatible" if incompatible else "",
        )
    )
    enterprise_regressions = _unique(regressions)
    all_regressions = _unique(
        (
            *enterprise_regressions,
            *pass_to_fail_transitions,
            "environment_incompatible" if incompatible else "",
        )
    )
    target_ids = set(target_metric_ids)
    target_metrics = tuple(metric for metric in metrics if metric.metric_id in target_ids)
    target_improved = _target_improved(target_metrics or metrics, resolved_policy.metric_tolerance)
    if incompatible or hard_regressions:
        verdict = ComparisonVerdict.REJECTED
    elif all_regressions:
        verdict = ComparisonVerdict.REGRESSED
    elif not metrics:
        verdict = ComparisonVerdict.INCONCLUSIVE
    else:
        verdict = ComparisonVerdict.IMPROVED

    dimensions = {
        name: _dimension_ids(metrics, regressions, name) for name in EnterpriseComparisonDimension
    }
    numerical = tuple(
        metric.metric_id
        for metric in metrics
        if metric.metric_id in regressions
        and (
            metric.dimension
            in {
                EnterpriseComparisonDimension.COST,
                EnterpriseComparisonDimension.LATENCY,
                EnterpriseComparisonDimension.TOKEN_USAGE,
                EnterpriseComparisonDimension.RELIABILITY,
            }
            or metric.metric_name.casefold()
            in {"cost", "latency", "duration", "tokens", "token_usage", "reliability"}
        )
    )
    legacy_metrics = tuple(_legacy_metric(metric) for metric in metrics)
    timestamp = created_at or utc_now()
    return BaselineComparison(
        comparison_id=_comparison_id(baseline_run_id, candidate_run_id),
        experiment_id=f"experiment:{candidate_run_id}",
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        metrics=legacy_metrics,
        regressions=all_regressions,
        targeted_failure_ids=tuple(targeted_failure_ids),
        target_cluster_id=target_cluster_id,
        target_improved=target_improved,
        pass_to_fail_transitions=tuple(pass_to_fail_transitions),
        numerical_regressions=numerical,
        hard_regressions=hard_regressions,
        enterprise_metrics=metrics,
        enterprise_regressions=enterprise_regressions,
        business_regressions=dimensions[EnterpriseComparisonDimension.BUSINESS_OUTCOMES],
        security_regressions=dimensions[EnterpriseComparisonDimension.SECURITY],
        authorization_regressions=dimensions[EnterpriseComparisonDimension.AUTHORIZATION],
        approval_regressions=dimensions[EnterpriseComparisonDimension.APPROVALS],
        state_integrity_regressions=dimensions[EnterpriseComparisonDimension.STATE_INTEGRITY],
        workflow_completion_regressions=dimensions[
            EnterpriseComparisonDimension.WORKFLOW_COMPLETION
        ],
        cost_regressions=dimensions[EnterpriseComparisonDimension.COST],
        latency_regressions=dimensions[EnterpriseComparisonDimension.LATENCY],
        token_usage_regressions=dimensions[EnterpriseComparisonDimension.TOKEN_USAGE],
        tool_side_effect_regressions=dimensions[EnterpriseComparisonDimension.TOOL_SIDE_EFFECTS],
        delegation_regressions=dimensions[EnterpriseComparisonDimension.DELEGATION],
        reliability_regressions=dimensions[EnterpriseComparisonDimension.RELIABILITY],
        policy_regressions=dimensions[EnterpriseComparisonDimension.POLICY],
        tenant_boundary_regressions=dimensions[EnterpriseComparisonDimension.TENANT_BOUNDARY],
        risk_weighted_regression_score=sum(
            metric.weighted_loss for metric in metrics if metric.metric_id in regressions
        ),
        evaluator_family_aggregates=_family_aggregates(metrics, resolved_policy.metric_tolerance),
        environment_compatible=not incompatible,
        verdict=verdict,
        created_at=timestamp,
        notes=(
            "Baseline and candidate use incompatible environment snapshots."
            if incompatible
            else "Enterprise dimensions compared with risk-weighted gates."
        ),
    )


def compare_enterprise_reports(
    baseline: EnterpriseEvaluationReport,
    candidate: EnterpriseEvaluationReport,
    *,
    policy: EnterpriseComparisonPolicy | None = None,
    baseline_snapshot: object | None = None,
    candidate_snapshot: object | None = None,
    target_metric_ids: Sequence[str] = (),
    target_failure_ids: Sequence[str] = (),
    target_cluster_id: str | None = None,
    created_at: datetime | None = None,
) -> BaselineComparison:
    """Derive comparable metrics from two typed evaluation reports."""

    if baseline.run_id == candidate.run_id:
        raise ComparisonError("Baseline and candidate report IDs must differ")
    if (baseline.dataset_id, baseline.dataset_version) != (
        candidate.dataset_id,
        candidate.dataset_version,
    ):
        raise ComparisonError("Baseline and candidate reports use different datasets")
    base_snapshot = (
        baseline_snapshot if baseline_snapshot is not None else baseline.environment_snapshot_id
    )
    candidate_snapshot_value = (
        candidate_snapshot if candidate_snapshot is not None else candidate.environment_snapshot_id
    )
    resolved_policy = policy or EnterpriseComparisonPolicy(policy_id="enterprise-default")
    base_scores = _scores_by_family(baseline)
    candidate_scores = _scores_by_family(candidate)
    if set(base_scores) != set(candidate_scores):
        raise ComparisonError(
            "Baseline and candidate reports must expose the same evaluator families"
        )
    metrics: list[EnterpriseComparisonMetric] = []
    for family in sorted(base_scores):
        base_values = base_scores[family]
        candidate_values = candidate_scores[family]
        dimension = _family_dimension(family, (*base_values, *candidate_values))
        metrics.append(
            EnterpriseComparisonMetric(
                metric_id=f"enterprise:{family}:mean_score",
                dimension=dimension,
                evaluator_family=family,
                metric_name="mean_score",
                baseline_value=sum(score.score for score in base_values) / len(base_values),
                candidate_value=sum(score.score for score in candidate_values)
                / len(candidate_values),
                higher_is_better=True,
                hard=dimension in resolved_policy.hard_dimensions,
                evidence_refs=_score_evidence((*base_values, *candidate_values)),
            )
        )
    target_ids = set(target_metric_ids)
    if target_failure_ids:
        target_ids.update(
            f"enterprise:{failure.evaluator_id}:mean_score"
            for failure in (*baseline.failures, *candidate.failures)
            if failure.failure_id in set(target_failure_ids)
        )
    transitions = _pass_to_fail(baseline, candidate)
    return compare_enterprise_metrics(
        metrics,
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        policy=resolved_policy,
        baseline_snapshot=base_snapshot,
        candidate_snapshot=candidate_snapshot_value,
        target_metric_ids=tuple(sorted(target_ids)),
        pass_to_fail_transitions=transitions,
        targeted_failure_ids=target_failure_ids,
        target_cluster_id=target_cluster_id,
        created_at=created_at,
    )


def _pair_metrics(
    baseline: Sequence[EnterpriseComparisonMetric | ComparisonMetric],
    candidate: Sequence[EnterpriseComparisonMetric | ComparisonMetric] | None,
) -> tuple[EnterpriseComparisonMetric, ...]:
    baseline_values = tuple(_coerce_metric(item) for item in baseline)
    if candidate is None:
        return baseline_values
    candidate_values = tuple(_coerce_metric(item) for item in candidate)
    base_by_id = {item.metric_id: item for item in baseline_values}
    current_by_id = {item.metric_id: item for item in candidate_values}
    if len(base_by_id) != len(baseline_values) or len(current_by_id) != len(candidate_values):
        raise ComparisonError("Enterprise metric IDs must be unique")
    if set(base_by_id) != set(current_by_id):
        raise ComparisonError("Baseline and candidate enterprise metrics must have the same IDs")
    result: list[EnterpriseComparisonMetric] = []
    for metric_id in sorted(base_by_id):
        base = base_by_id[metric_id]
        current = current_by_id[metric_id]
        if (base.dimension, base.evaluator_family, base.higher_is_better) != (
            current.dimension,
            current.evaluator_family,
            current.higher_is_better,
        ):
            raise ComparisonError(f"Enterprise metric {metric_id} changed its contract")
        result.append(
            base.model_copy(
                update={
                    "candidate_value": current.candidate_value,
                    "evidence_refs": tuple(
                        dict.fromkeys((*base.evidence_refs, *current.evidence_refs))
                    ),
                }
            )
        )
    return tuple(result)


def _coerce_metric(
    metric: EnterpriseComparisonMetric | ComparisonMetric,
) -> EnterpriseComparisonMetric:
    if isinstance(metric, EnterpriseComparisonMetric):
        return metric
    try:
        dimension = EnterpriseComparisonDimension(metric.dimension)
    except ValueError:
        dimension = EnterpriseComparisonDimension.BUSINESS_OUTCOMES
    return EnterpriseComparisonMetric(
        metric_id=metric.metric_id,
        dimension=dimension,
        evaluator_family=metric.slice_key or "legacy",
        metric_name=metric.metric_name,
        baseline_value=metric.baseline_value,
        candidate_value=metric.candidate_value,
        higher_is_better=metric.higher_is_better,
    )


def _apply_policy(
    metrics: Sequence[EnterpriseComparisonMetric],
    policy: EnterpriseComparisonPolicy,
) -> tuple[EnterpriseComparisonMetric, ...]:
    hard_dimensions = set(policy.hard_dimensions)
    weights: dict[EnterpriseComparisonDimension, ComparisonDimensionWeight] = {
        item.dimension: item for item in policy.dimension_weights
    }
    return tuple(
        metric.model_copy(
            update={
                "risk_weight": weights[metric.dimension].weight
                if metric.dimension in weights
                else metric.risk_weight,
                "hard": metric.hard
                or metric.dimension in hard_dimensions
                or (weights[metric.dimension].hard if metric.dimension in weights else False),
            }
        )
        for metric in metrics
    )


def _metric_regressed(metric: EnterpriseComparisonMetric, tolerance: float) -> bool:
    if metric.higher_is_better:
        return metric.candidate_value < metric.baseline_value - tolerance
    return metric.candidate_value > metric.baseline_value + tolerance


def _dimension_ids(
    metrics: Sequence[EnterpriseComparisonMetric],
    regressions: Sequence[str],
    dimension: EnterpriseComparisonDimension,
) -> tuple[str, ...]:
    regression_ids = set(regressions)
    return tuple(
        metric.metric_id
        for metric in metrics
        if metric.dimension == dimension and metric.metric_id in regression_ids
    )


def _target_improved(metrics: Sequence[EnterpriseComparisonMetric], tolerance: float) -> bool:
    return bool(metrics) and all(
        metric.candidate_value > metric.baseline_value + tolerance
        if metric.higher_is_better
        else metric.candidate_value < metric.baseline_value - tolerance
        for metric in metrics
    )


def _family_aggregates(
    metrics: Sequence[EnterpriseComparisonMetric], tolerance: float
) -> tuple[EvaluatorFamilyAggregate, ...]:
    grouped: dict[str, list[EnterpriseComparisonMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.evaluator_family, []).append(metric)
    return tuple(
        EvaluatorFamilyAggregate(
            family=family,
            metric_ids=tuple(metric.metric_id for metric in values),
            baseline_score=sum(metric.baseline_value for metric in values) / len(values),
            candidate_score=sum(metric.candidate_value for metric in values) / len(values),
            regression_count=sum(_metric_regressed(metric, tolerance) for metric in values),
            risk_weighted_loss=sum(metric.weighted_loss for metric in values),
            regressed=any(_metric_regressed(metric, tolerance) for metric in values),
        )
        for family, values in sorted(grouped.items())
    )


def _scores_by_family(
    report: EnterpriseEvaluationReport,
) -> dict[str, tuple[EvaluationScore, ...]]:
    grouped: dict[str, list[EvaluationScore]] = {}
    for score in report.scores:
        grouped.setdefault(score.evaluator_id, []).append(score)
    return {
        family: tuple(sorted(values, key=lambda score: score.score_id))
        for family, values in grouped.items()
    }


def _family_dimension(
    family: str, scores: Sequence[EvaluationScore]
) -> EnterpriseComparisonDimension:
    normalized = family.casefold()
    categories = {score.failure_category for score in scores if score.failure_category is not None}
    if FailureCategory.AUTHORIZATION in categories or "authorization" in normalized:
        return (
            EnterpriseComparisonDimension.TENANT_BOUNDARY
            if "tenant" in normalized
            else EnterpriseComparisonDimension.AUTHORIZATION
        )
    if FailureCategory.POLICY in categories or "policy" in normalized:
        return EnterpriseComparisonDimension.POLICY
    if FailureCategory.APPROVAL in categories or "approval" in normalized:
        return EnterpriseComparisonDimension.APPROVALS
    if (
        FailureCategory.STATE in categories
        or FailureCategory.DATA_INTEGRITY in categories
        or "state" in normalized
    ):
        return EnterpriseComparisonDimension.STATE_INTEGRITY
    if FailureCategory.DELEGATION in categories or "delegation" in normalized:
        return EnterpriseComparisonDimension.DELEGATION
    if FailureCategory.TOOL_SIDE_EFFECT in categories or "side_effect" in normalized:
        return EnterpriseComparisonDimension.TOOL_SIDE_EFFECTS
    if "workflow" in normalized:
        return EnterpriseComparisonDimension.WORKFLOW_COMPLETION
    if FailureCategory.BUSINESS_OUTCOME in categories or normalized.startswith("business"):
        return EnterpriseComparisonDimension.BUSINESS_OUTCOMES
    if any(marker in normalized for marker in ("latency", "duration")):
        return EnterpriseComparisonDimension.LATENCY
    if any(marker in normalized for marker in ("token", "usage")):
        return EnterpriseComparisonDimension.TOKEN_USAGE
    if "cost" in normalized:
        return EnterpriseComparisonDimension.COST
    if any(marker in normalized for marker in ("reliability", "retry", "timeout")):
        return EnterpriseComparisonDimension.RELIABILITY
    if any(marker in normalized for marker in ("safety", "security", "privacy")):
        return EnterpriseComparisonDimension.SECURITY
    return EnterpriseComparisonDimension.BUSINESS_OUTCOMES


def _score_evidence(scores: Sequence[EvaluationScore]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ref for score in scores for ref in score.evidence_refs))


def _pass_to_fail(
    baseline: EnterpriseEvaluationReport, candidate: EnterpriseEvaluationReport
) -> tuple[str, ...]:
    candidate_results = {
        (result.case_id, result.repeat_index): result for result in candidate.case_results
    }
    return tuple(
        f"pass_to_fail:{result.case_id}:repeat-{result.repeat_index}"
        for result in sorted(
            baseline.case_results, key=lambda item: (item.case_id, item.repeat_index)
        )
        if result.passed
        and (
            candidate_results.get((result.case_id, result.repeat_index)) is None
            or not candidate_results[(result.case_id, result.repeat_index)].passed
        )
    )


def _snapshots_incompatible(
    baseline: object | None,
    candidate: object | None,
    *,
    require: bool,
) -> bool:
    if not require:
        return False
    if (baseline is None) != (candidate is None):
        return True
    if baseline is None or candidate is None:
        return False
    checker = getattr(baseline, "is_compatible_with", None)
    if callable(checker):
        return not bool(checker(candidate))
    return baseline != candidate


def _legacy_metric(metric: EnterpriseComparisonMetric) -> ComparisonMetric:
    return ComparisonMetric(
        metric_id=metric.metric_id,
        baseline_value=metric.baseline_value,
        candidate_value=metric.candidate_value,
        higher_is_better=metric.higher_is_better,
        dimension=metric.dimension.value,
        slice_key=metric.evaluator_family,
        metric_name=metric.metric_name,
    )


def _comparison_id(baseline_run_id: str, candidate_run_id: str) -> str:
    value = f"{baseline_run_id}:{candidate_run_id}".encode("utf-8")
    return f"comparison-{sha256(value).hexdigest()[:16]}"


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


__all__ = [
    "ComparisonError",
    "EnterpriseComparisonRunner",
    "compare_enterprise_metrics",
    "compare_enterprise_reports",
]
