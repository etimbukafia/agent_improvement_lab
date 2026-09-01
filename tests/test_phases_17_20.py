"""Behavior tests for lifecycle, governance, storage ports, and runners."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from enterprise_agent_improvement_lab.comparison import compare_enterprise_metrics
from enterprise_agent_improvement_lab.contracts.candidates import CandidateStatus
from enterprise_agent_improvement_lab.contracts.evaluation_environment import StateSnapshot
from enterprise_agent_improvement_lab.contracts.experiments import (
    EnterpriseComparisonDimension,
    EnterpriseComparisonMetric,
    RunManifest,
)
from enterprise_agent_improvement_lab.contracts.governance import RetentionPolicy, TenantBoundary
from enterprise_agent_improvement_lab.contracts.lifecycle import (
    CanaryEvaluation,
    CandidateLifecycleService,
    CandidateStage,
    LifecycleTransitionError,
    PromotionReadiness,
    RollbackEvidence,
    ShadowEvaluation,
    StageEvidence,
    StageGate,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ExecutionTrace,
    MessageEvent,
    ToolCallEvent,
    ToolCallOutcome,
    TriggerInfo,
)
from enterprise_agent_improvement_lab.runners import (
    LocalEvaluationRunner,
    PydanticEvalsRunner,
    ReplayRunner,
    ShadowEvaluationRunner,
)
from enterprise_agent_improvement_lab.storage import SQLiteStore

UTC = timezone.utc
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _manifest(candidate: Any, dataset: Any, runtime: Any) -> RunManifest:
    return RunManifest(
        run_id=f"run-{runtime.name}",
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        candidate_id=candidate.candidate_id,
        runtime_name=runtime.name,
        runtime_version=runtime.version,
        provider="test",
        model="test-model",
        seed=7,
        created_at=NOW,
    )


def _trace(candidate: Any, case: Any, *, execution_id: str = "execution-1") -> ExecutionTrace:
    event = MessageEvent(
        event_id=f"{execution_id}:message",
        sequence=0,
        timestamp=NOW,
        message_id=f"{execution_id}:message",
        role="assistant",
        message_summary="safe output",
    )
    return ExecutionTrace(
        execution_id=execution_id,
        agent_id=candidate.agent_id,
        agent_version=candidate.agent_version or candidate.version,
        candidate_id=candidate.candidate_id,
        case_id=case.case_id,
        trigger=TriggerInfo(kind="test"),
        started_at=NOW,
        ended_at=NOW,
        events=(event,),
    )


def test_invalid_lifecycle_transitions_fail_and_approval_stays_separate(candidate) -> None:
    service = CandidateLifecycleService()

    with pytest.raises(LifecycleTransitionError):
        service.advance_candidate(candidate, CandidateStage.CANARY)

    with pytest.raises(ValidationError, match="Human approval is required"):
        service.transition(
            candidate,
            transition_id="transition-1",
            to_stage=CandidateStage.OFFLINE_EVALUATED,
            evidence_refs=("evidence:offline",),
            rationale="offline evidence is complete",
            human_approval_required=True,
        )

    readiness = service.readiness(
        candidate_id=candidate.candidate_id,
        target_stage=CandidateStage.APPROVED,
        gates=(
            StageGate(
                gate_id="canary-passed",
                stage=CandidateStage.APPROVED,
                passed=True,
                summary="bounded canary passed",
                evidence_refs=("canary:1",),
            ),
        ),
        evidence_refs=("canary:1",),
    )
    assert isinstance(readiness, PromotionReadiness)
    assert readiness.eligible is True
    assert readiness.human_approval_required is True

    with pytest.raises(ValidationError, match="production side effects"):
        ShadowEvaluation(
            evaluation_id="shadow-1",
            candidate_id=candidate.candidate_id,
            run_id="run-shadow",
            environment_snapshot_id="environment-1",
            side_effects_observed=True,
            passed=False,
            summary="invalid shadow evidence",
        )


def test_stage_gates_must_match_their_readiness_target(candidate) -> None:
    with pytest.raises(ValidationError, match="Stage gate stage"):
        PromotionReadiness(
            readiness_id="readiness-1",
            candidate_id=candidate.candidate_id,
            target_stage=CandidateStage.APPROVED,
            gates=(
                StageGate(
                    gate_id="offline-gate",
                    stage=CandidateStage.OFFLINE_EVALUATED,
                    passed=True,
                    summary="offline evaluation passed",
                ),
            ),
            eligible=True,
            evidence_refs=("evidence:offline",),
        )


def test_shadow_evaluation_uses_a_non_mutating_environment(candidate, dataset) -> None:
    runtime = _RecordingRuntime(candidate, dataset.cases[0])
    runner = ShadowEvaluationRunner(runtime, evaluators=())

    result = runner.run_sync(dataset, candidate, _manifest(candidate, dataset, runtime))

    assert result.report.traces[0].candidate_id == candidate.candidate_id
    assert runtime.execution_modes == ["shadow"]
    assert runtime.production_side_effect_flags == [False]
    assert result.cases[0].final_state is not None
    assert result.cases[0].final_state.state["shadow_checked"] is True


def test_canary_and_rollback_evidence_keep_candidate_and_environment_links(
    tmp_path, candidate
) -> None:
    service = CandidateLifecycleService()
    snapshot_id = "environment-test-1"
    canary = service.record_canary_evaluation(
        CanaryEvaluation(
            evaluation_id="canary-1",
            candidate_id=candidate.candidate_id,
            run_id="run-canary",
            environment_snapshot_id=snapshot_id,
            scope_id="bounded-orders",
            max_executions=10,
            executed_count=2,
            passed=True,
            summary="bounded canary passed",
            evidence_refs=("trace:canary",),
        )
    )
    assert canary.candidate_id == candidate.candidate_id
    assert canary.environment_snapshot_id == snapshot_id

    approved = candidate.model_copy(update={"status": CandidateStatus.APPROVED})
    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        lifecycle = CandidateLifecycleService(store)
        lifecycle.record_canary_evaluation(canary)
        rollback = lifecycle.rollback(
            approved,
            rollback_id="rollback-1",
            reason="canary evidence requires retirement",
            evidence_refs=("incident:1",),
            restored_candidate_id="candidate-baseline",
            environment_snapshot_id=snapshot_id,
        )
        stored_canary = store.canary_evaluations.get("canary-1")
        stored_rollback = store.rollback_evidence.get("rollback-1")

    assert stored_canary == canary
    assert rollback.evidence_refs == ("incident:1",)
    assert isinstance(stored_rollback, RollbackEvidence)
    assert stored_rollback.environment_snapshot_id == snapshot_id


def test_governed_persistence_redacts_sensitive_fields_before_storage(
    tmp_path, candidate, case
) -> None:
    trace = ExecutionTrace(
        execution_id="sensitive-execution",
        agent_id=candidate.agent_id,
        agent_version=candidate.version,
        candidate_id=candidate.candidate_id,
        case_id=case.case_id,
        started_at=NOW,
        ended_at=NOW,
        events=(
            ToolCallEvent(
                event_id="tool-1",
                sequence=0,
                timestamp=NOW,
                call_id="call-1",
                name="orders.read",
                outcome=ToolCallOutcome.SUCCESS,
                arguments={"api_key": "raw-api-key", "order_id": "order-1"},
                metadata={
                    "password": "raw-password",
                    "secret_key": "raw-secret-key",
                    "region": "test",
                },
            ),
        ),
        metadata={"authorization": "Bearer raw-token", "source": "test"},
    )
    state = StateSnapshot(
        snapshot_id="sensitive-state",
        captured_at=NOW,
        state={"api_key": "raw-api-key", "status": "open"},
    )

    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.execution_traces.save(trace)
        store.state_snapshots.save(state)
        trace_row = store.connection.execute(
            "SELECT payload_json FROM records WHERE entity_type = ? AND record_id = ?",
            ("execution_traces", trace.execution_id),
        ).fetchone()[0]
        state_row = store.connection.execute(
            "SELECT payload_json FROM records WHERE entity_type = ? AND record_id = ?",
            ("state_snapshots", state.snapshot_id),
        ).fetchone()[0]

    assert "raw-api-key" not in trace_row
    assert "raw-password" not in trace_row
    assert "raw-secret-key" not in trace_row
    assert "raw-token" not in trace_row
    assert "raw-api-key" not in state_row


def test_tenant_boundary_rejects_cross_tenant_evidence(tmp_path, candidate, case) -> None:
    trace = _trace(candidate, case, execution_id="tenant-one")
    one = trace.model_copy(update={"tenant_id": "tenant-1"})
    two = trace.model_copy(update={"execution_id": "tenant-two", "tenant_id": "tenant-2"})
    boundary = TenantBoundary(tenant_id="tenant-1")

    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.execution_traces.save(one, tenant_boundary=boundary)
        with pytest.raises(ValueError, match="tenant boundary"):
            store.execution_traces.save(two, tenant_boundary=boundary)


def test_retention_policy_is_enforced_at_the_storage_boundary(tmp_path) -> None:
    snapshot = StateSnapshot(
        snapshot_id="old-state",
        captured_at=NOW,
        state={"status": "closed"},
    )
    policy = RetentionPolicy(policy_id="short", retention_days=1)
    purge_at = NOW + timedelta(days=2)

    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.state_snapshots.save(snapshot)
        deleted = store.state_snapshots.purge_expired(policy, now=purge_at)

        assert deleted == ("old-state",)
        assert store.state_snapshots.get("old-state") is None


def test_core_runner_and_lifecycle_services_accept_storage_port_shapes(candidate, dataset) -> None:
    memory_store = _MemoryLifecycleStore()
    lifecycle = CandidateLifecycleService(memory_store)
    evidence = StageEvidence(
        evidence_id="offline-1",
        candidate_id=candidate.candidate_id,
        stage=CandidateStage.OFFLINE_EVALUATED,
        environment_snapshot_id="environment-1",
        safe_summary="offline evaluation passed",
        passed=True,
        evidence_refs=("report:1",),
    )

    assert lifecycle.record_stage_evidence(evidence) == evidence
    assert memory_store.stage_evidence.get("offline-1") == evidence

    runtime = _RecordingRuntime(candidate, dataset.cases[0])
    local = LocalEvaluationRunner(runtime, evaluators=())
    result = local.run_sync(dataset, candidate, _manifest(candidate, dataset, runtime))
    assert result.report.candidate_id == candidate.candidate_id


def test_pydantic_evals_maps_through_the_lab_runner_contract(candidate, dataset) -> None:
    pytest.importorskip("pydantic_evals")
    runner = PydanticEvalsRunner(task=lambda _value: "4", evaluators=())
    manifest = _manifest(candidate, dataset, _NamedRuntime("pydantic-evals"))

    pydantic_dataset = runner.to_pydantic_dataset(dataset)
    result = runner.run_sync(dataset, candidate, manifest)

    assert pydantic_dataset.name == dataset.dataset_id
    assert result.report.candidate_id == candidate.candidate_id
    assert result.report.traces[0].execution_id.startswith("pydantic-evals:")


def test_replay_runner_reuses_stored_evidence_deterministically(candidate, dataset) -> None:
    stored = _trace(candidate, dataset.cases[0], execution_id="stored-execution")
    runner = ReplayRunner((stored,), evaluators=())
    manifest = _manifest(candidate, dataset, _NamedRuntime("replay"))

    first = runner.run_sync(dataset, candidate, manifest)
    second = runner.run_sync(dataset, candidate, manifest)

    assert first.report.model_dump_json() == second.report.model_dump_json()
    assert first.traces[0].execution_id == "stored-execution"


def test_shadow_and_local_runners_return_the_same_lab_report_contract(candidate, dataset) -> None:
    local_runtime = _RecordingRuntime(candidate, dataset.cases[0])
    shadow_runtime = _RecordingRuntime(candidate, dataset.cases[0])
    local = LocalEvaluationRunner(local_runtime, evaluators=())
    shadow = ShadowEvaluationRunner(shadow_runtime, evaluators=())
    local_manifest = _manifest(candidate, dataset, local_runtime)
    shadow_manifest = _manifest(candidate, dataset, shadow_runtime)

    local_result = local.run_sync(dataset, candidate, local_manifest)
    shadow_result = shadow.run_sync(dataset, candidate, shadow_manifest)

    assert type(local_result.report) is type(shadow_result.report)
    assert local_result.report.case_results == shadow_result.report.case_results
    assert local_result.report.traces == shadow_result.report.traces


def test_comparison_accepts_lab_runner_outputs_without_provider_runner_types() -> None:
    baseline = EnterpriseComparisonMetric(
        metric_id="quality",
        dimension=EnterpriseComparisonDimension.BUSINESS_OUTCOMES,
        evaluator_family="quality",
        baseline_value=0.8,
        candidate_value=0.8,
        evidence_refs=("score:1",),
    )
    candidate = baseline.model_copy(update={"candidate_value": 0.9})

    comparison = compare_enterprise_metrics(
        (baseline,),
        (candidate,),
        baseline_run_id="baseline-1",
        candidate_run_id="candidate-1",
    )

    assert comparison.verdict.value == "improved"


@dataclass
class _RecordingRuntime:
    candidate: Any
    case: Any
    name: str = "shared-runtime"
    version: str = "1.0.0"
    execution_modes: list[str] = field(default_factory=list)
    production_side_effect_flags: list[bool] = field(default_factory=list)

    async def execute(self, case: Any, candidate: Any, environment: Any) -> ExecutionTrace:
        del case, candidate
        self.execution_modes.append(str(getattr(environment, "execution_mode", "local")))
        self.production_side_effect_flags.append(
            bool(getattr(environment, "production_side_effects", False))
        )
        environment.state["shadow_checked"] = True
        return _trace(self.candidate, self.case)


@dataclass(frozen=True)
class _NamedRuntime:
    name: str
    version: str = "1.0.0"


class _MemoryRepository:
    def __init__(self, key: str) -> None:
        self.key = key
        self.values: dict[str, Any] = {}

    def save(self, model: Any, **_governance: Any) -> Any:
        self.values[str(getattr(model, self.key))] = model
        return model

    def get(self, record_id: str) -> Any | None:
        return self.values.get(record_id)

    def list(self) -> list[Any]:
        return [self.values[key] for key in sorted(self.values)]


@dataclass
class _MemoryLifecycleStore:
    stage_evidence: _MemoryRepository = field(
        default_factory=lambda: _MemoryRepository("evidence_id")
    )
    stage_transitions: _MemoryRepository = field(
        default_factory=lambda: _MemoryRepository("transition_id")
    )
    shadow_evaluations: _MemoryRepository = field(
        default_factory=lambda: _MemoryRepository("evaluation_id")
    )
    canary_evaluations: _MemoryRepository = field(
        default_factory=lambda: _MemoryRepository("evaluation_id")
    )
    rollback_evidence: _MemoryRepository = field(
        default_factory=lambda: _MemoryRepository("rollback_id")
    )
    promotion_readiness: _MemoryRepository = field(
        default_factory=lambda: _MemoryRepository("readiness_id")
    )
