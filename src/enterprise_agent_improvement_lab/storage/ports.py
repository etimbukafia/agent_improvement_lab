"""Provider-neutral storage ports for Lab services."""

from __future__ import annotations

from typing import Any, Generic, Protocol, TypeVar

from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifact,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.cases import DatasetVersion
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
from enterprise_agent_improvement_lab.contracts.sessions import (
    SessionEvaluationResult,
    SessionSummary,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ExecutionTrace,
    ExecutionTraceSummary,
)

ModelT = TypeVar("ModelT")


class RepositoryPort(Protocol, Generic[ModelT]):
    """Minimal persistence port implemented by a typed repository."""

    def save(
        self,
        model: ModelT,
        *,
        redaction_policy: RedactionPolicy | None = None,
        retention_policy: RetentionPolicy | None = None,
        tenant_boundary: TenantBoundary | None = None,
    ) -> ModelT:
        """Save one model."""

    def get(self, record_id: str) -> ModelT | None:
        """Load one model by its stable ID."""

    def list(self) -> list[ModelT]:
        """List models in deterministic order."""

    def delete(self, record_id: str) -> bool:
        """Delete one model when the contract allows deletion."""

    def count(self) -> int:
        """Return the number of stored models."""


class DatasetStore(RepositoryPort[DatasetVersion], Protocol):
    """Persistence port for immutable datasets."""


class TraceStore(RepositoryPort[ExecutionTrace], Protocol):
    """Persistence port for immutable execution traces."""


class TraceSummaryStore(RepositoryPort[ExecutionTraceSummary], Protocol):
    """Persistence port for safe trace summaries."""


class ExperimentStore(RepositoryPort[ExperimentRun], Protocol):
    """Persistence port for experiment runs."""


class CandidateStore(RepositoryPort[EnterpriseAgentCandidate], Protocol):
    """Persistence port for immutable enterprise candidate versions."""


class FailureStore(RepositoryPort[EvaluationFailure], Protocol):
    """Persistence port for normalized failures."""


class EvaluationStore(RepositoryPort[EvaluationScore], Protocol):
    """Persistence port for evaluator scores."""


class EvaluationReportStore(RepositoryPort[EnterpriseEvaluationReport], Protocol):
    """Persistence port for complete evaluation reports."""


class PromotionStore(Protocol):
    """Storage boundary required by promotion services."""

    @property
    def enterprise_candidates(self) -> CandidateStore:
        """Return candidate storage."""

    @property
    def decisions(self) -> RepositoryPort[PromotionDecision]:
        """Return decision storage."""

    @property
    def policies(self) -> RepositoryPort[PromotionPolicy]:
        """Return policy storage."""

    @property
    def comparisons(self) -> RepositoryPort[BaselineComparison]:
        """Return comparison storage."""

    @property
    def active_candidate(self) -> RepositoryPort[ActiveCandidatePointer]:
        """Return the active-candidate pointer storage."""


class ArtifactStore(RepositoryPort[CandidateArtifact], Protocol):
    """Persistence port for immutable candidate artifacts."""


class EnvironmentSnapshotStore(RepositoryPort[EnvironmentSnapshot], Protocol):
    """Persistence port for immutable environment snapshots."""


class AnnotationStore(Protocol):
    """Storage boundary required by human annotation services."""

    def save(self, model: Any, **governance: Any) -> Any:
        """Save an annotation."""

    def get(self, record_id: str) -> Any | None:
        """Load an annotation."""

    def list(self) -> list[Any]:
        """List annotations."""


class LifecycleStore(Protocol):
    """Storage boundary for append-only lifecycle evidence."""

    stage_evidence: RepositoryPort[StageEvidence]
    stage_transitions: RepositoryPort[CandidateStageTransition]
    shadow_evaluations: RepositoryPort[ShadowEvaluation]
    canary_evaluations: RepositoryPort[CanaryEvaluation]
    rollback_evidence: RepositoryPort[RollbackEvidence]
    promotion_readiness: RepositoryPort[PromotionReadiness]

    def save(self, model: Any, **governance: Any) -> Any:
        """Save one lifecycle record when a single-record adapter is used."""


class GovernanceStore(Protocol):
    """Storage boundary for governance policy records."""

    redaction_policies: RepositoryPort[RedactionPolicy]
    retention_policies: RepositoryPort[RetentionPolicy]
    tenant_boundaries: RepositoryPort[TenantBoundary]
    evidence_refs: RepositoryPort[EvidenceRef]


class LabStore(Protocol):
    """Composite port used by services that write several evidence types."""

    @property
    def datasets(self) -> DatasetStore:
        """Return dataset storage."""

    @property
    def candidate_artifacts(self) -> ArtifactStore:
        """Return artifact storage."""

    @property
    def enterprise_candidates(self) -> CandidateStore:
        """Return candidate storage."""

    @property
    def experiments(self) -> ExperimentStore:
        """Return experiment storage."""

    @property
    def environment_snapshots(self) -> EnvironmentSnapshotStore:
        """Return environment snapshot storage."""

    @property
    def comparisons(self) -> RepositoryPort[BaselineComparison]:
        """Return comparison storage."""

    @property
    def execution_traces(self) -> TraceStore:
        """Return execution trace storage."""

    @property
    def execution_trace_summaries(self) -> TraceSummaryStore:
        """Return trace summary storage."""

    @property
    def state_snapshots(self) -> RepositoryPort[StateSnapshot]:
        """Return state snapshot storage."""

    @property
    def scores(self) -> EvaluationStore:
        """Return score storage."""

    @property
    def failures(self) -> FailureStore:
        """Return failure storage."""

    @property
    def failure_clusters(self) -> RepositoryPort[FailureCluster]:
        """Return failure-cluster storage."""

    @property
    def annotations(self) -> RepositoryPort[HumanAnnotation]:
        """Return annotation storage."""

    @property
    def enterprise_evaluation_reports(self) -> EvaluationReportStore:
        """Return evaluation-report storage."""

    @property
    def sessions(self) -> RepositoryPort[SessionSummary]:
        """Return session storage."""

    @property
    def session_evaluations(self) -> RepositoryPort[SessionEvaluationResult]:
        """Return session evaluation storage."""

    @property
    def sampling_events(self) -> RepositoryPort[SamplingEvent]:
        """Return sampling-event storage."""

    @property
    def decisions(self) -> RepositoryPort[PromotionDecision]:
        """Return decision storage."""

    @property
    def policies(self) -> RepositoryPort[PromotionPolicy]:
        """Return policy storage."""

    @property
    def active_candidate(self) -> RepositoryPort[ActiveCandidatePointer]:
        """Return active-candidate storage."""

    @property
    def root_cause_hypotheses(self) -> RepositoryPort[RootCauseHypothesis]:
        """Return root-cause hypothesis storage."""

    @property
    def improvement_plans(self) -> RepositoryPort[ImprovementPlan]:
        """Return improvement-plan storage."""

    @property
    def shadow_evaluations(self) -> RepositoryPort[ShadowEvaluation]:
        """Return shadow-evaluation storage."""

    @property
    def canary_evaluations(self) -> RepositoryPort[CanaryEvaluation]:
        """Return canary-evaluation storage."""

    @property
    def promotion_readiness(self) -> RepositoryPort[PromotionReadiness]:
        """Return promotion-readiness storage."""


__all__ = [
    "AnnotationStore",
    "ArtifactStore",
    "CandidateStore",
    "DatasetStore",
    "EnvironmentSnapshotStore",
    "EvaluationReportStore",
    "EvaluationStore",
    "ExperimentStore",
    "FailureStore",
    "GovernanceStore",
    "LabStore",
    "LifecycleStore",
    "PromotionStore",
    "RepositoryPort",
    "TraceStore",
    "TraceSummaryStore",
]
