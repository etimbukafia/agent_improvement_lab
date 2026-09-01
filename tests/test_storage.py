from pathlib import Path

import pytest

from enterprise_agent_improvement_lab.contracts.experiments import (
    PromotionDecision,
    PromotionOutcome,
    PromotionPolicy,
)
from enterprise_agent_improvement_lab.contracts.sessions import SessionSummary
from enterprise_agent_improvement_lab.contracts.traces import (
    ExecutionTrace,
    ExecutionTraceSummary,
    MessageEvent,
)
from enterprise_agent_improvement_lab.storage import RepositoryError, SQLiteStore


def test_sqlite_migration_is_forward_only_and_idempotent(tmp_path: Path):
    database = tmp_path / "lab.sqlite3"
    store = SQLiteStore(database)
    assert store.schema_version() == 1
    store.close()

    reopened = SQLiteStore(database)
    assert reopened.schema_version() == 1
    reopened.close()


def test_repositories_round_trip_versioned_records(
    tmp_path: Path, dataset, candidate, artifact, experiment
):
    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.datasets.save(dataset)
        store.candidate_artifacts.save(artifact)
        store.enterprise_candidates.save(candidate)
        store.experiments.save(experiment)

        assert store.datasets.get(dataset.dataset_id) == dataset
        assert store.candidate_artifacts.get(artifact.artifact_id) == artifact
        assert store.enterprise_candidates.get(candidate.candidate_id) == candidate
        assert store.experiments.get(experiment.run_id) == experiment
        assert store.experiments.list() == [experiment]


def test_candidate_artifact_repository_rejects_replacement(tmp_path: Path, artifact):
    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.candidate_artifacts.save(artifact)
        changed = artifact.model_copy(update={"content": "A different prompt."})
        with pytest.raises(RepositoryError, match="immutable"):
            store.candidate_artifacts.save(changed)


def test_execution_trace_summary_repository_stores_safe_summary(tmp_path: Path, created_at):
    summary = ExecutionTraceSummary(
        execution_id="execution-1",
        agent_id="agent-1",
        agent_version="1.0.0",
        candidate_id="candidate-1",
        trigger_kind="message",
        case_id="case-1",
        started_at=created_at,
        total_tokens=12,
    )
    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.execution_trace_summaries.save(summary)
        assert store.execution_trace_summaries.get("execution-1") == summary


def test_execution_trace_session_and_decision_records_round_trip(tmp_path: Path, created_at):
    event = MessageEvent(
        event_id="message-1",
        sequence=0,
        timestamp=created_at,
        message_id="message-1",
        role="assistant",
        message_summary="4",
    )
    trace = ExecutionTrace(
        execution_id="execution-1",
        agent_id="agent-1",
        agent_version="1.0.0",
        candidate_id="candidate-1",
        case_id="case-1",
        session_id="session-1",
        started_at=created_at,
        ended_at=created_at,
        events=(event,),
    )
    session = SessionSummary(
        session_id="session-1",
        trace_ids=("execution-1",),
        trace_count=1,
        started_at=created_at,
        ended_at=created_at,
    )
    policy = PromotionPolicy(policy_id="policy-1", version="1.0.0")
    decision = PromotionDecision(
        decision_id="decision-1",
        candidate_id="candidate-1",
        comparison_id="comparison-1",
        policy_id=policy.policy_id,
        outcome=PromotionOutcome.APPROVED,
        reviewer="reviewer-1",
        decided_at=created_at,
        reason="All gates passed.",
    )

    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.execution_traces.save(trace)
        store.sessions.save(session)
        store.policies.save(policy)
        store.decisions.save(decision)

        assert store.execution_traces.get(trace.execution_id) == trace
        assert store.sessions.get(session.session_id) == session
        assert store.policies.get(policy.policy_id) == policy
        assert store.decisions.get(decision.decision_id) == decision
