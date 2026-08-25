"""Run the complete deterministic calculator improvement cycle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from agent_improvement_lab.candidates import create_candidate
from agent_improvement_lab.comparison import ComparisonRunner
from agent_improvement_lab.contracts.candidates import (
    AgentCandidate,
    CandidateGenerationRequest,
    CandidateScope,
    PromptArtifact,
    PromptArtifactKind,
)
from agent_improvement_lab.contracts.experiments import (
    ExperimentRun,
    PromotionOutcome,
    PromotionPolicy,
    RunManifest,
    RunStatus,
)
from agent_improvement_lab.contracts.failures import AnnotationStatus, Severity
from agent_improvement_lab.contracts.sessions import SessionSummary
from agent_improvement_lab.dashboard import summarize_trace
from agent_improvement_lab.datasets import load_dataset
from agent_improvement_lab.failure_mining import cluster_failures, normalize_failures
from agent_improvement_lab.promotion import PromotionService
from agent_improvement_lab.review import AnnotationService
from agent_improvement_lab.runner import EvaluationRunResult, PydanticEvalsRunner
from agent_improvement_lab.serialization import write_json
from agent_improvement_lab.storage import SQLiteStore
from examples.calculator_agent.generator import CalculatorCandidateGenerator
from examples.calculator_agent.runtime import CalculatorRuntime

CREATED_AT = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def run_cycle(output_dir: Path) -> dict[str, object]:
    """Run baseline, review, candidate, comparison, and approval steps."""

    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = Path(__file__).with_name("dataset.json")
    dataset = load_dataset(dataset_path)
    baseline_artifact = PromptArtifact(
        artifact_id="calculator-prompt-v1",
        name="calculator-system-prompt",
        version="1.0.0",
        kind=PromptArtifactKind.SYSTEM_PROMPT,
        content="Answer arithmetic expressions directly.",
        created_at=CREATED_AT,
    )
    baseline = AgentCandidate(
        candidate_id="calculator-baseline",
        name="calculator-baseline",
        version="1.0.0",
        prompt_artifact_ids=(baseline_artifact.artifact_id,),
        rationale="Initial deterministic calculator behavior.",
        created_at=CREATED_AT,
        metadata={"calculator_mode": "direct"},
    )
    baseline_manifest = _manifest("calculator-baseline-run", baseline)
    runner = PydanticEvalsRunner(CalculatorRuntime())
    baseline_result = runner.run_sync(dataset, baseline, baseline_manifest)

    with SQLiteStore(":memory:") as store:
        store.datasets.save(dataset)
        store.prompt_artifacts.save(baseline_artifact)
        store.candidates.save(baseline)
        _persist_run(store, baseline_result, baseline_manifest)
        failures = normalize_failures(
            baseline_result.report,
            dataset,
            traces=baseline_result.traces,
            created_at=CREATED_AT,
        )
        clusters = cluster_failures(failures, created_at=CREATED_AT)
        for failure in failures:
            store.failures.save(failure)
        for cluster in clusters:
            store.failure_clusters.save(cluster)

        target = next(
            failure for failure in failures if failure.evaluator_id == "tool.selection_accuracy"
        )
        annotations = AnnotationService(store.annotations)
        unreviewed = annotations.create_unreviewed(
            annotation_id="annotation-calculator-tool-selection",
            target_id=target.failure_id,
            target_type="failure",
            reviewer="calculator-sme",
            reviewed_at=CREATED_AT,
            expected_behavior="Use the calculator tool before reporting the result.",
            label_confidence=1.0,
            severity=Severity.MEDIUM,
        )
        confirmed = annotations.transition(
            unreviewed,
            annotation_id="annotation-calculator-tool-selection-confirmed",
            status=AnnotationStatus.CONFIRMED,
            reviewer="calculator-sme",
            reviewed_at=CREATED_AT,
        )

        request = CandidateGenerationRequest(
            candidate_id="calculator-candidate",
            name="calculator-tool-candidate",
            version="1.1.0",
            parent_candidate=baseline,
            current_artifacts=(baseline_artifact,),
            selected_failures=(target,),
            confirmed_annotations=(confirmed,),
            scope=CandidateScope(
                scope_id="calculator-prompt-scope",
                allowed_artifact_ids=(baseline_artifact.artifact_id,),
            ),
            constraints=("Do not change datasets, labels, evaluators, or promotion rules.",),
            generator_id="calculator-generator",
            created_at=CREATED_AT,
        )
        built = create_candidate(request, CalculatorCandidateGenerator(), created_at=CREATED_AT)
        candidate = built.candidate.model_copy(update={"metadata": {"calculator_mode": "tool"}})
        for artifact in built.artifacts:
            store.prompt_artifacts.save(artifact)
        store.candidates.save(candidate)

        comparison_baseline_manifest = _manifest("calculator-baseline-comparison", baseline)
        comparison_candidate_manifest = _manifest("calculator-candidate-comparison", candidate)
        comparison_result = ComparisonRunner(runner).compare(
            dataset,
            baseline,
            candidate,
            comparison_baseline_manifest,
            comparison_candidate_manifest,
            target_failures=(target,),
            repeat=1,
            created_at=CREATED_AT,
        )
        _persist_run(
            store,
            comparison_result.baseline_result,
            comparison_result.baseline_run.manifest,
        )
        _persist_run(
            store,
            comparison_result.candidate_result,
            comparison_result.candidate_run.manifest,
        )
        if comparison_result.holdout_baseline_result and comparison_result.holdout_baseline_run:
            _persist_run(
                store,
                comparison_result.holdout_baseline_result,
                comparison_result.holdout_baseline_run.manifest,
            )
        if comparison_result.holdout_candidate_result and comparison_result.holdout_candidate_run:
            _persist_run(
                store,
                comparison_result.holdout_candidate_result,
                comparison_result.holdout_candidate_run.manifest,
            )
        store.comparisons.save(comparison_result.comparison)

        policy = PromotionPolicy(policy_id="calculator-promotion", version="1.0.0")
        decision = PromotionService(store, policy).decide(
            decision_id="decision-calculator-candidate-approved",
            candidate_id=candidate.candidate_id,
            comparison=comparison_result.comparison,
            outcome=PromotionOutcome.APPROVED,
            reviewer="calculator-owner",
            reason=(
                "Targeted tool-selection failures improved and holdout performance did not decline."
            ),
            decided_at=CREATED_AT,
        )

        write_json(output_dir / "baseline-report.json", baseline_result.report)
        write_json(output_dir / "candidate-report.json", comparison_result.candidate_result.report)
        write_json(output_dir / "comparison.json", comparison_result.comparison)
        write_json(output_dir / "baseline-failures.json", list(failures))
        write_json(output_dir / "failure-clusters.json", list(clusters))
        write_json(output_dir / "candidate.json", candidate)
        write_json(output_dir / "candidate-artifacts.json", list(built.artifacts))
        write_json(output_dir / "promotion-decision.json", decision)
        summary: dict[str, object] = {
            "baseline_run_id": baseline_result.report.run_id,
            "candidate_run_id": comparison_result.candidate_result.report.run_id,
            "comparison_id": comparison_result.comparison.comparison_id,
            "promotion_decision_id": decision.decision_id,
            "active_candidate_id": candidate.candidate_id,
        }
        write_json(output_dir / "cycle-summary.json", summary)
        return summary


def _manifest(run_id: str, candidate: AgentCandidate) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        dataset_id="calculator-demo",
        dataset_version="1.0.0",
        candidate_id=candidate.candidate_id,
        prompt_artifact_ids=candidate.prompt_artifact_ids,
        toolset=("calculator",),
        runtime_name=CalculatorRuntime.name,
        runtime_version=CalculatorRuntime.version,
        provider="deterministic",
        model="none",
        seed=0,
        created_at=CREATED_AT,
        metadata={"example": "calculator-agent"},
    )


def _persist_run(store: SQLiteStore, result: EvaluationRunResult, manifest: RunManifest) -> None:
    session_ids = tuple(
        dict.fromkeys(trace.session_id for trace in result.traces if trace.session_id is not None)
    )
    store.experiments.save(
        ExperimentRun(
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
    )
    score_ids_by_trace = {
        item.trace_id: item.score_ids
        for item in result.report.case_results
        if item.trace_id is not None
    }
    for trace in result.traces:
        store.traces.save(trace)
        store.trace_summaries.save(
            summarize_trace(trace, score_ids_by_trace.get(trace.trace_id, ()))
        )
    for score in result.report.scores:
        store.scores.save(score)
    session_ids_set = {trace.session_id for trace in result.traces if trace.session_id}
    for session_id in session_ids_set:
        traces = sorted(
            (trace for trace in result.traces if trace.session_id == session_id),
            key=lambda trace: trace.trace_id,
        )
        store.sessions.save(
            SessionSummary(
                session_id=str(session_id),
                trace_ids=tuple(trace.trace_id for trace in traces),
                started_at=min(trace.started_at for trace in traces),
                ended_at=max(trace.ended_at for trace in traces if trace.ended_at is not None),
                total_latency_ms=sum(summarize_trace(trace).total_latency_ms for trace in traces),
                total_tokens=sum(summarize_trace(trace).total_tokens for trace in traces),
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).with_name("reports"),
    )
    args = parser.parse_args()
    summary = run_cycle(args.output_dir)
    print(summary)


if __name__ == "__main__":
    main()
