"""SQLite migrations and typed JSON repositories."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from agent_improvement_lab.contracts.calibration import (
    JudgeCalibrationDataset,
    JudgeCalibrationMetrics,
    JudgeRubric,
    JudgeRubricComparison,
)
from agent_improvement_lab.contracts.candidates import AgentCandidate, PromptArtifact
from agent_improvement_lab.contracts.cases import DatasetVersion
from agent_improvement_lab.contracts.common import utc_now
from agent_improvement_lab.contracts.experiments import (
    ActiveCandidatePointer,
    BaselineComparison,
    ExperimentRun,
    PromotionDecision,
    PromotionPolicy,
)
from agent_improvement_lab.contracts.failures import (
    EvaluationFailure,
    EvaluationScore,
    FailureCluster,
    HumanAnnotation,
    SamplingEvent,
)
from agent_improvement_lab.contracts.sessions import SessionEvaluationResult, SessionSummary
from agent_improvement_lab.contracts.traces import AgentTrace, TraceSummary
from agent_improvement_lab.serialization import model_from_json, model_to_json

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
    def candidates(self) -> "CandidateRepository":
        return CandidateRepository(self)

    @property
    def prompt_artifacts(self) -> "PromptArtifactRepository":
        return PromptArtifactRepository(self)

    @property
    def experiments(self) -> "ExperimentRepository":
        return ExperimentRepository(self)

    @property
    def comparisons(self) -> "ComparisonRepository":
        return ComparisonRepository(self)

    @property
    def traces(self) -> "TraceRepository":
        return TraceRepository(self)

    @property
    def trace_summaries(self) -> "TraceSummaryRepository":
        return TraceSummaryRepository(self)

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
    ) -> None:
        self.store = store
        self.entity_type = entity_type
        self.model_type = model_type
        self.id_field = id_field

    def save(self, model: ModelT) -> ModelT:
        """Insert or replace one record."""

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


class DatasetRepository(JsonRepository[DatasetVersion]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store, entity_type="datasets", model_type=DatasetVersion, id_field="dataset_id"
        )


class CandidateRepository(JsonRepository[AgentCandidate]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store, entity_type="candidates", model_type=AgentCandidate, id_field="candidate_id"
        )


class PromptArtifactRepository(JsonRepository[PromptArtifact]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="prompt_artifacts",
            model_type=PromptArtifact,
            id_field="artifact_id",
        )

    def save(self, model: PromptArtifact) -> PromptArtifact:
        existing = self.get(model.artifact_id)
        if existing is not None and existing != model:
            raise RepositoryError(f"Prompt artifact {model.artifact_id} is immutable")
        return super().save(model)


class ExperimentRepository(JsonRepository[ExperimentRun]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store, entity_type="experiments", model_type=ExperimentRun, id_field="run_id"
        )


class ComparisonRepository(JsonRepository[BaselineComparison]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="comparisons",
            model_type=BaselineComparison,
            id_field="comparison_id",
        )


class TraceRepository(JsonRepository[AgentTrace]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(store, entity_type="traces", model_type=AgentTrace, id_field="trace_id")


class TraceSummaryRepository(JsonRepository[TraceSummary]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="trace_summaries",
            model_type=TraceSummary,
            id_field="trace_id",
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
            store, entity_type="scores", model_type=EvaluationScore, id_field="score_id"
        )


class FailureRepository(JsonRepository[EvaluationFailure]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store, entity_type="failures", model_type=EvaluationFailure, id_field="failure_id"
        )


class FailureClusterRepository(JsonRepository[FailureCluster]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="failure_clusters",
            model_type=FailureCluster,
            id_field="cluster_id",
        )


class AnnotationRepository(JsonRepository[HumanAnnotation]):
    def __init__(self, store: SQLiteStore) -> None:
        super().__init__(
            store,
            entity_type="annotations",
            model_type=HumanAnnotation,
            id_field="annotation_id",
        )

    def save(self, model: HumanAnnotation) -> HumanAnnotation:
        if self.get(model.annotation_id) is not None:
            raise RepositoryError(
                f"Annotation {model.annotation_id} already exists; "
                "annotation history is append-only"
            )
        return super().save(model)


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

    def save(self, model: PromotionDecision) -> PromotionDecision:
        if self.get(model.decision_id) is not None:
            raise RepositoryError(
                f"Promotion decision {model.decision_id} already exists; decisions are immutable"
            )
        return super().save(model)

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

    def save(self, model: JudgeRubric) -> JudgeRubric:
        record_id = f"{model.rubric_id}:{model.version}"
        if self.get(record_id) is not None:
            raise RepositoryError(
                f"Judge rubric {record_id} already exists; rubric versions are immutable"
            )
        if model.record_id != record_id:
            model = model.model_copy(update={"record_id": record_id})
        return super().save(model)

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

    def save(self, model: JudgeCalibrationDataset) -> JudgeCalibrationDataset:
        record_id = f"{model.dataset_id}:{model.version}:{model.rubric_id}"
        if model.record_id != record_id:
            model = model.model_copy(update={"record_id": record_id})
        if self.get(record_id) is not None:
            raise RepositoryError(
                f"Calibration dataset {record_id} already exists; datasets are immutable"
            )
        return super().save(model)

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

    def save(self, model: JudgeCalibrationMetrics) -> JudgeCalibrationMetrics:
        metrics_id = f"{model.dataset_id}:{model.rubric_id}:{model.rubric_version}"
        if model.metrics_id != metrics_id:
            model = model.model_copy(update={"metrics_id": metrics_id})
        if self.get(metrics_id) is not None:
            raise RepositoryError(
                f"Calibration metrics {metrics_id} already exists; metrics are immutable"
            )
        return super().save(model)

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

    def save(self, model: JudgeRubricComparison) -> JudgeRubricComparison:
        if self.get(model.comparison_id) is not None:
            raise RepositoryError(
                f"Rubric comparison {model.comparison_id} already exists; comparisons are immutable"
            )
        return super().save(model)

    def delete(self, record_id: str) -> bool:
        raise RepositoryError(f"Rubric comparison {record_id} is immutable")
