"""SQLite migrations and typed JSON repositories."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel

from enterprise_agent_improvement_lab.contracts.calibration import (
    JudgeCalibrationDataset,
    JudgeCalibrationMetrics,
    JudgeRubric,
    JudgeRubricComparison,
)
from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifact,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.cases import DatasetVersion
from enterprise_agent_improvement_lab.contracts.common import utc_now
from enterprise_agent_improvement_lab.contracts.environments import EnvironmentSnapshot
from enterprise_agent_improvement_lab.contracts.evaluation import EnterpriseEvaluationReport
from enterprise_agent_improvement_lab.contracts.evaluation_environment import StateSnapshot
from enterprise_agent_improvement_lab.contracts.experiments import (
    ActiveCandidatePointer,
    BaselineComparison,
    ExperimentRun,
    PromotionDecision,
    PromotionPolicy,
)
from enterprise_agent_improvement_lab.contracts.failures import (
    EvaluationFailure,
    EvaluationScore,
    FailureCluster,
    HumanAnnotation,
    SamplingEvent,
)
from enterprise_agent_improvement_lab.contracts.governance import (
    EvidenceRef,
    RedactionPolicy,
    RetentionPolicy,
    TenantBoundary,
    apply_governance,
)
from enterprise_agent_improvement_lab.contracts.improvement import (
    ImprovementPlan,
    RootCauseHypothesis,
)
from enterprise_agent_improvement_lab.contracts.lifecycle import (
    CanaryEvaluation,
    CandidateStageTransition,
    PromotionReadiness,
    RollbackEvidence,
    ShadowEvaluation,
    StageEvidence,
)
from enterprise_agent_improvement_lab.contracts.promotion import (
    PromotionProfile,
    RiskAwarePromotionEvaluation,
)
from enterprise_agent_improvement_lab.contracts.sessions import (
    SessionEvaluationResult,
    SessionSummary,
)
from enterprise_agent_improvement_lab.contracts.system import (
    SystemCandidate,
    SystemEvaluationReport,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ExecutionTrace,
    ExecutionTraceSummary,
)
from enterprise_agent_improvement_lab.serialization import model_from_json, model_to_json

ModelT = TypeVar("ModelT", bound=BaseModel)
CURRENT_SCHEMA_VERSION = 1


class RepositoryError(RuntimeError):
    """Raised when a repository operation fails."""


class SQLiteStore:
    """SQLite database with forward-only migrations."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the active SQLite connection."""

        return self._connection

    def migrate(self) -> None:
        """Apply all migrations that are not yet recorded."""

        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL
            )
            """
        )
        applied = {
            row[0] for row in self._connection.execute("SELECT version FROM schema_migrations")
        }
        migrations = {1: ("record store", self._migration_1)}
        for version in sorted(migrations):
            if version in applied:
                continue
            description, migration = migrations[version]
            with self._connection:
                migration()
                self._connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at, description) "
                    "VALUES (?, ?, ?)",
                    (version, utc_now().isoformat(), description),
                )

        current = self.schema_version()
        if current != CURRENT_SCHEMA_VERSION:
            raise RepositoryError(
                f"Database schema version {current} is not supported; expected "
                f"{CURRENT_SCHEMA_VERSION}"
            )

    def schema_version(self) -> int:
        """Return the highest applied migration version."""

        row = self._connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    def _migration_1(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                entity_type TEXT NOT NULL,
                record_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(entity_type, record_id)
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_entity ON records(entity_type)"
        )

    @property
    def datasets(self) -> "DatasetRepository":
        return DatasetRepository(self)

    @property
    def candidate_artifacts(self) -> "CandidateArtifactRepository":
        return CandidateArtifactRepository(self)

    @property
    def artifacts(self) -> "CandidateArtifactRepository":
        """Return the repository for general immutable candidate artifacts."""

        return CandidateArtifactRepository(self)

    @property
    def enterprise_candidates(self) -> "EnterpriseAgentCandidateRepository":
        return EnterpriseAgentCandidateRepository(self)

    @property
    def experiments(self) -> "ExperimentRepository":
        return ExperimentRepository(self)

    @property
    def environment_snapshots(self) -> "EnvironmentSnapshotRepository":
        return EnvironmentSnapshotRepository(self)

    @property
    def state_snapshots(self) -> "StateSnapshotRepository":
        return StateSnapshotRepository(self)

    @property
    def comparisons(self) -> "ComparisonRepository":
        return ComparisonRepository(self)

    @property
    def execution_traces(self) -> "ExecutionTraceRepository":
        return ExecutionTraceRepository(self)

    @property
    def execution_trace_summaries(self) -> "ExecutionTraceSummaryRepository":
        return ExecutionTraceSummaryRepository(self)

    @property
    def sessions(self) -> "SessionRepository":
        return SessionRepository(self)

    @property
    def session_evaluations(self) -> "SessionEvaluationRepository":
        return SessionEvaluationRepository(self)

    @property
    def scores(self) -> "ScoreRepository":
        return ScoreRepository(self)

    @property
    def failures(self) -> "FailureRepository":
        return FailureRepository(self)

    @property
    def failure_clusters(self) -> "FailureClusterRepository":
        return FailureClusterRepository(self)

    @property
    def root_cause_hypotheses(self) -> "RootCauseHypothesisRepository":
        return RootCauseHypothesisRepository(self)

    @property
    def improvement_plans(self) -> "ImprovementPlanRepository":
        return ImprovementPlanRepository(self)

    @property
    def enterprise_evaluation_reports(self) -> "EnterpriseEvaluationReportRepository":
        return EnterpriseEvaluationReportRepository(self)

    @property
    def promotion_profiles(self) -> "PromotionProfileRepository":
        return PromotionProfileRepository(self)

    @property
    def risk_promotion_evaluations(self) -> "RiskAwarePromotionEvaluationRepository":
        return RiskAwarePromotionEvaluationRepository(self)

    @property
    def stage_evidence(self) -> "StageEvidenceRepository":
        return StageEvidenceRepository(self)

    @property
    def stage_transitions(self) -> "CandidateStageTransitionRepository":
        return CandidateStageTransitionRepository(self)

    @property
    def shadow_evaluations(self) -> "ShadowEvaluationRepository":
        return ShadowEvaluationRepository(self)

    @property
    def canary_evaluations(self) -> "CanaryEvaluationRepository":
        return CanaryEvaluationRepository(self)

    @property
    def rollback_evidence(self) -> "RollbackEvidenceRepository":
        return RollbackEvidenceRepository(self)

    @property
    def promotion_readiness(self) -> "PromotionReadinessRepository":
        return PromotionReadinessRepository(self)

    @property
    def evidence_refs(self) -> "EvidenceRefRepository":
        return EvidenceRefRepository(self)

    @property
    def redaction_policies(self) -> "RedactionPolicyRepository":
        return RedactionPolicyRepository(self)

    @property
    def retention_policies(self) -> "RetentionPolicyRepository":
        return RetentionPolicyRepository(self)

    @property
    def tenant_boundaries(self) -> "TenantBoundaryRepository":
        return TenantBoundaryRepository(self)

    @property
    def system_candidates(self) -> "SystemCandidateRepository":
        return SystemCandidateRepository(self)

    @property
    def system_evaluation_reports(self) -> "SystemEvaluationReportRepository":
        return SystemEvaluationReportRepository(self)

    @property
    def annotations(self) -> "AnnotationRepository":
        return AnnotationRepository(self)

    @property
    def sampling_events(self) -> "SamplingEventRepository":
        return SamplingEventRepository(self)

    @property
    def decisions(self) -> "DecisionRepository":
        return DecisionRepository(self)

    @property
    def policies(self) -> "PolicyRepository":
        return PolicyRepository(self)

    @property
    def active_candidate(self) -> "ActiveCandidateRepository":
        return ActiveCandidateRepository(self)

    @property
    def rubrics(self) -> "RubricRepository":
        return RubricRepository(self)

    @property
    def calibration_datasets(self) -> "CalibrationDatasetRepository":
        return CalibrationDatasetRepository(self)

    @property
    def calibration_metrics(self) -> "CalibrationMetricsRepository":
        return CalibrationMetricsRepository(self)

    @property
    def rubric_comparisons(self) -> "RubricComparisonRepository":
        return RubricComparisonRepository(self)

    def close(self) -> None:
        """Close the database connection."""

        self._connection.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


class JsonRepository(Generic[ModelT]):
    """Persist one contract type as canonical JSON."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        entity_type: str,
        model_type: type[ModelT],
        id_field: str,
        governed: bool = False,
    ) -> None:
        self.store = store
        self.entity_type = entity_type
        self.model_type = model_type
        self.id_field = id_field
        self.governed = governed

    def save(
        self,
        model: ModelT,
        *,
        redaction_policy: RedactionPolicy | None = None,
        retention_policy: RetentionPolicy | None = None,
        tenant_boundary: TenantBoundary | None = None,
    ) -> ModelT:
        """Insert or replace one record."""

        if self.governed and redaction_policy is None:
            redaction_policy = RedactionPolicy.default()
        model = cast(
            ModelT,
            apply_governance(
                model,
                redaction_policy=redaction_policy,
                retention_policy=retention_policy,
                tenant_boundary=tenant_boundary,
            ),
        )
        record_id = getattr(model, self.id_field, None)
        if not isinstance(record_id, str) or not record_id:
            raise RepositoryError(f"{self.model_type.__name__} has no valid {self.id_field}")
        payload = model_to_json(model)
        schema_version = getattr(model, "schema_version", "1.0")
        now = utc_now().isoformat()
        try:
            with self.store.connection:
                self.store.connection.execute(
                    """
                    INSERT INTO records(
                        entity_type, record_id, schema_version, payload_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_type, record_id) DO UPDATE SET
                        schema_version = excluded.schema_version,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (self.entity_type, record_id, schema_version, payload, now, now),
                )
        except sqlite3.Error as exc:
            raise RepositoryError(f"Could not save {self.entity_type}/{record_id}: {exc}") from exc
        return model

    def get(self, record_id: str) -> ModelT | None:
        """Load one record by ID."""

        row = self.store.connection.execute(
            "SELECT payload_json FROM records WHERE entity_type = ? AND record_id = ?",
            (self.entity_type, record_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return model_from_json(self.model_type, row[0])
        except ValueError as exc:
            raise RepositoryError(
                f"Stored {self.entity_type}/{record_id} is invalid: {exc}"
            ) from exc

    def list(self) -> list[ModelT]:
        """Load all records in stable ID order."""

        rows = self.store.connection.execute(
            "SELECT payload_json FROM records WHERE entity_type = ? ORDER BY record_id",
            (self.entity_type,),
        ).fetchall()
        try:
            return [model_from_json(self.model_type, row[0]) for row in rows]
        except ValueError as exc:
            raise RepositoryError(
                f"Stored {self.entity_type} contains invalid data: {exc}"
            ) from exc

    def delete(self, record_id: str) -> bool:
        """Delete one record and return whether it existed."""

        with self.store.connection:
            cursor = self.store.connection.execute(
                "DELETE FROM records WHERE entity_type = ? AND record_id = ?",
                (self.entity_type, record_id),
            )
        return cursor.rowcount == 1

    def count(self) -> int:
        """Return the number of records for this entity."""

        row = self.store.connection.execute(
            "SELECT COUNT(*) FROM records WHERE entity_type = ?", (self.entity_type,)
        ).fetchone()
        return int(row[0])

    def purge_expired(
        self,
        policy: RetentionPolicy,
        *,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        """Delete records that have reached a delete retention policy."""

        if policy.action.value != "delete" or policy.legal_hold:
            return ()
        expired: list[str] = []
        for model in self.list():
            timestamp = getattr(model, "created_at", None)
            if timestamp is None:
                timestamp = getattr(model, "captured_at", None)
            if timestamp is not None and policy.is_expired(timestamp, now=now):
                record_id = str(getattr(model, self.id_field))
                expired.append(record_id)
        if not expired:
            return ()
        with self.store.connection:
            self.store.connection.executemany(
                "DELETE FROM records WHERE entity_type = ? AND record_id = ?",
                [(self.entity_type, record_id) for record_id in expired],
            )
        return tuple(expired)


class _ImmutableRepository(JsonRepository[ModelT]):
    """Repository mixin for immutable evidence and decision records."""

    def save(
        self,
        model: ModelT,
        *,
        redaction_policy: RedactionPolicy | None = None,
        retention_policy: RetentionPolicy | None = None,
        tenant_boundary: TenantBoundary | None = None,
    ) -> ModelT:
        if self.governed and redaction_policy is None:
            redaction_policy = RedactionPolicy.default()
        model = cast(
            ModelT,
            apply_governance(
                model,
                redaction_policy=redaction_policy,
                retention_policy=retention_policy,
                tenant_boundary=tenant_boundary,
            ),
        )
        record_id = getattr(model, self.id_field)
        existing = self.get(record_id)
        if existing is not None and existing != model:
            raise RepositoryError(f"{self.entity_type}/{record_id} is immutable")
        return super().save(model)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"{self.entity_type}/{record_id} is immutable")


class DatasetRepository(JsonRepository[DatasetVersion]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store, entity_type="datasets", model_type=DatasetVersion, id_field="dataset_id"
        )


class CandidateArtifactRepository(JsonRepository[CandidateArtifact]):
    """Store immutable general candidate artifacts."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="candidate_artifacts",
            model_type=CandidateArtifact,
            id_field="artifact_id",
        )

    def save(self, model: CandidateArtifact, **governance: Any) -> CandidateArtifact:
        existing = self.get(model.artifact_id)
        if existing is not None and existing != model:
            raise RepositoryError(f"Candidate artifact {model.artifact_id} is immutable")
        return super().save(model, **governance)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Candidate artifact {record_id} is immutable")


class EnterpriseAgentCandidateRepository(JsonRepository[EnterpriseAgentCandidate]):
    """Store immutable candidate content with a mutable lifecycle status."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="enterprise_candidates",
            model_type=EnterpriseAgentCandidate,
            id_field="candidate_id",
        )

    def save(self, model: EnterpriseAgentCandidate, **governance: Any) -> EnterpriseAgentCandidate:
        existing = self.get(model.candidate_id)
        if existing is not None and existing != model:
            existing_content = existing.model_dump(exclude={"status"})
            new_content = model.model_dump(exclude={"status"})
            if existing_content != new_content:
                raise RepositoryError(
                    f"Enterprise candidate {model.candidate_id} content is immutable"
                )
        return super().save(model, **governance)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Enterprise candidate {record_id} is immutable")


class ExperimentRepository(JsonRepository[ExperimentRun]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store, entity_type="experiments", model_type=ExperimentRun, id_field="run_id"
        )

    def save(self, model: ExperimentRun, **governance: Any) -> ExperimentRun:
        """Save a run only after its referenced snapshot is available."""

        snapshot_id = model.manifest.environment_snapshot_ref
        snapshot = model.manifest.environment_snapshot
        if snapshot is not None:
            if snapshot.identity != snapshot_id:
                raise RepositoryError("Run manifest snapshot reference does not match its snapshot")
            self.store.environment_snapshots.save(snapshot)
        elif self.store.environment_snapshots.get(snapshot_id) is None:
            legacy = model.manifest.legacy_environment_snapshot()
            if legacy.identity != snapshot_id:
                raise RepositoryError(
                    "Run manifest references an environment snapshot that is not stored"
                )
            self.store.environment_snapshots.save(legacy)
        return super().save(model, **governance)

    def get(self, record_id: str) -> ExperimentRun | None:
        """Load a run and reattach a stored explicit snapshot when available."""

        result = super().get(record_id)
        if result is None or result.manifest.environment_snapshot is not None:
            return result
        snapshot = self.store.environment_snapshots.get(result.manifest.environment_snapshot_ref)
        if snapshot is None:
            return result
        if snapshot.identity == result.manifest.legacy_environment_snapshot().identity:
            return result
        manifest = result.manifest.model_copy(update={"environment_snapshot": snapshot})
        return result.model_copy(update={"manifest": manifest})


class EnvironmentSnapshotRepository(JsonRepository[EnvironmentSnapshot]):
    """Store immutable reproducibility snapshots."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="environment_snapshots",
            model_type=EnvironmentSnapshot,
            id_field="snapshot_id",
            governed=True,
        )

    def save(self, model: EnvironmentSnapshot, **governance: Any) -> EnvironmentSnapshot:
        existing = self.get(model.identity)
        if existing is not None:
            if existing.identity != model.identity:
                raise RepositoryError(f"Environment snapshot {model.identity} is immutable")
            return existing
        return super().save(model, **governance)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Environment snapshot {record_id} is immutable")


class ComparisonRepository(JsonRepository[BaselineComparison]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="comparisons",
            model_type=BaselineComparison,
            id_field="comparison_id",
        )


class ExecutionTraceRepository(_ImmutableRepository[ExecutionTrace]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="execution_traces",
            model_type=ExecutionTrace,
            id_field="execution_id",
            governed=True,
        )


class ExecutionTraceSummaryRepository(_ImmutableRepository[ExecutionTraceSummary]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="execution_trace_summaries",
            model_type=ExecutionTraceSummary,
            id_field="execution_id",
            governed=True,
        )


class SessionRepository(JsonRepository[SessionSummary]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store, entity_type="sessions", model_type=SessionSummary, id_field="session_id"
        )


class SessionEvaluationRepository(JsonRepository[SessionEvaluationResult]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="session_evaluations",
            model_type=SessionEvaluationResult,
            id_field="session_id",
        )


class ScoreRepository(JsonRepository[EvaluationScore]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="scores",
            model_type=EvaluationScore,
            id_field="score_id",
            governed=True,
        )


class FailureRepository(JsonRepository[EvaluationFailure]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="failures",
            model_type=EvaluationFailure,
            id_field="failure_id",
            governed=True,
        )


class FailureClusterRepository(JsonRepository[FailureCluster]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="failure_clusters",
            model_type=FailureCluster,
            id_field="cluster_id",
        )


class RootCauseHypothesisRepository(_ImmutableRepository[RootCauseHypothesis]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="root_cause_hypotheses",
            model_type=RootCauseHypothesis,
            id_field="hypothesis_id",
        )


class ImprovementPlanRepository(_ImmutableRepository[ImprovementPlan]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="improvement_plans",
            model_type=ImprovementPlan,
            id_field="plan_id",
        )


class EnterpriseEvaluationReportRepository(_ImmutableRepository[EnterpriseEvaluationReport]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="enterprise_evaluation_reports",
            model_type=EnterpriseEvaluationReport,
            id_field="run_id",
            governed=True,
        )


class StateSnapshotRepository(_ImmutableRepository[StateSnapshot]):
    """Store immutable state evidence with default redaction."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="state_snapshots",
            model_type=StateSnapshot,
            id_field="snapshot_id",
            governed=True,
        )


class StageEvidenceRepository(_ImmutableRepository[StageEvidence]):
    """Store immutable evidence for a lifecycle stage."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="stage_evidence",
            model_type=StageEvidence,
            id_field="evidence_id",
            governed=True,
        )


class CandidateStageTransitionRepository(_ImmutableRepository[CandidateStageTransition]):
    """Store immutable candidate stage transitions."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="candidate_stage_transitions",
            model_type=CandidateStageTransition,
            id_field="transition_id",
            governed=True,
        )


class ShadowEvaluationRepository(_ImmutableRepository[ShadowEvaluation]):
    """Store immutable shadow evaluation records."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="shadow_evaluations",
            model_type=ShadowEvaluation,
            id_field="evaluation_id",
            governed=True,
        )


class CanaryEvaluationRepository(_ImmutableRepository[CanaryEvaluation]):
    """Store immutable bounded canary evaluation records."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="canary_evaluations",
            model_type=CanaryEvaluation,
            id_field="evaluation_id",
            governed=True,
        )


class RollbackEvidenceRepository(_ImmutableRepository[RollbackEvidence]):
    """Store immutable rollback evidence."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="rollback_evidence",
            model_type=RollbackEvidence,
            id_field="rollback_id",
            governed=True,
        )


class PromotionReadinessRepository(_ImmutableRepository[PromotionReadiness]):
    """Store immutable computed promotion readiness."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="promotion_readiness",
            model_type=PromotionReadiness,
            id_field="readiness_id",
            governed=True,
        )


class EvidenceRefRepository(_ImmutableRepository[EvidenceRef]):
    """Store immutable evidence references."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="evidence_refs",
            model_type=EvidenceRef,
            id_field="evidence_id",
            governed=True,
        )


class RedactionPolicyRepository(_ImmutableRepository[RedactionPolicy]):
    """Store immutable redaction policy versions."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="redaction_policies",
            model_type=RedactionPolicy,
            id_field="policy_id",
        )


class RetentionPolicyRepository(_ImmutableRepository[RetentionPolicy]):
    """Store immutable retention policy versions."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="retention_policies",
            model_type=RetentionPolicy,
            id_field="policy_id",
        )


class TenantBoundaryRepository(_ImmutableRepository[TenantBoundary]):
    """Store immutable tenant boundary definitions."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="tenant_boundaries",
            model_type=TenantBoundary,
            id_field="boundary_id",
        )


class PromotionProfileRepository(_ImmutableRepository[PromotionProfile]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="promotion_profiles",
            model_type=PromotionProfile,
            id_field="profile_id",
        )


class RiskAwarePromotionEvaluationRepository(_ImmutableRepository[RiskAwarePromotionEvaluation]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="risk_promotion_evaluations",
            model_type=RiskAwarePromotionEvaluation,
            id_field="evaluation_id",
        )


class SystemCandidateRepository(_ImmutableRepository[SystemCandidate]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="system_candidates",
            model_type=SystemCandidate,
            id_field="system_candidate_id",
        )


class SystemEvaluationReportRepository(_ImmutableRepository[SystemEvaluationReport]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="system_evaluation_reports",
            model_type=SystemEvaluationReport,
            id_field="report_id",
        )


class AnnotationRepository(JsonRepository[HumanAnnotation]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="annotations",
            model_type=HumanAnnotation,
            id_field="annotation_id",
        )

    def save(self, model: HumanAnnotation, **governance: Any) -> HumanAnnotation:
        if self.get(model.annotation_id) is not None:
            raise RepositoryError(
                f"Annotation {model.annotation_id} already exists; "
                "annotation history is append-only"
            )
        return super().save(model, **governance)


class SamplingEventRepository(JsonRepository[SamplingEvent]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="sampling_events",
            model_type=SamplingEvent,
            id_field="event_id",
        )


class DecisionRepository(JsonRepository[PromotionDecision]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="decisions",
            model_type=PromotionDecision,
            id_field="decision_id",
        )

    def save(self, model: PromotionDecision, **governance: Any) -> PromotionDecision:
        if self.get(model.decision_id) is not None:
            raise RepositoryError(
                f"Promotion decision {model.decision_id} already exists; decisions are immutable"
            )
        return super().save(model, **governance)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Promotion decision {record_id} is immutable")


class PolicyRepository(JsonRepository[PromotionPolicy]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store, entity_type="policies", model_type=PromotionPolicy, id_field="policy_id"
        )


class ActiveCandidateRepository(JsonRepository[ActiveCandidatePointer]):
    """Store the one mutable pointer to the active candidate."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="active_candidate",
            model_type=ActiveCandidatePointer,
            id_field="pointer_id",
        )


class RubricRepository(JsonRepository[JudgeRubric]):
    """Store immutable judge rubric versions."""

    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="judge_rubrics",
            model_type=JudgeRubric,
            id_field="record_id",
        )

    def save(self, model: JudgeRubric, **governance: Any) -> JudgeRubric:
        record_id = f"{model.rubric_id}:{model.version}"
        if self.get(record_id) is not None:
            raise RepositoryError(
                f"Judge rubric {record_id} already exists; rubric versions are immutable"
            )
        if model.record_id != record_id:
            model = model.model_copy(update={"record_id": record_id})
        return super().save(model, **governance)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Judge rubric {record_id} is immutable")


class CalibrationDatasetRepository(JsonRepository[JudgeCalibrationDataset]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="judge_calibration_datasets",
            model_type=JudgeCalibrationDataset,
            id_field="record_id",
        )

    def save(self, model: JudgeCalibrationDataset, **governance: Any) -> JudgeCalibrationDataset:
        record_id = f"{model.dataset_id}:{model.version}:{model.rubric_id}"
        if model.record_id != record_id:
            model = model.model_copy(update={"record_id": record_id})
        if self.get(record_id) is not None:
            raise RepositoryError(
                f"Calibration dataset {record_id} already exists; datasets are immutable"
            )
        return super().save(model, **governance)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Calibration dataset {record_id} is immutable")


class CalibrationMetricsRepository(JsonRepository[JudgeCalibrationMetrics]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="judge_calibration_metrics",
            model_type=JudgeCalibrationMetrics,
            id_field="metrics_id",
        )

    def save(self, model: JudgeCalibrationMetrics, **governance: Any) -> JudgeCalibrationMetrics:
        metrics_id = f"{model.dataset_id}:{model.rubric_id}:{model.rubric_version}"
        if model.metrics_id != metrics_id:
            model = model.model_copy(update={"metrics_id": metrics_id})
        if self.get(metrics_id) is not None:
            raise RepositoryError(
                f"Calibration metrics {metrics_id} already exists; metrics are immutable"
            )
        return super().save(model, **governance)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Calibration metrics {record_id} are immutable")


class RubricComparisonRepository(JsonRepository[JudgeRubricComparison]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="judge_rubric_comparisons",
            model_type=JudgeRubricComparison,
            id_field="comparison_id",
        )

    def save(self, model: JudgeRubricComparison, **governance: Any) -> JudgeRubricComparison:
        if self.get(model.comparison_id) is not None:
            raise RepositoryError(
                f"Rubric comparison {model.comparison_id} already exists; comparisons are immutable"
            )
        return super().save(model, **governance)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Rubric comparison {record_id} is immutable")
