from datetime import datetime, timezone
from hashlib import sha256

import pytest
from pydantic import ValidationError

from enterprise_agent_improvement_lab.comparison import compare_enterprise_metrics
from enterprise_agent_improvement_lab.contracts.environments import EnvironmentSnapshot
from enterprise_agent_improvement_lab.contracts.experiments import (
    EnterpriseComparisonDimension,
    EnterpriseComparisonMetric,
    ExperimentRun,
    RunManifest,
    RunStatus,
)
from enterprise_agent_improvement_lab.serialization import model_to_json
from enterprise_agent_improvement_lab.storage import SQLiteStore

UTC = timezone.utc
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _snapshot(**updates: object) -> EnvironmentSnapshot:
    values: dict[str, object] = {
        "agent_registry_version": "agent-registry-7",
        "prompt_registry_version": "prompt-registry-3",
        "skill_registry_version": "skill-registry-2",
        "tool_registry_version": "tool-registry-4",
        "policy_registry_version": "policy-registry-5",
        "agent_definition_hash": sha256(b"agent-definition").hexdigest(),
        "tool_hashes": {"orders.read@1.0.0": sha256(b"tool").hexdigest()},
        "policy_hashes": {"orders-policy@1.0.0": sha256(b"policy").hexdigest()},
        "runtime_name": "harness",
        "runtime_version": "0.1.0",
        "provider": "deterministic",
        "provider_version": "1.0.0",
        "model": "deterministic-model",
        "model_parameters": {"temperature": 0.0, "max_tokens": 20},
        "feature_flags": {"safe_mode": True},
        "tenant_profile": "tenant-test",
        "fixture_version": "fixture-3",
        "external_service_stub_versions": {"orders": "2.0.0"},
        "environment_name": "test",
        "clock_mode": "fixed",
        "seed": 7,
        "captured_at": NOW,
    }
    values.update(updates)
    return EnvironmentSnapshot(**values)


def _manifest(
    candidate_id: str,
    *,
    snapshot: EnvironmentSnapshot | None = None,
    run_id: str | None = None,
) -> RunManifest:
    return RunManifest(
        run_id=run_id or f"run-{candidate_id}",
        dataset_id="demo",
        dataset_version="1.0.0",
        candidate_id=candidate_id,
        runtime_name="harness",
        runtime_version="0.1.0",
        provider="deterministic",
        model="deterministic-model",
        seed=7,
        environment_snapshot=snapshot,
        created_at=NOW,
    )


def test_equivalent_environment_state_has_one_deterministic_identity() -> None:
    first = _snapshot()
    second = _snapshot(captured_at=datetime(2026, 8, 24, tzinfo=UTC))

    assert first.identity == second.identity
    assert first.checksum == second.checksum


def test_registry_changes_produce_a_different_snapshot_identity() -> None:
    changed = _snapshot(tool_registry_version="tool-registry-5")

    assert changed.identity != _snapshot().identity


def test_policy_changes_produce_a_different_snapshot_identity() -> None:
    changed = _snapshot(policy_hashes={"orders-policy@1.0.0": sha256(b"changed").hexdigest()})

    assert changed.identity != _snapshot().identity


def test_model_configuration_changes_produce_a_different_snapshot_identity() -> None:
    changed = _snapshot(model_parameters={"temperature": 0.0, "max_tokens": 40})

    assert changed.identity != _snapshot().identity
    assert (
        dict((setting.key, setting.value) for setting in _snapshot().model_parameters)["max_tokens"]
        == 20
    )


def test_runtime_version_changes_produce_a_different_snapshot_identity() -> None:
    changed = _snapshot(runtime_version="0.2.0")

    assert changed.identity != _snapshot().identity


def test_snapshot_copy_recomputes_identity() -> None:
    changed = _snapshot().model_copy(update={"runtime_version": "0.2.0"})

    assert changed.identity != _snapshot().identity
    assert changed.runtime_version == "0.2.0"


def test_snapshot_persistence_removes_secret_bearing_settings(tmp_path) -> None:
    snapshot = _snapshot(
        model_parameters={"temperature": 0.0, "api_key": "raw-secret"},
        metadata={"password": "raw-password", "safe_label": "test"},
    )

    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.environment_snapshots.save(snapshot)
        payload = store.connection.execute(
            "SELECT payload_json FROM records WHERE entity_type = ? AND record_id = ?",
            ("environment_snapshots", snapshot.identity),
        ).fetchone()[0]
        restored = store.environment_snapshots.get(snapshot.identity)

    assert restored == snapshot
    assert "raw-secret" not in payload
    assert "raw-password" not in payload
    assert "api_key" not in payload
    assert "password" not in payload
    assert "safe_label" in payload


def test_run_manifest_references_and_persists_the_exact_snapshot(tmp_path) -> None:
    snapshot = _snapshot()
    manifest = _manifest("candidate-1", snapshot=snapshot)
    run = ExperimentRun(
        run_id=manifest.run_id,
        experiment_id="experiment-1",
        manifest=manifest,
        status=RunStatus.COMPLETED,
        started_at=NOW,
        ended_at=NOW,
    )

    with SQLiteStore(tmp_path / "lab.sqlite3") as store:
        store.experiments.save(run)
        stored = store.experiments.get(run.run_id)

        assert store.environment_snapshots.get(snapshot.identity) == snapshot

    assert stored is not None
    assert stored.manifest.environment_snapshot_ref == snapshot.identity
    assert stored.manifest.environment_snapshot == snapshot
    assert '"environment_snapshot_id"' in model_to_json(manifest)
    assert '"agent_registry_version"' not in model_to_json(manifest)


def test_comparison_rejects_incompatible_environment_snapshots() -> None:
    baseline_snapshot = _snapshot()
    candidate_snapshot = _snapshot(model_parameters={"temperature": 0.8, "max_tokens": 20})

    metrics = (
        EnterpriseComparisonMetric(
            metric_id="quality",
            dimension=EnterpriseComparisonDimension.BUSINESS_OUTCOMES,
            evaluator_family="quality",
            baseline_value=1.0,
            candidate_value=1.0,
            evidence_refs=("evidence-1",),
        ),
    )

    comparison = compare_enterprise_metrics(
        metrics,
        metrics,
        baseline_snapshot=baseline_snapshot,
        candidate_snapshot=candidate_snapshot,
    )

    assert comparison.environment_compatible is False
    assert comparison.verdict.value == "rejected"
    assert "environment_incompatible" in comparison.regressions


def test_invalid_snapshot_checksum_is_rejected() -> None:
    with pytest.raises(ValidationError, match="snapshot_sha256"):
        _snapshot(snapshot_sha256="0" * 64)
