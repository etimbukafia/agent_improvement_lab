from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from enterprise_agent_improvement_lab.comparison import compare_enterprise_metrics
from enterprise_agent_improvement_lab.contracts.environments import EnvironmentSnapshot
from enterprise_agent_improvement_lab.contracts.experiments import (
    ComparisonVerdict,
    EnterpriseComparisonDimension,
    EnterpriseComparisonMetric,
    EnterpriseComparisonPolicy,
)
from enterprise_agent_improvement_lab.contracts.promotion import (
    PromotionEvidence,
    RiskClass,
    default_promotion_profile,
)
from enterprise_agent_improvement_lab.promotion import RiskAwarePromotionEngine

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _metric(
    metric_id: str,
    dimension: EnterpriseComparisonDimension,
    baseline: float,
    candidate: float,
    *,
    hard: bool = False,
    higher_is_better: bool = True,
) -> EnterpriseComparisonMetric:
    return EnterpriseComparisonMetric(
        metric_id=metric_id,
        dimension=dimension,
        evaluator_family=metric_id,
        metric_name="mean_score",
        baseline_value=baseline,
        candidate_value=candidate,
        higher_is_better=higher_is_better,
        hard=hard,
        evidence_refs=(f"evidence:{metric_id}",),
    )


def _snapshot(runtime_version: str = "1.0.0") -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        agent_registry_version="agents-1",
        tool_registry_version="tools-1",
        capability_registry_version="capabilities-1",
        policy_registry_version="policies-1",
        agent_definition_hash=sha256(b"agent").hexdigest(),
        runtime_name="test-runtime",
        runtime_version=runtime_version,
        environment_name="test",
        clock_mode="fixed",
        captured_at=NOW,
    )


def test_hard_security_regression_rejects_a_quality_improvement() -> None:
    comparison = compare_enterprise_metrics(
        (
            _metric("quality", EnterpriseComparisonDimension.BUSINESS_OUTCOMES, 0.5, 0.8),
            _metric("security", EnterpriseComparisonDimension.SECURITY, 1.0, 0.9),
        ),
        baseline_run_id="baseline",
        candidate_run_id="candidate",
    )

    assert comparison.verdict is ComparisonVerdict.REJECTED
    assert comparison.security_regressions == ("security",)
    assert comparison.risk_weighted_regression_score > 0


def test_evaluator_family_aggregation_and_environment_identity_are_preserved() -> None:
    policy = EnterpriseComparisonPolicy(
        policy_id="policy-1",
        hard_dimensions=(EnterpriseComparisonDimension.AUTHORIZATION,),
    )
    comparison = compare_enterprise_metrics(
        (_metric("auth", EnterpriseComparisonDimension.AUTHORIZATION, 1.0, 1.0),),
        baseline_run_id="baseline",
        candidate_run_id="candidate",
        policy=policy,
        baseline_snapshot=_snapshot(),
        candidate_snapshot=_snapshot(),
    )

    assert comparison.environment_compatible
    assert comparison.evaluator_family_aggregates[0].family == "auth"
    assert comparison.evaluator_family_aggregates[0].regressed is False


def test_token_usage_is_a_first_class_comparison_dimension() -> None:
    comparison = compare_enterprise_metrics(
        (
            _metric(
                "tokens",
                EnterpriseComparisonDimension.TOKEN_USAGE,
                100.0,
                120.0,
                higher_is_better=False,
            ),
        ),
        baseline_run_id="baseline",
        candidate_run_id="candidate",
    )

    assert comparison.token_usage_regressions == ("tokens",)
    assert comparison.numerical_regressions == ("tokens",)


def test_incompatible_environment_is_a_hard_comparison_failure() -> None:
    comparison = compare_enterprise_metrics(
        (_metric("quality", EnterpriseComparisonDimension.BUSINESS_OUTCOMES, 0.5, 0.8),),
        baseline_run_id="baseline",
        candidate_run_id="candidate",
        baseline_snapshot=_snapshot(),
        candidate_snapshot=_snapshot("2.0.0"),
    )

    assert comparison.verdict is ComparisonVerdict.REJECTED
    assert not comparison.environment_compatible
    assert "environment_incompatible" in comparison.hard_regressions


def test_financial_profile_requires_boundaries_and_a_human_reviewer() -> None:
    comparison = compare_enterprise_metrics(
        (_metric("quality", EnterpriseComparisonDimension.BUSINESS_OUTCOMES, 0.5, 0.8),),
        baseline_run_id="baseline",
        candidate_run_id="candidate",
    )
    profile = default_promotion_profile(RiskClass.FINANCIAL)
    engine = RiskAwarePromotionEngine()

    missing = engine.evaluate("candidate-1", comparison, profile)
    assert not missing.eligible
    assert "authorization" in missing.missing_evidence_ids
    assert "security_reviewer" in missing.missing_reviewer_roles
    assert missing.human_decision_required

    evidence = tuple(
        PromotionEvidence(
            evidence_id=item.evidence_id,
            evidence_type=item.evidence_type,
            passed=True,
            summary="Required evidence passed.",
            evidence_refs=(item.evidence_id,),
        )
        for item in profile.required_evidence
    )
    eligible = engine.evaluate(
        "candidate-1",
        comparison,
        profile,
        evidence=evidence,
        reviewer_roles=("security_reviewer", "business_owner"),
    )
    assert eligible.eligible
    assert eligible.human_decision_required
