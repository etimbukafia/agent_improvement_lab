"""Command-line access to the public Lab workflows."""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from agent_improvement_lab.candidates import render_candidate_diff
from agent_improvement_lab.comparison import ComparisonRunner
from agent_improvement_lab.contracts.candidates import AgentCandidate
from agent_improvement_lab.contracts.cases import DatasetVersion
from agent_improvement_lab.contracts.evaluation import LabEvaluationReport
from agent_improvement_lab.contracts.experiments import (
    ExperimentRun,
    PromotionOutcome,
    PromotionPolicy,
    RunManifest,
    RunStatus,
)
from agent_improvement_lab.contracts.failures import (
    AnnotationStatus,
    EvaluationFailure,
    HumanAnnotation,
    Severity,
)
from agent_improvement_lab.contracts.sessions import SessionSummary
from agent_improvement_lab.dashboard import DashboardQueryService, summarize_trace
from agent_improvement_lab.datasets import load_dataset
from agent_improvement_lab.failure_mining import cluster_failures, normalize_failures
from agent_improvement_lab.promotion import PromotionEngine, PromotionService
from agent_improvement_lab.review import AnnotationService
from agent_improvement_lab.runner import EvaluationRunResult, PydanticEvalsRunner
from agent_improvement_lab.serialization import read_json, stable_json_dumps, write_json
from agent_improvement_lab.storage import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    """Build the Lab command-line parser."""

    parser = argparse.ArgumentParser(prog="agent-improvement-lab")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_dataset_commands(commands)
    _add_experiment_commands(commands)
    _add_failure_commands(commands)
    _add_annotation_commands(commands)
    _add_candidate_commands(commands)
    _add_promotion_commands(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one CLI command and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        value = args.handler(args)
        if value is not None:
            _print(value)
        return 0
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _add_dataset_commands(commands: Any) -> None:
    group = commands.add_parser("dataset", help="Validate evaluation datasets.")
    subcommands = group.add_subparsers(dest="dataset_command", required=True)
    validate = subcommands.add_parser("validate")
    validate.add_argument("path", type=Path)
    validate.set_defaults(handler=_validate_dataset)


def _add_experiment_commands(commands: Any) -> None:
    group = commands.add_parser("experiment", help="Run and compare experiments.")
    subcommands = group.add_subparsers(dest="experiment_command", required=True)

    run = subcommands.add_parser("run")
    _add_run_arguments(run)
    run.set_defaults(handler=_run_experiment)

    compare = subcommands.add_parser("compare")
    _add_run_arguments(compare, comparison=True)
    compare.add_argument("--target-failures", type=Path)
    compare.add_argument("--target-cluster-id")
    compare.add_argument("--holdout-dataset", type=Path)
    compare.add_argument("--output-dir", type=Path)
    compare.set_defaults(handler=_compare_experiments)


def _add_run_arguments(parser: Any, *, comparison: bool = False) -> None:
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--runtime", required=True, help="Runtime object as module:attribute.")
    parser.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    parser.add_argument("--repeat", type=int, default=1)
    if comparison:
        parser.add_argument("--baseline", required=True, type=Path)
        parser.add_argument("--candidate", required=True, type=Path)
        parser.add_argument("--baseline-manifest", required=True, type=Path)
        parser.add_argument("--candidate-manifest", required=True, type=Path)
    else:
        parser.add_argument("--candidate", required=True, type=Path)
        parser.add_argument("--manifest", required=True, type=Path)
        parser.add_argument("--report", type=Path)


def _add_failure_commands(commands: Any) -> None:
    group = commands.add_parser("failures", help="Normalize and inspect failures.")
    subcommands = group.add_subparsers(dest="failure_command", required=True)

    normalize = subcommands.add_parser("normalize")
    normalize.add_argument("--dataset", required=True, type=Path)
    normalize.add_argument("--report", required=True, type=Path)
    normalize.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    normalize.set_defaults(handler=_normalize_failures)

    listing = subcommands.add_parser("list")
    listing.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    listing.set_defaults(handler=_list_failures)


def _add_annotation_commands(commands: Any) -> None:
    group = commands.add_parser("annotations", help="Manage append-only human annotations.")
    subcommands = group.add_subparsers(dest="annotation_command", required=True)

    create = subcommands.add_parser("create")
    _add_annotation_common(create)
    create.set_defaults(handler=_create_annotation)

    transition = subcommands.add_parser("transition")
    transition.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    transition.add_argument("--current-id", required=True)
    transition.add_argument("--annotation-id", required=True)
    transition.add_argument(
        "--status",
        required=True,
        choices=[item.value for item in AnnotationStatus],
    )
    transition.add_argument("--reviewer", required=True)
    transition.add_argument("--reviewed-at", required=True, type=_datetime)
    transition.add_argument("--expected-behavior")
    transition.add_argument("--severity", choices=[item.value for item in Severity])
    transition.add_argument("--notes")
    transition.add_argument("--label-confidence", type=float)
    transition.set_defaults(handler=_transition_annotation)

    listing = subcommands.add_parser("list")
    listing.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    listing.set_defaults(handler=_list_annotations)


def _add_annotation_common(parser: Any) -> None:
    parser.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    parser.add_argument("--annotation-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--target-type", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--reviewed-at", required=True, type=_datetime)
    parser.add_argument("--expected-behavior", required=True)
    parser.add_argument("--label-confidence", required=True, type=float)
    parser.add_argument("--severity", choices=[item.value for item in Severity])
    parser.add_argument("--notes", default="")


def _add_candidate_commands(commands: Any) -> None:
    group = commands.add_parser("candidates", help="Inspect stored candidates.")
    subcommands = group.add_subparsers(dest="candidate_command", required=True)

    listing = subcommands.add_parser("list")
    listing.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    listing.set_defaults(handler=_list_candidates)

    show = subcommands.add_parser("show")
    show.add_argument("candidate_id")
    show.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    show.set_defaults(handler=_show_candidate)


def _add_promotion_commands(commands: Any) -> None:
    group = commands.add_parser("promotion", help="Evaluate and record human promotion decisions.")
    subcommands = group.add_subparsers(dest="promotion_command", required=True)

    evaluate = subcommands.add_parser("evaluate")
    evaluate.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    evaluate.add_argument("--candidate-id", required=True)
    evaluate.add_argument("--comparison-id", required=True)
    evaluate.set_defaults(handler=_evaluate_promotion)

    decide = subcommands.add_parser("decide")
    decide.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    decide.add_argument("--candidate-id", required=True)
    decide.add_argument("--comparison-id", required=True)
    decide.add_argument("--decision-id", required=True)
    decide.add_argument(
        "--outcome",
        required=True,
        choices=[item.value for item in PromotionOutcome if item != PromotionOutcome.ROLLBACK],
    )
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--reason", required=True)
    decide.add_argument("--decided-at", type=_datetime)
    decide.set_defaults(handler=_decide_promotion)

    rollback = subcommands.add_parser("rollback")
    rollback.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    rollback.add_argument("--decision-id", required=True)
    rollback.add_argument("--rollback-decision-id", required=True)
    rollback.add_argument("--reviewer", required=True)
    rollback.add_argument("--reason", required=True)
    rollback.add_argument("--decided-at", type=_datetime)
    rollback.set_defaults(handler=_rollback_promotion)

    listing = subcommands.add_parser("list")
    listing.add_argument("--database", type=Path, default=Path("artifacts/lab.sqlite3"))
    listing.set_defaults(handler=_list_promotions)


def _validate_dataset(args: Any) -> DatasetVersion:
    return load_dataset(args.path)


def _run_experiment(args: Any) -> dict[str, Any]:
    dataset = load_dataset(args.dataset)
    candidate = _load_model(AgentCandidate, args.candidate)
    manifest = _load_model(RunManifest, args.manifest)
    runtime = _load_runtime(args.runtime)
    result = PydanticEvalsRunner(runtime).run_sync(dataset, candidate, manifest, repeat=args.repeat)
    with SQLiteStore(args.database) as store:
        store.datasets.save(dataset)
        store.candidates.save(candidate)
        _save_run(store, result, manifest)
    if args.report:
        write_json(args.report, result.report)
    return {"run": _experiment(result, manifest), "report": result.report}


def _compare_experiments(args: Any) -> dict[str, Any]:
    dataset = load_dataset(args.dataset)
    baseline = _load_model(AgentCandidate, args.baseline)
    candidate = _load_model(AgentCandidate, args.candidate)
    baseline_manifest = _load_model(RunManifest, args.baseline_manifest)
    candidate_manifest = _load_model(RunManifest, args.candidate_manifest)
    holdout = load_dataset(args.holdout_dataset) if args.holdout_dataset else None
    targets = (
        tuple(EvaluationFailure.model_validate(item) for item in read_json(args.target_failures))
        if args.target_failures
        else ()
    )
    result = ComparisonRunner(PydanticEvalsRunner(_load_runtime(args.runtime))).compare(
        dataset,
        baseline,
        candidate,
        baseline_manifest,
        candidate_manifest,
        target_failures=targets,
        target_cluster_id=args.target_cluster_id,
        holdout_dataset=holdout,
        repeat=args.repeat,
    )
    with SQLiteStore(args.database) as store:
        store.datasets.save(dataset)
        if holdout is not None:
            store.datasets.save(holdout)
        store.candidates.save(baseline)
        store.candidates.save(candidate)
        _save_run(store, result.baseline_result, result.baseline_run.manifest)
        _save_run(store, result.candidate_result, result.candidate_run.manifest)
        if result.holdout_baseline_result is not None and result.holdout_baseline_run is not None:
            _save_run(store, result.holdout_baseline_result, result.holdout_baseline_run.manifest)
        if result.holdout_candidate_result is not None and result.holdout_candidate_run is not None:
            _save_run(store, result.holdout_candidate_result, result.holdout_candidate_run.manifest)
        store.comparisons.save(result.comparison)
    if args.output_dir:
        write_json(args.output_dir / "comparison.json", result.comparison)
        write_json(args.output_dir / "baseline-report.json", result.baseline_result.report)
        write_json(args.output_dir / "candidate-report.json", result.candidate_result.report)
    return {
        "comparison": result.comparison,
        "baseline_report": result.baseline_result.report,
        "candidate_report": result.candidate_result.report,
    }


def _normalize_failures(args: Any) -> tuple[EvaluationFailure, ...]:
    dataset = load_dataset(args.dataset)
    report = _load_model(LabEvaluationReport, args.report)
    with SQLiteStore(args.database) as store:
        experiment = store.experiments.get(report.run_id)
        trace_ids = set(experiment.trace_ids) if experiment is not None else set()
        traces = tuple(trace for trace in store.traces.list() if trace.trace_id in trace_ids)
        failures = normalize_failures(report, dataset, traces=traces)
        clusters = cluster_failures(failures)
        for failure in failures:
            store.failures.save(failure)
        for cluster in clusters:
            store.failure_clusters.save(cluster)
    return failures


def _list_failures(args: Any) -> tuple[EvaluationFailure, ...]:
    with SQLiteStore(args.database) as store:
        return tuple(store.failures.list())


def _create_annotation(args: Any) -> HumanAnnotation:
    with SQLiteStore(args.database) as store:
        return AnnotationService(store.annotations).create_unreviewed(
            annotation_id=args.annotation_id,
            target_id=args.target_id,
            target_type=args.target_type,
            reviewer=args.reviewer,
            reviewed_at=args.reviewed_at,
            expected_behavior=args.expected_behavior,
            label_confidence=args.label_confidence,
            severity=Severity(args.severity) if args.severity else None,
            notes=args.notes,
        )


def _transition_annotation(args: Any) -> HumanAnnotation:
    with SQLiteStore(args.database) as store:
        return AnnotationService(store.annotations).transition(
            args.current_id,
            annotation_id=args.annotation_id,
            status=AnnotationStatus(args.status),
            reviewer=args.reviewer,
            reviewed_at=args.reviewed_at,
            expected_behavior=args.expected_behavior,
            severity=Severity(args.severity) if args.severity else None,
            notes=args.notes,
            label_confidence=args.label_confidence,
        )


def _list_annotations(args: Any) -> list[HumanAnnotation]:
    with SQLiteStore(args.database) as store:
        return store.annotations.list()


def _list_candidates(args: Any) -> list[AgentCandidate]:
    with SQLiteStore(args.database) as store:
        return store.candidates.list()


def _show_candidate(args: Any) -> dict[str, Any]:
    with SQLiteStore(args.database) as store:
        candidate = store.candidates.get(args.candidate_id)
        if candidate is None:
            raise KeyError(f"Candidate {args.candidate_id!r} was not found")
        return {"candidate": candidate, "diff": render_candidate_diff(candidate)}


def _evaluate_promotion(args: Any) -> Any:
    with SQLiteStore(args.database) as store:
        comparison = store.comparisons.get(args.comparison_id)
        if comparison is None:
            raise KeyError(f"Comparison {args.comparison_id!r} was not found")
        policy = _policy_for(store)
        return PromotionEngine(policy).evaluate(args.candidate_id, comparison)


def _decide_promotion(args: Any) -> Any:
    with SQLiteStore(args.database) as store:
        comparison = store.comparisons.get(args.comparison_id)
        if comparison is None:
            raise KeyError(f"Comparison {args.comparison_id!r} was not found")
        policy = _policy_for(store)
        return PromotionService(store, policy).decide(
            decision_id=args.decision_id,
            candidate_id=args.candidate_id,
            comparison=comparison,
            outcome=PromotionOutcome(args.outcome),
            reviewer=args.reviewer,
            reason=args.reason,
            decided_at=args.decided_at,
        )


def _rollback_promotion(args: Any) -> Any:
    with SQLiteStore(args.database) as store:
        policy = _policy_for(store)
        return PromotionService(store, policy).rollback(
            decision_id=args.decision_id,
            rollback_decision_id=args.rollback_decision_id,
            reviewer=args.reviewer,
            reason=args.reason,
            decided_at=args.decided_at,
        )


def _list_promotions(args: Any) -> Any:
    with SQLiteStore(args.database) as store:
        return DashboardQueryService(store).promotion()


def _save_run(store: SQLiteStore, result: EvaluationRunResult, manifest: RunManifest) -> None:
    experiment = _experiment(result, manifest)
    store.experiments.save(experiment)
    score_ids_by_trace = _score_ids_by_trace(result.report)
    for trace in result.traces:
        store.traces.save(trace)
        store.trace_summaries.save(
            summarize_trace(trace, score_ids_by_trace.get(trace.trace_id, ()))
        )
    for score in result.report.scores:
        store.scores.save(score)
    _save_sessions(store, result, score_ids_by_trace)


def _save_sessions(
    store: SQLiteStore,
    result: EvaluationRunResult,
    score_ids_by_trace: dict[str, tuple[str, ...]],
) -> None:
    traces_by_session: dict[str, list[Any]] = {}
    for trace in result.traces:
        if trace.session_id:
            traces_by_session.setdefault(trace.session_id, []).append(trace)
    scores_by_id = {score.score_id: score for score in result.report.scores}
    for session_id, traces in traces_by_session.items():
        ordered = sorted(traces, key=lambda trace: (trace.started_at, trace.trace_id))
        score_ids = tuple(
            score_id
            for trace in ordered
            for score_id in score_ids_by_trace.get(trace.trace_id, ())
            if score_id in scores_by_id
        )
        store.sessions.save(
            SessionSummary(
                session_id=session_id,
                trace_ids=tuple(trace.trace_id for trace in ordered),
                started_at=min(trace.started_at for trace in ordered),
                ended_at=max(
                    (trace.ended_at for trace in ordered if trace.ended_at is not None),
                    default=None,
                ),
                total_latency_ms=sum(summarize_trace(trace).total_latency_ms for trace in ordered),
                total_tokens=sum(summarize_trace(trace).total_tokens for trace in ordered),
                evaluation_score_ids=score_ids,
            )
        )


def _score_ids_by_trace(report: LabEvaluationReport) -> dict[str, tuple[str, ...]]:
    return {
        result.trace_id: result.score_ids
        for result in report.case_results
        if result.trace_id is not None
    }


def _experiment(result: EvaluationRunResult, manifest: RunManifest) -> ExperimentRun:
    session_ids = tuple(
        dict.fromkeys(trace.session_id for trace in result.traces if trace.session_id is not None)
    )
    return ExperimentRun(
        run_id=manifest.run_id,
        experiment_id=f"experiment:{manifest.run_id}",
        manifest=manifest,
        status=RunStatus.COMPLETED,
        trace_ids=tuple(trace.trace_id for trace in result.traces),
        session_ids=session_ids,
        score_ids=tuple(score.score_id for score in result.report.scores),
        started_at=manifest.created_at,
        ended_at=result.report.created_at,
    )


def _load_model(model_type: type[Any], path: Path) -> Any:
    return model_type.model_validate(read_json(path))


def _load_runtime(reference: str) -> Any:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("Runtime must use module:attribute syntax")
    value = getattr(importlib.import_module(module_name), attribute)
    if isinstance(value, type) or (callable(value) and not hasattr(value, "execute")):
        value = value()
    if not hasattr(value, "execute"):
        raise TypeError("Runtime object must provide execute(case, candidate)")
    return value


def _policy_for(store: SQLiteStore) -> PromotionPolicy:
    policies = store.policies.list()
    return policies[0] if policies else PromotionPolicy(policy_id="default", version="1.0.0")


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a UTC offset")
    return parsed


def _print(value: Any) -> None:
    if is_dataclass(value):
        value = asdict(cast(Any, value))
    elif isinstance(value, tuple):
        value = list(value)
    print(stable_json_dumps(value))


__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
