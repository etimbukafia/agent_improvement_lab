"""Reproducible baseline and candidate comparison workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Iterable, Sequence

from agent_improvement_lab.contracts.candidates import AgentCandidate
from agent_improvement_lab.contracts.cases import DatasetSplit, DatasetVersion
from agent_improvement_lab.contracts.common import utc_now
from agent_improvement_lab.contracts.evaluation import LabEvaluationReport
from agent_improvement_lab.contracts.experiments import (
    BaselineComparison,
    ComparisonMetric,
    ComparisonPolicy,
    ComparisonVerdict,
    ExperimentRun,
    RunManifest,
    RunStatus,
)
from agent_improvement_lab.contracts.failures import EvaluationFailure, EvaluationScore
from agent_improvement_lab.runner import EvaluationRunResult, PydanticEvalsRunner


class ComparisonError(ValueError):
    """Raised when a comparison cannot be reproduced safely."""


@dataclass(frozen=True)
class ComparisonResult:
    """Comparison record and all runs created by the workflow."""

    comparison: BaselineComparison
    baseline_run: ExperimentRun
    candidate_run: ExperimentRun
    baseline_result: EvaluationRunResult
    candidate_result: EvaluationRunResult
    holdout_baseline_run: ExperimentRun | None = None
    holdout_candidate_run: ExperimentRun | None = None
    holdout_baseline_result: EvaluationRunResult | None = None
    holdout_candidate_result: EvaluationRunResult | None = None


class ComparisonRunner:
    """Run two candidates with one dataset and one runtime configuration."""

    def __init__(
        self,
        runner: PydanticEvalsRunner,
        *,
        policy: ComparisonPolicy | None = None,
    ) -> None:
        self.runner = runner
        self.policy = policy or ComparisonPolicy(policy_id="default")

    def compare(
        self,
        dataset: DatasetVersion,
        baseline: AgentCandidate,
        candidate: AgentCandidate,
        baseline_manifest: RunManifest,
        candidate_manifest: RunManifest,
        *,
        target_failures: Sequence[EvaluationFailure] = (),
        target_cluster_id: str | None = None,
        holdout_dataset: DatasetVersion | None = None,
        repeat: int = 1,
        created_at: datetime | None = None,
    ) -> ComparisonResult:
        """Compare development and regression splits, then optionally run holdout."""

        self._validate_manifest_pair(
            dataset, baseline, candidate, baseline_manifest, candidate_manifest
        )
        development_dataset = _select_splits(dataset, self.policy.development_splits)
        baseline_result = self.runner.run_sync(
            development_dataset, baseline, baseline_manifest, repeat=repeat
        )
        candidate_result = self.runner.run_sync(
            development_dataset, candidate, candidate_manifest, repeat=repeat
        )
        timestamp = created_at or utc_now()
        development = _build_comparison(
            baseline_result.report,
            candidate_result.report,
            baseline_manifest.run_id,
            candidate_manifest.run_id,
            target_failures=target_failures,
            target_cluster_id=target_cluster_id,
            policy=self.policy,
            created_at=timestamp,
        )

        holdout_base_result: EvaluationRunResult | None = None
        holdout_candidate_result: EvaluationRunResult | None = None
        holdout_base_run: ExperimentRun | None = None
        holdout_candidate_run: ExperimentRun | None = None
        final = development
        holdout = holdout_dataset or _optional_split(dataset, self.policy.holdout_split)
        can_run_holdout = (
            not development.regressions
            and (development.target_improved or not self.policy.require_target_improvement)
            and development.verdict == ComparisonVerdict.IMPROVED
        )
        if can_run_holdout and holdout is not None:
            self._validate_holdout_dataset(holdout)
            holdout_base_manifest = _holdout_manifest(baseline_manifest, holdout, "baseline")
            holdout_candidate_manifest = _holdout_manifest(candidate_manifest, holdout, "candidate")
            self._validate_manifest_pair(
                holdout,
                baseline,
                candidate,
                holdout_base_manifest,
                holdout_candidate_manifest,
            )
            holdout_base_result = self.runner.run_sync(
                holdout, baseline, holdout_base_manifest, repeat=repeat
            )
            holdout_candidate_result = self.runner.run_sync(
                holdout, candidate, holdout_candidate_manifest, repeat=repeat
            )
            holdout_comparison = _build_comparison(
                holdout_base_result.report,
                holdout_candidate_result.report,
                holdout_base_manifest.run_id,
                holdout_candidate_manifest.run_id,
                target_failures=(),
                target_cluster_id=None,
                policy=self.policy,
                created_at=timestamp,
                metric_prefix="holdout.",
            )
            final = _merge_comparisons(development, holdout_comparison, timestamp)
            holdout_base_run = _experiment_run(holdout_base_result, holdout_base_manifest)
            holdout_candidate_run = _experiment_run(
                holdout_candidate_result, holdout_candidate_manifest
            )
        elif can_run_holdout and self.policy.require_holdout:
            final = _with_verdict(
                development,
                ComparisonVerdict.INCONCLUSIVE,
                timestamp,
                notes="Development gates passed, but no holdout dataset was supplied.",
            )
        elif not development.regressions and self.policy.require_target_improvement:
            final = _with_verdict(
                development,
                ComparisonVerdict.INCONCLUSIVE,
                timestamp,
                notes="Development gates did not show targeted improvement.",
            )

        final = final.model_copy(
            update={
                "holdout_checked": holdout_base_result is not None,
                "holdout_baseline_run_id": (
                    holdout_base_run.run_id if holdout_base_run is not None else None
                ),
                "holdout_candidate_run_id": (
                    holdout_candidate_run.run_id if holdout_candidate_run is not None else None
                ),
            }
        )
        return ComparisonResult(
            comparison=final,
            baseline_run=_experiment_run(baseline_result, baseline_manifest),
            candidate_run=_experiment_run(candidate_result, candidate_manifest),
            baseline_result=baseline_result,
            candidate_result=candidate_result,
            holdout_baseline_run=holdout_base_run,
            holdout_candidate_run=holdout_candidate_run,
            holdout_baseline_result=holdout_base_result,
            holdout_candidate_result=holdout_candidate_result,
        )

    def _validate_manifest_pair(
        self,
        dataset: DatasetVersion,
        baseline: AgentCandidate,
        candidate: AgentCandidate,
        baseline_manifest: RunManifest,
        candidate_manifest: RunManifest,
    ) -> None:
        manifests = (baseline_manifest, candidate_manifest)
        if baseline_manifest.run_id == candidate_manifest.run_id:
            raise ComparisonError("Baseline and candidate run IDs must differ")
        if baseline_manifest.candidate_id != baseline.candidate_id:
            raise ComparisonError("Baseline manifest does not reference the baseline candidate")
        if candidate_manifest.candidate_id != candidate.candidate_id:
            raise ComparisonError("Candidate manifest does not reference the candidate")
        for manifest in manifests:
            if (
                manifest.dataset_id != dataset.dataset_id
                or manifest.dataset_version != dataset.version
            ):
                raise ComparisonError("Run manifest dataset does not match the comparison dataset")
        identity_fields = (
            "dataset_id",
            "dataset_version",
            "toolset",
            "runtime_name",
            "runtime_version",
            "provider",
            "model",
            "seed",
            "metadata",
        )
        for field in identity_fields:
            if getattr(baseline_manifest, field) != getattr(candidate_manifest, field):
                raise ComparisonError(f"Baseline and candidate manifests differ in {field}")

    def _validate_holdout_dataset(self, dataset: DatasetVersion) -> None:
        if any(case.split != self.policy.holdout_split for case in dataset.cases):
            raise ComparisonError("A holdout dataset can contain only holdout cases")


def _select_splits(dataset: DatasetVersion, splits: Iterable[DatasetSplit]) -> DatasetVersion:
    allowed = set(splits)
    cases = tuple(case for case in dataset.cases if case.split in allowed)
    if not cases:
        raise ComparisonError("Dataset has no development or regression cases")
    return dataset.model_copy(
        update={
            "cases": cases,
            "description": (
                f"{dataset.description} ({','.join(sorted(split.value for split in allowed))})"
            ),
            "metadata": {
                **dataset.metadata,
                "comparison_splits": sorted(split.value for split in allowed),
            },
        }
    )


def _optional_split(dataset: DatasetVersion, split: DatasetSplit) -> DatasetVersion | None:
    cases = tuple(case for case in dataset.cases if case.split == split)
    if not cases:
        return None
    return dataset.model_copy(
        update={
            "cases": cases,
            "description": f"{dataset.description} ({split.value})",
            "metadata": {**dataset.metadata, "comparison_splits": [split.value]},
        }
    )


def _holdout_manifest(manifest: RunManifest, dataset: DatasetVersion, label: str) -> RunManifest:
    return manifest.model_copy(
        update={
            "run_id": f"{manifest.run_id}:holdout:{label}",
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.version,
        }
    )


def _build_comparison(
    baseline: LabEvaluationReport,
    candidate: LabEvaluationReport,
    baseline_run_id: str,
    candidate_run_id: str,
    *,
    target_failures: Sequence[EvaluationFailure],
    target_cluster_id: str | None,
    policy: ComparisonPolicy,
    created_at: datetime,
    metric_prefix: str = "",
) -> BaselineComparison:
    metrics = _compare_metrics(baseline, candidate, policy.metric_tolerance, metric_prefix)
    metric_regressions = tuple(
        metric.metric_id for metric in metrics if _metric_regressed(metric, policy.metric_tolerance)
    )
    pass_to_fail = _pass_to_fail_transitions(baseline, candidate)
    numerical = _numerical_regressions(baseline, candidate, policy)
    hard = _hard_regressions(baseline, candidate, numerical, policy)
    regressions = _unique((*metric_regressions, *pass_to_fail, *numerical))
    target_improved = _target_improved(baseline, candidate, target_failures, policy)
    if hard:
        verdict = ComparisonVerdict.REJECTED
    elif regressions:
        verdict = ComparisonVerdict.REGRESSED
    elif not target_improved and policy.require_target_improvement:
        verdict = ComparisonVerdict.INCONCLUSIVE
    else:
        verdict = ComparisonVerdict.IMPROVED
    return BaselineComparison(
        comparison_id=_comparison_id(baseline_run_id, candidate_run_id),
        experiment_id=f"experiment:{candidate_run_id}",
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        metrics=metrics,
        regressions=regressions,
        targeted_failure_ids=tuple(failure.failure_id for failure in target_failures),
        target_cluster_id=target_cluster_id,
        target_improved=target_improved,
        pass_to_fail_transitions=pass_to_fail,
        numerical_regressions=numerical,
        hard_regressions=hard,
        verdict=verdict,
        created_at=created_at,
        notes="",
    )


def _compare_metrics(
    baseline: LabEvaluationReport,
    candidate: LabEvaluationReport,
    tolerance: float,
    prefix: str,
) -> tuple[ComparisonMetric, ...]:
    baseline_groups = {(item.dimension, item.key): item for item in baseline.aggregates}
    candidate_groups = {(item.dimension, item.key): item for item in candidate.aggregates}
    common = sorted(set(baseline_groups) & set(candidate_groups))
    result: list[ComparisonMetric] = []
    for dimension, key in common:
        base = baseline_groups[(dimension, key)]
        current = candidate_groups[(dimension, key)]
        for metric_name, base_value, candidate_value, higher_is_better in (
            ("pass_rate", base.pass_rate, current.pass_rate, True),
            ("mean_score", base.mean_score, current.mean_score, True),
            ("failure_count", float(base.failure_count), float(current.failure_count), False),
        ):
            result.append(
                ComparisonMetric(
                    metric_id=f"{prefix}{dimension}:{key}:{metric_name}",
                    baseline_value=base_value,
                    candidate_value=candidate_value,
                    higher_is_better=higher_is_better,
                    dimension=dimension,
                    slice_key=key,
                    metric_name=metric_name,
                )
            )
    return tuple(result)


def _metric_regressed(metric: ComparisonMetric, tolerance: float) -> bool:
    if metric.higher_is_better:
        return metric.candidate_value < metric.baseline_value - tolerance
    return metric.candidate_value > metric.baseline_value + tolerance


def _pass_to_fail_transitions(
    baseline: LabEvaluationReport, candidate: LabEvaluationReport
) -> tuple[str, ...]:
    base = {(item.case_id, item.repeat_index): item for item in baseline.case_results}
    current = {(item.case_id, item.repeat_index): item for item in candidate.case_results}
    transitions = [
        f"pass_to_fail:{case_id}:repeat-{repeat_index}"
        for (case_id, repeat_index), base_result in sorted(base.items())
        if base_result.passed
        and (
            current.get((case_id, repeat_index)) is None
            or not current[(case_id, repeat_index)].passed
        )
    ]
    return tuple(transitions)


def _score_map(report: LabEvaluationReport) -> dict[tuple[str, int, str], EvaluationScore]:
    by_id = {score.score_id: score for score in report.scores}
    result: dict[tuple[str, int, str], EvaluationScore] = {}
    for case_result in report.case_results:
        for score_id in case_result.score_ids:
            score = by_id.get(score_id)
            if score is not None:
                result[(case_result.case_id, case_result.repeat_index, score.evaluator_id)] = score
    return result


def _numerical_regressions(
    baseline: LabEvaluationReport,
    candidate: LabEvaluationReport,
    policy: ComparisonPolicy,
) -> tuple[str, ...]:
    base = _score_map(baseline)
    current = _score_map(candidate)
    result: list[str] = []
    for key in sorted(set(base) & set(current)):
        case_id, repeat_index, evaluator_id = key
        if evaluator_id not in policy.numerical_evaluator_ids:
            continue
        base_score = base[key]
        current_score = current[key]
        if current_score.score < base_score.score - policy.metric_tolerance:
            result.append(f"numerical:{case_id}:repeat-{repeat_index}:{evaluator_id}")
    return tuple(result)


def _hard_regressions(
    baseline: LabEvaluationReport,
    candidate: LabEvaluationReport,
    numerical: Sequence[str],
    policy: ComparisonPolicy,
) -> tuple[str, ...]:
    base = _score_map(baseline)
    current = _score_map(candidate)
    result = list(numerical)
    for key in sorted(set(base) & set(current)):
        case_id, repeat_index, evaluator_id = key
        if evaluator_id not in policy.hard_evaluator_ids:
            continue
        if base[key].passed and not current[key].passed:
            result.append(f"hard:{case_id}:repeat-{repeat_index}:{evaluator_id}")
    return _unique(result)


def _target_improved(
    baseline: LabEvaluationReport,
    candidate: LabEvaluationReport,
    failures: Sequence[EvaluationFailure],
    policy: ComparisonPolicy,
) -> bool:
    if not failures:
        base_overall = _aggregate_value(baseline, "overall", "all", "mean_score")
        candidate_overall = _aggregate_value(candidate, "overall", "all", "mean_score")
        if base_overall is None or candidate_overall is None:
            return not policy.require_target_improvement
        return candidate_overall > base_overall + policy.metric_tolerance
    base = _score_map(baseline)
    current = _score_map(candidate)
    matches = 0
    for failure in failures:
        if failure.case_id is None:
            continue
        keys = [key for key in base if key[0] == failure.case_id and key[2] == failure.evaluator_id]
        if failure.score_id:
            repeat_match = _repeat_index(failure.score_id)
            if repeat_match is not None:
                keys = [key for key in keys if key[1] == repeat_match]
        for key in keys:
            if key not in current:
                continue
            matches += 1
            if current[key].score <= base[key].score + policy.metric_tolerance:
                return False
            break
    return matches == len(failures)


def _repeat_index(score_id: str) -> int | None:
    match = re.search(r":repeat-(\d+):", score_id)
    return int(match.group(1)) if match else None


def _aggregate_value(
    report: LabEvaluationReport, dimension: str, key: str, metric_name: str
) -> float | None:
    for item in report.aggregates:
        if item.dimension == dimension and item.key == key:
            return float(getattr(item, metric_name))
    return None


def _merge_comparisons(
    development: BaselineComparison,
    holdout: BaselineComparison,
    created_at: datetime,
) -> BaselineComparison:
    regressions = _unique((*development.regressions, *holdout.regressions))
    pass_to_fail = _unique(
        (*development.pass_to_fail_transitions, *holdout.pass_to_fail_transitions)
    )
    numerical = _unique((*development.numerical_regressions, *holdout.numerical_regressions))
    hard = _unique((*development.hard_regressions, *holdout.hard_regressions))
    if hard:
        verdict = ComparisonVerdict.REJECTED
    elif regressions:
        verdict = ComparisonVerdict.REGRESSED
    else:
        verdict = ComparisonVerdict.IMPROVED
    return development.model_copy(
        update={
            "metrics": (*development.metrics, *holdout.metrics),
            "regressions": regressions,
            "pass_to_fail_transitions": pass_to_fail,
            "numerical_regressions": numerical,
            "hard_regressions": hard,
            "verdict": verdict,
            "created_at": created_at,
            "notes": "Development and holdout comparisons passed."
            if verdict == ComparisonVerdict.IMPROVED
            else "Holdout comparison found a regression.",
        }
    )


def _with_verdict(
    comparison: BaselineComparison,
    verdict: ComparisonVerdict,
    created_at: datetime,
    *,
    notes: str,
) -> BaselineComparison:
    return comparison.model_copy(
        update={"verdict": verdict, "created_at": created_at, "notes": notes}
    )


def _comparison_id(baseline_run_id: str, candidate_run_id: str) -> str:
    digest = sha256(f"{baseline_run_id}:{candidate_run_id}".encode("utf-8")).hexdigest()[:16]
    return f"comparison-{digest}"


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _experiment_run(result: EvaluationRunResult, manifest: RunManifest) -> ExperimentRun:
    return ExperimentRun(
        run_id=manifest.run_id,
        experiment_id=f"experiment:{manifest.run_id}",
        manifest=manifest,
        status=RunStatus.COMPLETED,
        trace_ids=tuple(trace.trace_id for trace in result.traces),
        score_ids=tuple(score.score_id for score in result.report.scores),
        started_at=manifest.created_at,
        ended_at=result.report.created_at,
    )
