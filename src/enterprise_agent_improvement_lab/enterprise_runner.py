"""Provider-neutral evaluation runner for enterprise cases."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from enterprise_agent_improvement_lab.contracts.candidates import EnterpriseAgentCandidate
from enterprise_agent_improvement_lab.contracts.cases import (
    DatasetVersion,
    EnterpriseEvaluationCase,
)
from enterprise_agent_improvement_lab.contracts.common import utc_now
from enterprise_agent_improvement_lab.contracts.evaluation import (
    EnterpriseAggregateGroup,
    EnterpriseCaseEvaluationResult,
    EnterpriseEvaluationReport,
)
from enterprise_agent_improvement_lab.contracts.evaluation_environment import (
    StateComparison,
    StateSnapshot,
)
from enterprise_agent_improvement_lab.contracts.experiments import RunManifest
from enterprise_agent_improvement_lab.contracts.failures import (
    EvaluationFailure,
    EvaluationScore,
    FailureCategory,
    Severity,
)
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTrace
from enterprise_agent_improvement_lab.environment import (
    EvaluationEnvironment,
    LocalEvaluationEnvironment,
)
from enterprise_agent_improvement_lab.evaluators import (
    EvaluationContext,
    LabEvaluator,
    default_enterprise_evaluators,
)
from enterprise_agent_improvement_lab.evaluators.base import (
    EvaluationOutcome,
    validate_evaluator_ids,
)
from enterprise_agent_improvement_lab.runtime import (
    EnterpriseRuntime,
    EnterpriseRuntimeLifecycleHooks,
    NoopEnterpriseRuntimeLifecycle,
    runtime_identity,
)


@dataclass(frozen=True)
class EnterpriseCaseResult:
    """Evidence retained for one case, including runtime failures."""

    case: EnterpriseEvaluationCase
    trace: ExecutionTrace | None
    outcomes: tuple[tuple[str, EvaluationOutcome], ...]
    initial_state: StateSnapshot | None
    final_state: StateSnapshot | None
    state_comparison: StateComparison | None
    failures: tuple[EvaluationFailure, ...]
    error: Exception | None


@dataclass(frozen=True)
class EnterpriseEvaluationRunResult:
    """Typed report and execution evidence for one enterprise run."""

    report: EnterpriseEvaluationReport
    traces: tuple[ExecutionTrace, ...]
    cases: tuple[EnterpriseCaseResult, ...]


class EnterpriseEvaluationRunner:
    """Run enterprise cases with isolated environments and typed evidence."""

    def __init__(
        self,
        runtime: EnterpriseRuntime,
        evaluators: Sequence[LabEvaluator] | None = None,
        *,
        environment_factory: Callable[[], EvaluationEnvironment] | None = None,
        hooks: EnterpriseRuntimeLifecycleHooks | None = None,
    ) -> None:
        self.runtime = runtime
        self.evaluators = validate_evaluator_ids(
            tuple(evaluators) if evaluators is not None else default_enterprise_evaluators()
        )
        self.environment_factory = environment_factory or LocalEvaluationEnvironment
        self.hooks = hooks or NoopEnterpriseRuntimeLifecycle()

    async def run_case(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        environment: EvaluationEnvironment,
    ) -> EnterpriseCaseResult:
        """Run and evaluate one case while always attempting teardown."""

        trace: ExecutionTrace | None = None
        error: Exception | None = None
        try:
            await self.hooks.before_case(case, candidate)
            await environment.setup(case)
            trace = await self.runtime.execute(case, candidate, environment)
            if not isinstance(trace, ExecutionTrace):
                raise TypeError("Enterprise runtime must return an ExecutionTrace")
            if trace.candidate_id != candidate.candidate_id or trace.case_id != case.case_id:
                raise ValueError("Runtime trace identity does not match the case and candidate")
        except Exception as exc:
            error = exc
        finally:
            try:
                await environment.teardown()
            finally:
                await self.hooks.after_case(
                    case,
                    candidate,
                    trace,
                    type(error).__name__ if error is not None else None,
                )

        outcomes: list[tuple[str, EvaluationOutcome]] = []
        if trace is not None:
            context = EvaluationContext(
                case,
                trace,
                environment.initial_snapshot,
                environment.final_snapshot,
                environment.state_comparison,
            )
            for evaluator in self.evaluators:
                try:
                    outcomes.append((evaluator.evaluator_id, evaluator.evaluate(context)))
                except Exception as exc:
                    outcomes.append(
                        (
                            evaluator.evaluator_id,
                            EvaluationOutcome(
                                score=0.0,
                                passed=False,
                                explanation=(
                                    f"Evaluator {evaluator.evaluator_id} raised "
                                    f"{type(exc).__name__}."
                                ),
                                confidence=0.0,
                                failure_category=FailureCategory.QUALITY,
                            ),
                        )
                    )
        failures = self._failures(case, trace, outcomes, error)
        return EnterpriseCaseResult(
            case=case,
            trace=trace,
            outcomes=tuple(outcomes),
            initial_state=environment.initial_snapshot,
            final_state=environment.final_snapshot,
            state_comparison=environment.state_comparison,
            failures=failures,
            error=error,
        )

    async def run(
        self,
        dataset: DatasetVersion,
        candidate: EnterpriseAgentCandidate,
        manifest: RunManifest,
        *,
        repeat: int = 1,
    ) -> EnterpriseEvaluationRunResult:
        """Execute every enterprise case in deterministic case/repetition order."""

        self._validate_request(dataset, candidate, manifest, repeat)
        cases: list[EnterpriseCaseResult] = []
        for case in dataset.cases:
            for _ in range(repeat):
                cases.append(await self.run_case(case, candidate, self.environment_factory()))

        return self.build_result(dataset, candidate, manifest, cases, repeat=repeat)

    def build_result(
        self,
        dataset: DatasetVersion,
        candidate: EnterpriseAgentCandidate,
        manifest: RunManifest,
        cases: Sequence[EnterpriseCaseResult],
        *,
        repeat: int = 1,
    ) -> EnterpriseEvaluationRunResult:
        """Build the standard Lab result from already executed case results.

        Runner adapters use this method after their provider has scheduled the
        cases. Evaluator and report semantics remain in the Lab runner.
        """

        self._validate_request(dataset, candidate, manifest, repeat)

        scores: list[EvaluationScore] = []
        report_results: list[EnterpriseCaseEvaluationResult] = []
        traces: list[ExecutionTrace] = []
        failures: list[EvaluationFailure] = []
        occurrence: defaultdict[str, int] = defaultdict(int)
        for result in cases:
            repeat_index = occurrence[result.case.case_id]
            occurrence[result.case.case_id] += 1
            if result.trace is not None:
                traces.append(result.trace)
            failures.extend(result.failures)
            case_scores: list[EvaluationScore] = []
            for evaluator_id, evaluated in result.outcomes:
                score = EvaluationScore(
                    score_id=self._score_id(
                        manifest.run_id,
                        result.case.case_id,
                        repeat_index,
                        evaluator_id,
                    ),
                    evaluator_id=evaluator_id,
                    score=evaluated.score,
                    passed=evaluated.passed,
                    explanation=evaluated.explanation,
                    confidence=evaluated.confidence,
                    evidence_refs=evaluated.evidence_refs,
                    failure_category=evaluated.failure_category,
                    created_at=manifest.created_at,
                )
                scores.append(score)
                case_scores.append(score)
            if result.error is not None:
                scores.append(
                    EvaluationScore(
                        score_id=self._score_id(
                            manifest.run_id,
                            result.case.case_id,
                            repeat_index,
                            "runtime.execution",
                        ),
                        evaluator_id="runtime.execution",
                        score=0.0,
                        passed=False,
                        explanation="Runtime execution failed for this case.",
                        confidence=1.0,
                        failure_category=FailureCategory.INTEGRATION,
                        created_at=manifest.created_at,
                    )
                )
            report_results.append(
                self._case_result(
                    result,
                    repeat_index,
                    case_scores,
                    manifest.run_id,
                )
            )

        if not report_results:
            raise RuntimeError("Enterprise evaluation returned no case results")
        scores.sort(key=lambda item: item.score_id)
        report_results.sort(key=lambda item: (item.case_id, item.repeat_index))
        failures.sort(key=lambda item: item.failure_id)
        traces.sort(key=lambda item: item.execution_id)
        report = EnterpriseEvaluationReport(
            run_id=manifest.run_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            candidate_id=candidate.candidate_id,
            repeat_count=repeat,
            evaluator_ids=tuple(evaluator.evaluator_id for evaluator in self.evaluators),
            case_results=tuple(report_results),
            scores=tuple(scores),
            failures=tuple(failures),
            traces=tuple(traces),
            aggregates=tuple(self._aggregate(report_results, dataset)),
            environment_snapshot_id=manifest.environment_snapshot_ref,
            created_at=manifest.created_at,
        )
        return EnterpriseEvaluationRunResult(
            report=report, traces=tuple(traces), cases=tuple(cases)
        )

    def run_sync(
        self,
        dataset: DatasetVersion,
        candidate: EnterpriseAgentCandidate,
        manifest: RunManifest,
        *,
        repeat: int = 1,
    ) -> EnterpriseEvaluationRunResult:
        """Synchronous wrapper around :meth:`run`."""

        import asyncio

        return asyncio.run(self.run(dataset, candidate, manifest, repeat=repeat))

    @staticmethod
    def _failures(
        case: EnterpriseEvaluationCase,
        trace: ExecutionTrace | None,
        outcomes: Sequence[tuple[str, EvaluationOutcome]],
        error: Exception | None,
    ) -> tuple[EvaluationFailure, ...]:
        """Create safe failure records from runtime and evaluator outcomes."""

        trace_id = trace.trace_id if trace is not None else None
        failures: list[EvaluationFailure] = []
        if error is not None:
            failures.append(
                EvaluationFailure(
                    failure_id=f"failure:{trace_id or case.case_id}:runtime.execution",
                    evaluator_id="runtime.execution",
                    category=FailureCategory.INTEGRATION,
                    severity=_severity(case.risk.value, FailureCategory.INTEGRATION),
                    case_id=case.case_id,
                    trace_id=trace_id,
                    summary=f"Runtime execution failed for case {case.case_id}.",
                    expected_behavior="The runtime must execute the case.",
                    observed_behavior=f"{type(error).__name__} was raised by the runtime.",
                    created_at=case.provenance.collected_at or utc_now(),
                )
            )
        for evaluator_id, evaluated in outcomes:
            if evaluated.passed:
                continue
            category = evaluated.failure_category or FailureCategory.QUALITY
            failures.append(
                EvaluationFailure(
                    failure_id=f"failure:{trace_id or case.case_id}:{evaluator_id}",
                    evaluator_id=evaluator_id,
                    category=category,
                    severity=_severity(case.risk.value, category),
                    case_id=case.case_id,
                    trace_id=trace_id,
                    summary=f"{evaluator_id} failed for case {case.case_id}.",
                    expected_behavior=_expected_behavior(evaluator_id),
                    observed_behavior=evaluated.explanation,
                    created_at=case.provenance.collected_at or utc_now(),
                    score=evaluated.score,
                    confidence=evaluated.confidence,
                    evidence_refs=evaluated.evidence_refs,
                    affected_workflow=_workflow(case),
                )
            )
        return tuple(failures)

    def _validate_request(
        self,
        dataset: DatasetVersion,
        candidate: Any,
        manifest: RunManifest,
        repeat: int,
    ) -> None:
        if repeat < 1:
            raise ValueError("repeat must be at least 1")
        if manifest.dataset_id != dataset.dataset_id or manifest.dataset_version != dataset.version:
            raise ValueError("Run manifest dataset reference does not match the dataset")
        if manifest.candidate_id != candidate.candidate_id:
            raise ValueError("Run manifest candidate reference does not match the candidate")
        runtime_name, runtime_version = runtime_identity(self.runtime)
        if manifest.runtime_name != runtime_name or manifest.runtime_version != runtime_version:
            raise ValueError("Run manifest runtime identity does not match the runtime")

    @staticmethod
    def _score_id(run_id: str, case_id: str, repeat_index: int, evaluator_id: str) -> str:
        return f"{run_id}:{case_id}:repeat-{repeat_index}:{evaluator_id}"

    @staticmethod
    def _case_result(
        result: EnterpriseCaseResult,
        repeat_index: int,
        scores: Sequence[EvaluationScore],
        run_id: str,
    ) -> EnterpriseCaseEvaluationResult:
        all_failures = tuple(result.failures)
        mean_score = sum(score.score for score in scores) / max(len(scores), 1)
        passed = result.error is None and all(score.passed for score in scores)
        categories = tuple(
            sorted(
                {failure.category for failure in all_failures}
                | {
                    score.failure_category
                    for score in scores
                    if not score.passed and score.failure_category is not None
                },
                key=lambda item: item.value,
            )
        )
        duration = (
            max(
                0,
                int((result.trace.ended_at - result.trace.started_at).total_seconds() * 1000),
            )
            if result.trace is not None and result.trace.ended_at is not None
            else 0
        )
        return EnterpriseCaseEvaluationResult(
            case_id=result.case.case_id,
            repeat_index=repeat_index,
            split=result.case.split,
            risk=result.case.risk,
            trace_id=result.trace.trace_id if result.trace is not None else None,
            score_ids=tuple(score.score_id for score in scores),
            failure_ids=tuple(failure.failure_id for failure in all_failures),
            passed=passed,
            mean_score=mean_score,
            failure_categories=categories,
            dimensions=tuple(sorted({score.evaluator_id for score in scores})),
            task_duration_ms=duration,
            total_duration_ms=duration,
        )

    @staticmethod
    def _aggregate(
        results: Sequence[EnterpriseCaseEvaluationResult],
        dataset: DatasetVersion,
    ) -> list[EnterpriseAggregateGroup]:
        case_by_id = {case.case_id: case for case in dataset.cases}
        groups: dict[tuple[str, str], list[EnterpriseCaseEvaluationResult]] = defaultdict(list)
        for result in results:
            groups[("overall", "all")].append(result)
            groups[("split", result.split.value)].append(result)
            groups[("risk", result.risk.value)].append(result)
            case = case_by_id[result.case_id]
            workflow = case.metadata.get("workflow")
            if isinstance(workflow, str) and workflow.strip():
                groups[("workflow", workflow)].append(result)
            for tag in case.tags:
                groups[("tag", tag)].append(result)
            for category in result.failure_categories:
                groups[("failure_category", category.value)].append(result)
        order = {
            "overall": 0,
            "split": 1,
            "risk": 2,
            "workflow": 3,
            "tag": 4,
            "failure_category": 5,
        }
        aggregate: list[EnterpriseAggregateGroup] = []
        for (dimension, key), grouped in sorted(
            groups.items(), key=lambda item: (order[item[0][0]], item[0][1])
        ):
            failure_count = sum(not item.passed for item in grouped)
            aggregate.append(
                EnterpriseAggregateGroup(
                    dimension=dimension,
                    key=key,
                    case_count=len(grouped),
                    passed_count=sum(item.passed for item in grouped),
                    pass_rate=sum(item.passed for item in grouped) / len(grouped),
                    mean_score=sum(item.mean_score for item in grouped) / len(grouped),
                    failure_count=failure_count,
                    failure_categories=tuple(
                        sorted(
                            {category for item in grouped for category in item.failure_categories},
                            key=lambda item: item.value,
                        )
                    ),
                )
            )
        return aggregate


def _expected_behavior(evaluator_id: str) -> str:
    return f"The case must satisfy {evaluator_id}."


def _workflow(case: EnterpriseEvaluationCase) -> str | None:
    value = case.metadata.get("workflow")
    return value if isinstance(value, str) and value.strip() else None


def _severity(risk: str, category: FailureCategory) -> Severity:
    if category in {FailureCategory.AUTHORIZATION, FailureCategory.PRIVACY}:
        return Severity.CRITICAL if risk == "critical" else Severity.HIGH
    return Severity(risk)


__all__ = [
    "EnterpriseCaseResult",
    "EnterpriseEvaluationRunResult",
    "EnterpriseEvaluationRunner",
]
