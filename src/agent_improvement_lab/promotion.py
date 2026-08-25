"""Human-controlled promotion and rollback workflow."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from agent_improvement_lab.contracts.candidates import CandidateStatus
from agent_improvement_lab.contracts.common import utc_now
from agent_improvement_lab.contracts.experiments import (
    ActiveCandidatePointer,
    BaselineComparison,
    ComparisonMetric,
    PromotionDecision,
    PromotionEvaluation,
    PromotionGateKind,
    PromotionGateResult,
    PromotionOutcome,
    PromotionPolicy,
)
from agent_improvement_lab.storage import SQLiteStore


class PromotionError(ValueError):
    """Raised when promotion evidence or a human decision is invalid."""


_MANDATORY_HARD_GATES = (
    "no_security_regression",
    "no_protected_argument_regression",
    "no_numerical_consistency_regression",
)
_SUPPORTED_GATES = frozenset(
    {
        *_MANDATORY_HARD_GATES,
        "target_improvement",
        "holdout_non_declining",
        "overall_improvement",
        "no_regressions",
        "comparison_improved",
    }
)


class PromotionEngine:
    """Evaluate configurable hard and soft gates without making a decision."""

    def __init__(self, policy: PromotionPolicy | None = None) -> None:
        self.policy = policy or PromotionPolicy(policy_id="default", version="1.0.0")

    def evaluate(
        self,
        candidate_id: str,
        comparison: BaselineComparison,
        *,
        created_at: datetime | None = None,
    ) -> PromotionEvaluation:
        """Return gate evidence for a candidate and comparison."""

        configured_required = []
        if self.policy.require_target_improvement:
            configured_required.append("target_improvement")
        if self.policy.require_holdout_check:
            configured_required.append("holdout_non_declining")
        hard_ids = _unique((*_MANDATORY_HARD_GATES, *configured_required, *self.policy.hard_gates))
        soft_ids = self.policy.soft_gates
        unknown = sorted((set(hard_ids) | set(soft_ids)) - _SUPPORTED_GATES)
        if unknown:
            raise PromotionError(f"Unsupported promotion gate IDs: {', '.join(unknown)}")
        hard = tuple(
            self._evaluate_gate(gate_id, PromotionGateKind.HARD, comparison) for gate_id in hard_ids
        )
        soft = tuple(
            self._evaluate_gate(gate_id, PromotionGateKind.SOFT, comparison) for gate_id in soft_ids
        )
        return PromotionEvaluation(
            candidate_id=candidate_id,
            comparison_id=comparison.comparison_id,
            policy_id=self.policy.policy_id,
            hard_gates=hard,
            soft_gates=soft,
            eligible=all(gate.passed for gate in hard),
            created_at=created_at or utc_now(),
        )

    def _evaluate_gate(
        self,
        gate_id: str,
        kind: PromotionGateKind,
        comparison: BaselineComparison,
    ) -> PromotionGateResult:
        if gate_id == "no_security_regression":
            passed = not any(
                _is_security_regression(item)
                for item in (*comparison.regressions, *comparison.hard_regressions)
            )
            return _gate(gate_id, kind, passed, "No safety or security regression exists.")
        if gate_id == "no_protected_argument_regression":
            passed = not any(
                "protected_argument_integrity" in item
                for item in (*comparison.regressions, *comparison.hard_regressions)
            )
            return _gate(gate_id, kind, passed, "No protected argument regression exists.")
        if gate_id == "no_numerical_consistency_regression":
            passed = not comparison.numerical_regressions and not any(
                "cross_turn_numerical_consistency" in item for item in comparison.hard_regressions
            )
            return _gate(gate_id, kind, passed, "No numerical consistency regression exists.")
        if gate_id == "target_improvement":
            passed = (
                bool(comparison.target_cluster_id or comparison.targeted_failure_ids)
                and comparison.target_improved
            )
            return _gate(
                gate_id,
                kind,
                passed,
                "The targeted failure set improved."
                if passed
                else "The targeted failure set did not improve.",
                observed=comparison.target_improved,
                required=True,
            )
        if gate_id == "holdout_non_declining":
            passed, reason = _holdout_result(comparison, self.policy.holdout_tolerance)
            return _gate(
                gate_id, kind, passed, reason, observed=comparison.holdout_checked, required=True
            )
        if gate_id == "overall_improvement":
            delta = _overall_delta(comparison.metrics)
            passed = delta is not None and delta > self.policy.metric_tolerance
            return _gate(
                gate_id,
                kind,
                passed,
                "Overall mean score improved." if passed else "Overall mean score did not improve.",
                observed=delta,
                required=self.policy.metric_tolerance,
            )
        if gate_id == "no_regressions":
            passed = not comparison.regressions
            return _gate(gate_id, kind, passed, "No comparison regression exists.")
        if gate_id == "comparison_improved":
            passed = comparison.verdict.value == "improved"
            return _gate(gate_id, kind, passed, "The comparison verdict is improved.")
        raise PromotionError(f"Unsupported promotion gate ID: {gate_id}")


class PromotionService:
    """Persist explicit human decisions and maintain the active pointer."""

    def __init__(
        self,
        store: SQLiteStore,
        policy: PromotionPolicy,
        *,
        pointer_id: str = "active",
    ) -> None:
        self.store = store
        self.policy = policy
        self.pointer_id = pointer_id
        self.engine = PromotionEngine(policy)

    def evaluate(
        self,
        candidate_id: str,
        comparison: BaselineComparison,
        *,
        created_at: datetime | None = None,
    ) -> PromotionEvaluation:
        """Evaluate gates without changing stored state."""

        return self.engine.evaluate(candidate_id, comparison, created_at=created_at)

    def decide(
        self,
        *,
        decision_id: str,
        candidate_id: str,
        comparison: BaselineComparison,
        outcome: PromotionOutcome,
        reviewer: str,
        reason: str,
        decided_at: datetime | None = None,
    ) -> PromotionDecision:
        """Record one human decision. Approval requires all hard gates."""

        self._save_policy()
        self._save_comparison(comparison)
        evaluation = self.evaluate(candidate_id, comparison, created_at=decided_at)
        timestamp = decided_at or utc_now()
        if self.store.candidates.get(candidate_id) is None:
            raise PromotionError(f"Candidate {candidate_id} was not found")
        if outcome == PromotionOutcome.APPROVED and not evaluation.eligible:
            failed = ", ".join(gate.gate_id for gate in evaluation.hard_gates if not gate.passed)
            raise PromotionError(f"Cannot approve candidate; hard gates failed: {failed}")
        if outcome == PromotionOutcome.ROLLBACK:
            raise PromotionError("Use rollback() to record a rollback decision")

        active = self.store.active_candidate.get(self.pointer_id)
        if outcome == PromotionOutcome.APPROVED and active is not None:
            if active.candidate_id == candidate_id:
                raise PromotionError("Candidate is already active")
        previous = active.candidate_id if outcome == PromotionOutcome.APPROVED and active else None
        decision = PromotionDecision(
            decision_id=decision_id,
            candidate_id=candidate_id,
            comparison_id=comparison.comparison_id,
            policy_id=self.policy.policy_id,
            outcome=outcome,
            reviewer=reviewer,
            decided_at=timestamp,
            reason=reason,
            previous_active_candidate_id=previous,
        )
        self.store.decisions.save(decision)
        if outcome == PromotionOutcome.APPROVED:
            self.store.active_candidate.save(
                ActiveCandidatePointer(
                    pointer_id=self.pointer_id,
                    candidate_id=candidate_id,
                    decision_id=decision.decision_id,
                    updated_at=timestamp,
                )
            )
            self._set_candidate_status(candidate_id, CandidateStatus.APPROVED)
            if previous is not None:
                self._set_candidate_status(previous, CandidateStatus.RETIRED)
        elif outcome == PromotionOutcome.REJECTED:
            self._set_candidate_status(candidate_id, CandidateStatus.REJECTED)
        else:
            self._set_candidate_status(candidate_id, CandidateStatus.EVALUATED)
        return decision

    def rollback(
        self,
        *,
        decision_id: str,
        rollback_decision_id: str,
        reviewer: str,
        reason: str,
        decided_at: datetime | None = None,
    ) -> PromotionDecision:
        """Restore the candidate that was active before an approval."""

        original = self.store.decisions.get(decision_id)
        if original is None:
            raise PromotionError(f"Promotion decision {decision_id} was not found")
        if original.outcome != PromotionOutcome.APPROVED:
            raise PromotionError("Only an approved decision can be rolled back")
        active = self.store.active_candidate.get(self.pointer_id)
        if active is None or active.candidate_id != original.candidate_id:
            raise PromotionError("The approved candidate is not the active candidate")
        restored = original.previous_active_candidate_id
        if restored is None:
            raise PromotionError("The approved decision has no previous candidate to restore")
        if self.store.candidates.get(restored) is None:
            raise PromotionError(f"Previous candidate {restored} was not found")
        timestamp = decided_at or utc_now()
        rollback = PromotionDecision(
            decision_id=rollback_decision_id,
            candidate_id=original.candidate_id,
            comparison_id=original.comparison_id,
            policy_id=original.policy_id,
            outcome=PromotionOutcome.ROLLBACK,
            reviewer=reviewer,
            decided_at=timestamp,
            reason=reason,
            previous_active_candidate_id=active.candidate_id,
            rollback_of_decision_id=original.decision_id,
            restored_candidate_id=restored,
        )
        self.store.decisions.save(rollback)
        self.store.active_candidate.save(
            ActiveCandidatePointer(
                pointer_id=self.pointer_id,
                candidate_id=restored,
                decision_id=rollback.decision_id,
                updated_at=timestamp,
            )
        )
        self._set_candidate_status(original.candidate_id, CandidateStatus.RETIRED)
        self._set_candidate_status(restored, CandidateStatus.APPROVED)
        return rollback

    def active_candidate(self) -> ActiveCandidatePointer | None:
        """Return the current active-candidate pointer."""

        return self.store.active_candidate.get(self.pointer_id)

    def _save_policy(self) -> None:
        existing = self.store.policies.get(self.policy.policy_id)
        if existing is None:
            self.store.policies.save(self.policy)
        elif existing != self.policy:
            raise PromotionError(
                f"Stored policy {self.policy.policy_id} does not match the decision"
            )

    def _save_comparison(self, comparison: BaselineComparison) -> None:
        existing = self.store.comparisons.get(comparison.comparison_id)
        if existing is None:
            self.store.comparisons.save(comparison)
        elif existing != comparison:
            raise PromotionError(
                f"Stored comparison {comparison.comparison_id} does not match the decision"
            )

    def _set_candidate_status(self, candidate_id: str, status: CandidateStatus) -> None:
        candidate = self.store.candidates.get(candidate_id)
        if candidate is not None and candidate.status != status:
            self.store.candidates.save(candidate.model_copy(update={"status": status}))


def _gate(
    gate_id: str,
    kind: PromotionGateKind,
    passed: bool,
    reason: str,
    *,
    observed: float | int | str | bool | None = None,
    required: float | int | str | bool | None = None,
) -> PromotionGateResult:
    return PromotionGateResult(
        gate_id=gate_id,
        kind=kind,
        passed=passed,
        reason=reason,
        observed=observed,
        required=required,
    )


def _overall_delta(metrics: Iterable[ComparisonMetric]) -> float | None:
    for metric in metrics:
        if (
            metric.dimension == "overall"
            and metric.slice_key == "all"
            and metric.metric_name == "mean_score"
        ):
            return metric.delta
    return None


def _holdout_result(comparison: BaselineComparison, tolerance: float) -> tuple[bool, str]:
    if not comparison.holdout_checked:
        return False, "Holdout evaluation was not checked."
    if comparison.numerical_regressions or comparison.pass_to_fail_transitions:
        return False, "The holdout evaluation contains a hard regression."
    metrics = tuple(
        metric for metric in comparison.metrics if metric.metric_id.startswith("holdout.")
    )
    for metric in metrics:
        if _metric_regressed(metric, tolerance):
            return False, "The holdout evaluation declined."
    if not metrics:
        return False, "Holdout metrics are missing."
    return True, "Holdout performance did not decline."


def _metric_regressed(metric: ComparisonMetric, tolerance: float) -> bool:
    if metric.higher_is_better:
        return metric.candidate_value < metric.baseline_value - tolerance
    return metric.candidate_value > metric.baseline_value + tolerance


def _is_security_regression(value: str) -> bool:
    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in (
            "safety.",
            "security",
            "instruction_override",
            "authorization_boundary",
            "protected_argument",
        )
    )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
