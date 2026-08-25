"""Read-only query services for a Lab dashboard or another client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from agent_improvement_lab.contracts.evaluation import LabEvaluationReport
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
)
from agent_improvement_lab.contracts.sessions import SessionEvaluationResult, SessionSummary
from agent_improvement_lab.contracts.traces import (
    AgentTrace,
    ObservedToolCall,
    TraceSummary,
)
from agent_improvement_lab.storage import SQLiteStore


class QueryError(LookupError):
    """Raised when a requested dashboard record does not exist."""


@dataclass(frozen=True)
class ToolCallView:
    """One tool call in the trace navigation view."""

    turn_id: str
    call: ObservedToolCall
    node: str


@dataclass(frozen=True)
class TraceView:
    """Trace details with tool calls, scores, and evaluator explanations."""

    trace: AgentTrace | None
    summary: TraceSummary | None
    tools: tuple[ToolCallView, ...]
    scores: tuple[EvaluationScore, ...]
    failures: tuple[EvaluationFailure, ...]


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
    report: LabEvaluationReport | None
    traces: tuple[AgentTrace, ...]
    scores: tuple[EvaluationScore, ...]
    failures: tuple[EvaluationFailure, ...]


@dataclass(frozen=True)
class PromotionView:
    """Promotion evidence and the current active-candidate pointer."""

    decisions: tuple[PromotionDecision, ...]
    comparisons: tuple[BaselineComparison, ...]
    policies: tuple[PromotionPolicy, ...]
    active_candidate: ActiveCandidatePointer | None


def summarize_trace(trace: AgentTrace, score_ids: tuple[str, ...] = ()) -> TraceSummary:
    """Create a safe summary from one complete trace."""

    turns = trace.turns
    calls = tuple(call for turn in turns for call in turn.tool_calls)
    total_tokens = sum(turn.token_usage.total_tokens for turn in turns)
    latency = _elapsed_ms(trace.started_at, trace.ended_at)
    return TraceSummary(
        trace_id=trace.trace_id,
        case_id=trace.case_id,
        candidate_id=trace.candidate_id,
        session_id=trace.session_id,
        started_at=trace.started_at,
        ended_at=trace.ended_at,
        total_latency_ms=latency,
        total_tokens=total_tokens,
        turn_count=len(turns),
        tool_call_count=len(calls),
        tool_error_count=sum(call.outcome.value == "error" for call in calls),
        evaluation_score_ids=score_ids,
    )


class DashboardQueryService:
    """Provide stable read-only views for a dashboard or CLI client."""

    def __init__(self, store: SQLiteStore) -> None:
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

        trace = self.store.traces.get(trace_id)
        summary = self.store.trace_summaries.get(trace_id)
        if trace is None and summary is None:
            raise QueryError(f"Trace {trace_id!r} was not found")
        if summary is None and trace is not None:
            score_ids = tuple(
                score.score_id
                for score in self.store.scores.list()
                if trace_id in score.evidence_refs
            )
            summary = summarize_trace(trace, score_ids)
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
            for turn in sorted(trace.turns, key=lambda item: item.sequence):
                for call in sorted(turn.tool_calls, key=lambda item: item.sequence):
                    tools.append(ToolCallView(turn_id=turn.turn_id, call=call, node=node))
        return TraceView(
            trace=trace,
            summary=summary,
            tools=tuple(tools),
            scores=scores,
            failures=failures,
        )

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
        report: LabEvaluationReport | None = None,
    ) -> ExperimentView:
        """Return one experiment and its stored execution evidence."""

        experiment = self.store.experiments.get(run_id)
        if experiment is None:
            raise QueryError(f"Experiment {run_id!r} was not found")
        traces = tuple(
            trace for trace_id in experiment.trace_ids if (trace := self.store.traces.get(trace_id))
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
                    or failure.trace_id in {trace.trace_id for trace in traces}
                ),
                key=lambda failure: failure.failure_id,
            )
        )
        return ExperimentView(
            experiment=experiment,
            report=report,
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

    def list_sessions(self) -> tuple[SessionSummary, ...]:
        """Return sessions in stable order."""

        return tuple(self.store.sessions.list())

    def list_failures(self) -> tuple[EvaluationFailure, ...]:
        """Return failures in stable order."""

        return tuple(self.store.failures.list())

    def list_experiments(self) -> tuple[ExperimentRun, ...]:
        """Return experiments in stable order."""

        return tuple(self.store.experiments.list())


def _node_name(trace: AgentTrace) -> str:
    for key in ("node", "node_name", "runtime_component", "component"):
        value = trace.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "unknown"


def _elapsed_ms(started_at: datetime, ended_at: datetime | None) -> int:
    if ended_at is None:
        return 0
    elapsed = (ended_at - started_at) / timedelta(milliseconds=1)
    return max(0, int(round(elapsed)))
