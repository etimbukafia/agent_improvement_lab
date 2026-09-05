"""Run the complete deterministic enterprise calculator improvement cycle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from enterprise_agent_improvement_lab.comparison import compare_enterprise_reports
from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifact,
    CandidateArtifactKind,
    ChangeKind,
    EnterpriseAgentCandidate,
    ImprovementScope,
)
from enterprise_agent_improvement_lab.contracts.environments import (
    EnvironmentSnapshot,
    SnapshotComponentHash,
    SnapshotSetting,
)
from enterprise_agent_improvement_lab.contracts.experiments import (
    ExperimentRun,
    PromotionOutcome,
    PromotionPolicy,
    RunManifest,
    RunStatus,
)
from enterprise_agent_improvement_lab.contracts.failures import AnnotationStatus, Severity
from enterprise_agent_improvement_lab.contracts.sessions import SessionSummary
from enterprise_agent_improvement_lab.contracts.traces import summarize_execution_trace
from enterprise_agent_improvement_lab.datasets import load_dataset
from enterprise_agent_improvement_lab.enterprise_runner import (
    EnterpriseEvaluationRunner,
    EnterpriseEvaluationRunResult,
)
from enterprise_agent_improvement_lab.evaluators import (
    ProtectedArgumentIntegrity,
    ToolArgumentAccuracy,
    ToolArgumentConstraintMatch,
    ToolSelectionAccuracy,
    default_enterprise_evaluators,
)
from enterprise_agent_improvement_lab.failure_mining import cluster_failures, normalize_failures
from enterprise_agent_improvement_lab.promotion import PromotionService
from enterprise_agent_improvement_lab.review import AnnotationService
from enterprise_agent_improvement_lab.serialization import write_json
from enterprise_agent_improvement_lab.storage import SQLiteStore
from examples.calculator_agent.generator import CalculatorCandidateGenerator
from examples.calculator_agent.runtime import CalculatorRuntime

CREATED_AT = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def run_cycle(output_dir: Path) -> dict[str, object]:
    """Run baseline, review, candidate, comparison, and approval steps."""

    output_dir.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(Path(__file__).with_name("dataset.json"))
    baseline_artifact = CandidateArtifact(
        artifact_id="calculator-prompt-v1",
        name="calculator-system-prompt",
        version="1.0.0",
        kind=CandidateArtifactKind.SYSTEM_PROMPT,
        content="Answer arithmetic expressions directly.",
        registry_reference="prompt:calculator-prompt@1.0.0",
        created_at=CREATED_AT,
    )
    baseline = EnterpriseAgentCandidate(
        candidate_id="calculator-baseline",
        agent_id="calculator-agent",
        name="calculator-baseline",
        version="1.0.0",
        artifacts=(baseline_artifact.to_reference(),),
        prompt_ref=baseline_artifact.to_reference(),
        tools=("calculator",),
        tool_bindings=("calculator-binding@1.0.0",),
        rationale="Initial deterministic calculator behavior.",
        created_at=CREATED_AT,
        metadata={"calculator_mode": "direct"},
    )
    baseline_manifest = _manifest(
        "calculator-baseline-run", baseline, prompt_artifact=baseline_artifact
    )
    evaluators = (
        *default_enterprise_evaluators(),
        ToolSelectionAccuracy(),
        ToolArgumentAccuracy(),
        ToolArgumentConstraintMatch(),
        ProtectedArgumentIntegrity(),
    )
    runner = EnterpriseEvaluationRunner(CalculatorRuntime(), evaluators=evaluators)
    baseline_result = runner.run_sync(dataset, baseline, baseline_manifest)

    with SQLiteStore(":memory:") as store:
        store.datasets.save(dataset)
        store.candidate_artifacts.save(baseline_artifact)
        store.enterprise_candidates.save(baseline)
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
        del confirmed

        scope = ImprovementScope(
            scope_id="calculator-prompt-scope",
            allowed_change_kinds=(ChangeKind.PROMPT_CHANGE,),
            allowed_agents=(baseline.agent_id,),
            allowed_artifact_ids=(baseline_artifact.artifact_id,),
        )
        built = CalculatorCandidateGenerator().build(
            baseline,
            scope,
            base_artifact=baseline_artifact,
            source_failure_ids=(target.failure_id,),
            created_at=CREATED_AT,
        )
        candidate = built.candidate.model_copy(update={"metadata": {"calculator_mode": "tool"}})
        for artifact in built.artifacts:
            store.candidate_artifacts.save(artifact)
        store.enterprise_candidates.save(candidate)

        candidate_prompt_artifact = next(
            artifact
            for artifact in built.artifacts
            if artifact.kind is CandidateArtifactKind.SYSTEM_PROMPT
        )
        candidate_manifest = _manifest(
            "calculator-candidate-run", candidate, prompt_artifact=candidate_prompt_artifact
        )
        candidate_result = runner.run_sync(dataset, candidate, candidate_manifest)
        comparison = compare_enterprise_reports(
            baseline_result.report,
            candidate_result.report,
            baseline_snapshot=baseline_manifest.environment_snapshot,
            candidate_snapshot=candidate_manifest.environment_snapshot,
            baseline_candidate=baseline,
            candidate_candidate=candidate,
            baseline_manifest=baseline_manifest,
            candidate_manifest=candidate_manifest,
            target_failure_ids=(target.failure_id,),
            created_at=CREATED_AT,
        )
        _persist_run(store, candidate_result, candidate_manifest)
        store.comparisons.save(comparison)

        policy = PromotionPolicy(policy_id="calculator-promotion", version="1.0.0")
        decision = PromotionService(store, policy).decide(
            decision_id="decision-calculator-candidate-approved",
            candidate_id=candidate.candidate_id,
            comparison=comparison,
            outcome=PromotionOutcome.APPROVED,
            reviewer="calculator-owner",
            reason="Targeted tool-selection failures improved without a comparison regression.",
            decided_at=CREATED_AT,
        )

        write_json(output_dir / "baseline-report.json", baseline_result.report)
        write_json(output_dir / "candidate-report.json", candidate_result.report)
        write_json(output_dir / "comparison.json", comparison)
        write_json(output_dir / "baseline-failures.json", list(failures))
        write_json(output_dir / "failure-clusters.json", list(clusters))
        write_json(output_dir / "candidate.json", candidate)
        write_json(output_dir / "candidate-artifacts.json", list(built.artifacts))
        write_json(output_dir / "promotion-decision.json", decision)
        summary: dict[str, object] = {
            "baseline_run_id": baseline_result.report.run_id,
            "candidate_run_id": candidate_result.report.run_id,
            "comparison_id": comparison.comparison_id,
            "promotion_decision_id": decision.decision_id,
            "active_candidate_id": candidate.candidate_id,
        }
        write_json(output_dir / "cycle-summary.json", summary)
        return summary


def _manifest(
    run_id: str,
    candidate: EnterpriseAgentCandidate,
    *,
    prompt_artifact: CandidateArtifact,
) -> RunManifest:
    snapshot = EnvironmentSnapshot(
        agent_registry_version="calculator-registry-1",
        prompt_registry_version="calculator-prompt-registry-1",
        skill_registry_version="calculator-skill-registry-1",
        tool_registry_version="calculator-tool-registry-1",
        policy_registry_version="calculator-policy-registry-1",
        agent_definition_hash=sha256(b"calculator-agent-definition").hexdigest(),
        prompt_hashes=(
            SnapshotComponentHash(
                component_id=prompt_artifact.artifact_id,
                version=prompt_artifact.version,
                sha256=sha256(prompt_artifact.content.encode("utf-8")).hexdigest(),
            ),
        ),
        tool_hashes=(
            SnapshotComponentHash(
                component_id="calculator",
                version="1.0.0",
                sha256=sha256(b"calculator-tool").hexdigest(),
            ),
        ),
        runtime_name=CalculatorRuntime.name,
        runtime_version=CalculatorRuntime.version,
        provider="deterministic",
        model="none",
        model_parameters=(SnapshotSetting(key="temperature", value=0.0),),
        environment_name="calculator-example",
        clock_mode="fixed",
        seed=0,
        captured_at=CREATED_AT,
    )
    return RunManifest(
        run_id=run_id,
        dataset_id="calculator-demo",
        dataset_version="1.0.0",
        candidate_id=candidate.candidate_id,
        candidate_artifact_ids=candidate.artifact_ids,
        toolset=candidate.tools,
        runtime_name=CalculatorRuntime.name,
        runtime_version=CalculatorRuntime.version,
        provider="deterministic",
        model="none",
        seed=0,
        environment_snapshot=snapshot,
        prompt_ref=(
            candidate.prompt_ref.registry_reference
            if candidate.prompt_ref is not None
            and candidate.prompt_ref.registry_reference is not None
            else (
                f"prompt:{prompt_artifact.artifact_id}@{prompt_artifact.version}"
                if candidate.prompt_ref is not None
                else None
            )
        ),
        skill_refs=(),
        tool_refs=tuple(f"tool:{tool}@1.0.0" for tool in candidate.tools),
        created_at=CREATED_AT,
        metadata={"example": "calculator-agent"},
    )


def _persist_run(
    store: SQLiteStore,
    result: EnterpriseEvaluationRunResult,
    manifest: RunManifest,
) -> None:
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
    store.enterprise_evaluation_reports.save(result.report)
    score_ids_by_trace = {
        item.trace_id: item.score_ids
        for item in result.report.case_results
        if item.trace_id is not None
    }
    for trace in result.traces:
        store.execution_traces.save(trace)
        store.execution_trace_summaries.save(
            summarize_execution_trace(trace, score_ids_by_trace.get(trace.trace_id, ()))
        )
    for score in result.report.scores:
        store.scores.save(score)
    for failure in result.report.failures:
        store.failures.save(failure)
    for session_id in session_ids:
        traces = [trace for trace in result.traces if trace.session_id == session_id]
        store.sessions.save(
            SessionSummary(
                session_id=session_id,
                trace_ids=tuple(trace.trace_id for trace in traces),
                started_at=min(trace.started_at for trace in traces),
                ended_at=max(
                    (trace.ended_at for trace in traces if trace.ended_at is not None),
                    default=None,
                ),
                total_latency_ms=sum(
                    summarize_execution_trace(trace).total_latency_ms for trace in traces
                ),
                total_tokens=sum(summarize_execution_trace(trace).total_tokens for trace in traces),
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
    print(run_cycle(args.output_dir))


if __name__ == "__main__":
    main()
