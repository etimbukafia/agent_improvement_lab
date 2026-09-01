"""Read-only query services for a Lab dashboard or another client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from enterprise_agent_improvement_lab.contracts.candidates import EnterpriseAgentCandidate
from enterprise_agent_improvement_lab.contracts.evaluation import EnterpriseEvaluationReport
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
)
from enterprise_agent_improvement_lab.contracts.improvement import (
    ImprovementPlan,
    RootCauseHypothesis,
)
from enterprise_agent_improvement_lab.contracts.lifecycle import (
    CanaryEvaluation,
    PromotionReadiness,
    ShadowEvaluation,
)
from enterprise_agent_improvement_lab.contracts.sessions import (
    SessionEvaluationResult,
    SessionSummary,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    ExecutionTrace,
    ExecutionTraceSummary,
    ToolCallEvent,
)
from enterprise_agent_improvement_lab.contracts.traces import (
    summarize_execution_trace as build_execution_trace_summary,
)

if TYPE_CHECKING:
    from enterprise_agent_improvement_lab.storage.ports import LabStore


class QueryError(LookupError):
    """Raised when a requested dashboard record does not exist."""


@dataclass(frozen=True)
class ToolCallView:
    """One tool call in the trace navigation view."""

    event: ToolCallEvent
    node: str


@dataclass(frozen=True)
class TraceView:
    """Trace details with tool calls, scores, and evaluator explanations."""

    trace: ExecutionTrace | None
    summary: ExecutionTraceSummary | None
    tools: tuple[ToolCallView, ...]
    scores: tuple[EvaluationScore, ...]
    failures: tuple[EvaluationFailure, ...]


@dataclass(frozen=True)
class ExecutionTraceView:
    """An enterprise trace and its safe aggregate summary."""

    trace: ExecutionTrace | None
    summary: ExecutionTraceSummary | None


@dataclass(frozen=True)
class SessionView:
    """Session details with its ordered trace views."""

    session: SessionSummary
    evaluation: SessionEvaluationResult | None
    traces: tuple[TraceView, ...]


@dataclass(frozen=True)
class EvaluatorView:
    """All stored evidence for one evaluator."""

    evaluator_id: str
    scores: tuple[EvaluationScore, ...]
    failures: tuple[EvaluationFailure, ...]


@dataclass(frozen=True)
class FailureView:
    """A failure with its cluster and annotation history."""

    failure: EvaluationFailure
    cluster: FailureCluster | None
    annotations: tuple[HumanAnnotation, ...]


@dataclass(frozen=True)
class ExperimentView:
    """A run with its traces, scores, and normalized failures."""

    experiment: ExperimentRun
    report: EnterpriseEvaluationReport | None
    traces: tuple[ExecutionTrace, ...]
    scores: tuple[EvaluationScore, ...]
    failures: tuple[EvaluationFailure, ...]


@dataclass(frozen=True)
class PromotionView:
    """Promotion evidence and the current active-candidate pointer."""

    decisions: tuple[PromotionDecision, ...]
    comparisons: tuple[BaselineComparison, ...]
    policies: tuple[PromotionPolicy, ...]
    active_candidate: ActiveCandidatePointer | None


@dataclass(frozen=True)
class CandidateLineageView:
    """A candidate and its direct parent chain."""

    candidate: EnterpriseAgentCandidate
    ancestors: tuple[EnterpriseAgentCandidate, ...]


@dataclass(frozen=True)
class LifecycleEvidenceView:
    """Stored shadow, canary, and promotion-readiness evidence."""

    shadow: tuple[ShadowEvaluation, ...]
    canary: tuple[CanaryEvaluation, ...]
    readiness: tuple[PromotionReadiness, ...]


def summarize_execution_trace(
    trace: ExecutionTrace,
    score_ids: tuple[str, ...] = (),
) -> ExecutionTraceSummary:
    """Create a safe summary for an enterprise execution trace."""

    return build_execution_trace_summary(trace, score_ids)


class DashboardQueryService:
    """Provide stable read-only views for a dashboard or CLI client."""

    def __init__(self, store: LabStore) -> None:
        self.store = store

    def session(self, session_id: str) -> SessionView:
        """Return a session and its ordered traces."""

        session = self.store.sessions.get(session_id)
        if session is None:
            raise QueryError(f"Session {session_id!r} was not found")
        evaluation = self.store.session_evaluations.get(session_id)
        traces = tuple(self.trace(trace_id) for trace_id in session.trace_ids)
        return SessionView(session=session, evaluation=evaluation, traces=traces)

    def trace(self, trace_id: str) -> TraceView:
        """Return one trace and its node, tool, and evaluator detail."""

        trace = self.store.execution_traces.get(trace_id)
        summary = self.store.execution_trace_summaries.get(trace_id)
        if trace is None and summary is None:
            raise QueryError(f"Trace {trace_id!r} was not found")
        if summary is None and trace is not None:
            score_ids = tuple(
                score.score_id
                for score in self.store.scores.list()
                if trace_id in score.evidence_refs
            )
            summary = summarize_execution_trace(trace, score_ids)
        scores = tuple(
            sorted(
                (
                    score
                    for score in self.store.scores.list()
                    if score.score_id in (summary.evaluation_score_ids if summary else ())
                ),
                key=lambda score: score.score_id,
            )
        )
        failures = tuple(
            sorted(
                (failure for failure in self.store.failures.list() if failure.trace_id == trace_id),
                key=lambda failure: failure.failure_id,
            )
        )
        tools: list[ToolCallView] = []
        if trace is not None:
            node = _node_name(trace)
            for event in trace.ordered_events():
                if isinstance(event, ToolCallEvent):
                    tools.append(ToolCallView(event=event, node=node))
        return TraceView(
            trace=trace,
            summary=summary,
            tools=tuple(tools),
            scores=scores,
            failures=failures,
        )

    def execution_trace(self, execution_id: str) -> ExecutionTraceView:
        """Return one enterprise execution trace and its safe summary."""

        trace = self.store.execution_traces.get(execution_id)
        summary = self.store.execution_trace_summaries.get(execution_id)
        if trace is None and summary is None:
            raise QueryError(f"Execution trace {execution_id!r} was not found")
        if summary is None and trace is not None:
            summary = summarize_execution_trace(trace)
        return ExecutionTraceView(trace=trace, summary=summary)

    def evaluator(self, evaluator_id: str) -> EvaluatorView:
        """Return score evidence and failures for one evaluator."""

        scores = tuple(
            sorted(
                (score for score in self.store.scores.list() if score.evaluator_id == evaluator_id),
                key=lambda score: score.score_id,
            )
        )
        failures = tuple(
            sorted(
                (
                    failure
                    for failure in self.store.failures.list()
                    if failure.evaluator_id == evaluator_id
                ),
                key=lambda failure: failure.failure_id,
            )
        )
        if not scores and not failures:
            raise QueryError(f"Evaluator {evaluator_id!r} was not found")
        return EvaluatorView(evaluator_id=evaluator_id, scores=scores, failures=failures)

    def failure(self, failure_id: str) -> FailureView:
        """Return one failure with cluster membership and annotation history."""

        failure = self.store.failures.get(failure_id)
        if failure is None:
            raise QueryError(f"Failure {failure_id!r} was not found")
        cluster = next(
            (item for item in self.store.failure_clusters.list() if failure_id in item.failure_ids),
            None,
        )
        annotations = tuple(
            sorted(
                (
                    annotation
                    for annotation in self.store.annotations.list()
                    if annotation.target_id == failure_id
                ),
                key=lambda annotation: annotation.annotation_id,
            )
        )
        return FailureView(failure=failure, cluster=cluster, annotations=annotations)

    def experiment(
        self,
        run_id: str,
        *,
        report: EnterpriseEvaluationReport | None = None,
    ) -> ExperimentView:
        """Return one experiment and its stored execution evidence."""

        experiment = self.store.experiments.get(run_id)
        if experiment is None:
            raise QueryError(f"Experiment {run_id!r} was not found")
        traces = tuple(
            trace
            for trace_id in experiment.trace_ids
            if (trace := self.store.execution_traces.get(trace_id))
        )
        scores = tuple(
            sorted(
                (
                    score
                    for score_id in experiment.score_ids
                    if (score := self.store.scores.get(score_id))
                ),
                key=lambda score: score.score_id,
            )
        )
        failures = tuple(
            sorted(
                (
                    failure
                    for failure in self.store.failures.list()
                    if failure.metadata.get("run_id") == run_id
                    or failure.trace_id in {trace.execution_id for trace in traces}
                ),
                key=lambda failure: failure.failure_id,
            )
        )
        stored_report = report or self.store.enterprise_evaluation_reports.get(run_id)
        return ExperimentView(
            experiment=experiment,
            report=stored_report,
            traces=traces,
            scores=scores,
            failures=failures,
        )

    def promotion(self, candidate_id: str | None = None) -> PromotionView:
        """Return promotion decisions, comparisons, policies, and active state."""

        decisions = tuple(
            decision
            for decision in self.store.decisions.list()
            if candidate_id is None or decision.candidate_id == candidate_id
        )
        comparison_ids = {decision.comparison_id for decision in decisions}
        comparisons = tuple(
            comparison
            for comparison in self.store.comparisons.list()
            if candidate_id is None or comparison.comparison_id in comparison_ids
        )
        return PromotionView(
            decisions=decisions,
            comparisons=comparisons,
            policies=tuple(self.store.policies.list()),
            active_candidate=self.store.active_candidate.get("active"),
        )

    def comparison(self, comparison_id: str) -> BaselineComparison:
        """Return one stored baseline and candidate comparison."""

        comparison = self.store.comparisons.get(comparison_id)
        if comparison is None:
            raise QueryError(f"Comparison {comparison_id!r} was not found")
        return comparison

    def candidate_lineage(self, candidate_id: str) -> CandidateLineageView:
        """Return one candidate and all available parent candidates."""

        candidate = self.store.enterprise_candidates.get(candidate_id)
        if candidate is None:
            raise QueryError(f"Candidate {candidate_id!r} was not found")
        ancestors = []
        parent_id = candidate.parent_candidate_id
        while parent_id:
            parent = self.store.enterprise_candidates.get(parent_id)
            if parent is None:
                break
            ancestors.append(parent)
            parent_id = parent.parent_candidate_id
        return CandidateLineageView(candidate=candidate, ancestors=tuple(ancestors))

    def lifecycle_evidence(self, candidate_id: str | None = None) -> LifecycleEvidenceView:
        """Return stored lifecycle evidence without routing production traffic."""

        return LifecycleEvidenceView(
            shadow=tuple(
                item
                for item in self.store.shadow_evaluations.list()
                if candidate_id is None or item.candidate_id == candidate_id
            ),
            canary=tuple(
                item
                for item in self.store.canary_evaluations.list()
                if candidate_id is None or item.candidate_id == candidate_id
            ),
            readiness=tuple(
                item
                for item in self.store.promotion_readiness.list()
                if candidate_id is None or item.candidate_id == candidate_id
            ),
        )

    def list_sessions(self) -> tuple[SessionSummary, ...]:
        """Return sessions in stable order."""

        return tuple(self.store.sessions.list())

    def list_failures(self) -> tuple[EvaluationFailure, ...]:
        """Return failures in stable order."""

        return tuple(self.store.failures.list())

    def list_experiments(self) -> tuple[ExperimentRun, ...]:
        """Return experiments in stable order."""

        return tuple(self.store.experiments.list())

    def list_failure_clusters(self) -> tuple[FailureCluster, ...]:
        """Return failure clusters in stable order."""

        return tuple(self.store.failure_clusters.list())

    def list_root_cause_hypotheses(self) -> tuple[RootCauseHypothesis, ...]:
        """Return stored root-cause hypotheses."""

        return tuple(self.store.root_cause_hypotheses.list())

    def list_improvement_plans(self) -> tuple[ImprovementPlan, ...]:
        """Return stored bounded improvement plans."""

        return tuple(self.store.improvement_plans.list())


def _node_name(trace: ExecutionTrace) -> str:
    for key in ("node", "node_name", "runtime_component", "component"):
        value = trace.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "unknown"
