"""Pydantic Evals execution adapter for Lab runtimes."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from pydantic_evals import Case, Dataset
from pydantic_evals.dataset import CaseLifecycle, ReportCase, ReportCaseFailure
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from agent_improvement_lab.contracts.candidates import AgentCandidate
from agent_improvement_lab.contracts.cases import DatasetVersion, EvaluationCaseRef
from agent_improvement_lab.contracts.evaluation import (
    AggregateGroup,
    CaseEvaluationResult,
    LabEvaluationReport,
)
from agent_improvement_lab.contracts.experiments import RunManifest
from agent_improvement_lab.contracts.failures import EvaluationScore, FailureCategory
from agent_improvement_lab.contracts.traces import AgentTrace
from agent_improvement_lab.evaluators import EvaluationContext, LabEvaluator, default_evaluators
from agent_improvement_lab.evaluators.base import EvaluationOutcome, validate_evaluator_ids
from agent_improvement_lab.runtime import (
    AgentRuntime,
    NoopRuntimeLifecycle,
    RuntimeLifecycleHooks,
    runtime_identity,
)


class PydanticEvalsUnavailableError(RuntimeError):
    """Raised when the optional Pydantic Evals extra is not installed."""


@dataclass
class _LabPydanticEvaluator(Evaluator[EvaluationCaseRef, AgentTrace, dict[str, Any]]):
    """Adapt one Lab evaluator to Pydantic Evals' evaluator protocol."""

    lab_evaluator: LabEvaluator

    @classmethod
    def get_serialization_name(cls) -> str:
        return "AgentImprovementLabEvaluator"

    def get_default_evaluation_name(self) -> str:
        return self.lab_evaluator.evaluator_id

    def evaluate(
        self, ctx: EvaluatorContext[EvaluationCaseRef, AgentTrace, dict[str, Any]]
    ) -> EvaluationReason:
        try:
            result = self.lab_evaluator.evaluate(EvaluationContext(ctx.inputs, ctx.output))
        except Exception as exc:  # pragma: no cover - the runner records this path separately
            result = EvaluationOutcome(
                score=0.0,
                passed=False,
                explanation=(
                    f"Evaluator {self.lab_evaluator.evaluator_id} raised "
                    f"{type(exc).__name__}: {exc}"
                ),
                confidence=0.0,
                failure_category=FailureCategory.QUALITY,
            )
        return EvaluationReason(result.score, result.explanation)


def _make_lifecycle(
    hooks: RuntimeLifecycleHooks,
    candidate: AgentCandidate,
) -> type[CaseLifecycle[Any, Any, Any]]:
    """Build a Pydantic Evals lifecycle class with Lab adapter hooks."""

    class RuntimeLifecycle(CaseLifecycle[Any, Any, Any]):
        async def setup(self) -> None:
            await hooks.before_case(self.case.inputs, candidate)

        async def teardown(self, result: Any) -> None:
            trace = getattr(result, "output", None)
            if not isinstance(trace, AgentTrace):
                trace = None
            error = getattr(result, "error_message", None)
            await hooks.after_case(self.case.inputs, candidate, trace, error)

    return RuntimeLifecycle


@dataclass(frozen=True)
class EvaluationRunResult:
    """Safe Lab report plus traces and the underlying Pydantic report."""

    report: LabEvaluationReport
    traces: tuple[AgentTrace, ...]
    pydantic_report: Any


class PydanticEvalsRunner:
    """Run a generic Lab runtime through Pydantic Evals."""

    def __init__(
        self,
        runtime: AgentRuntime,
        evaluators: Sequence[LabEvaluator] | None = None,
        *,
        hooks: RuntimeLifecycleHooks | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self.runtime = runtime
        self.evaluators = validate_evaluator_ids(
            tuple(evaluators) if evaluators is not None else default_evaluators()
        )
        self.hooks = hooks or NoopRuntimeLifecycle()
        self.max_concurrency = max_concurrency

    async def run(
        self,
        dataset: DatasetVersion,
        candidate: AgentCandidate,
        manifest: RunManifest,
        *,
        repeat: int = 1,
    ) -> EvaluationRunResult:
        """Execute and score a dataset."""

        self._validate_request(dataset, candidate, manifest, repeat)
        try:
            pydantic_dataset: Dataset[Any, Any, Any] = Dataset(
                name=f"{dataset.dataset_id}@{dataset.version}",
                cases=[self._to_pydantic_case(case) for case in dataset.cases],
                evaluators=[_LabPydanticEvaluator(evaluator) for evaluator in self.evaluators],
            )
        except Exception as exc:  # pragma: no cover - package API errors are environment-specific
            raise PydanticEvalsUnavailableError(
                f"Could not create the Pydantic Evals dataset: {exc}"
            ) from exc

        async def task(case: EvaluationCaseRef) -> AgentTrace:
            return await self.runtime.execute(case, candidate)

        pydantic_report = await pydantic_dataset.evaluate(
            task,
            name=manifest.run_id,
            task_name=manifest.runtime_name,
            max_concurrency=self.max_concurrency,
            progress=False,
            repeat=repeat,
            lifecycle=_make_lifecycle(self.hooks, candidate),
        )
        return self._build_result(dataset, candidate, manifest, repeat, pydantic_report)

    def run_sync(
        self,
        dataset: DatasetVersion,
        candidate: AgentCandidate,
        manifest: RunManifest,
        *,
        repeat: int = 1,
    ) -> EvaluationRunResult:
        """Synchronous wrapper around :meth:`run`."""

        return asyncio.run(self.run(dataset, candidate, manifest, repeat=repeat))

    @staticmethod
    def _to_pydantic_case(case: EvaluationCaseRef) -> Case[EvaluationCaseRef, None, dict[str, Any]]:
        metadata = dict(case.metadata)
        metadata.update(
            {
                "lab_case_id": case.case_id,
                "split": case.split.value,
                "risk": case.risk.value,
                "tags": list(case.tags),
                "workflow": str(case.metadata.get("workflow", "unspecified")),
            }
        )
        return Case(
            name=case.case_id,
            inputs=case,
            expected_output=None,
            metadata=metadata,
        )

    def _validate_request(
        self,
        dataset: DatasetVersion,
        candidate: AgentCandidate,
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

    def _build_result(
        self,
        dataset: DatasetVersion,
        candidate: AgentCandidate,
        manifest: RunManifest,
        repeat: int,
        pydantic_report: Any,
    ) -> EvaluationRunResult:
        case_by_id = {case.case_id: case for case in dataset.cases}
        occurrences: defaultdict[str, int] = defaultdict(int)
        scores: list[EvaluationScore] = []
        case_results: list[CaseEvaluationResult] = []
        traces: list[AgentTrace] = []
        runtime_failures: list[str] = []

        for report_case in pydantic_report.cases:
            case_id = self._report_case_id(report_case)
            case = case_by_id[case_id]
            repeat_index = occurrences[case_id]
            occurrences[case_id] += 1
            trace = report_case.output
            if not isinstance(trace, AgentTrace):
                continue
            traces.append(trace)
            case_scores = self._score_trace(manifest, case, trace, repeat_index)
            scores.extend(case_scores)
            case_results.append(
                self._case_result(
                    case,
                    trace,
                    repeat_index,
                    case_scores,
                    task_duration_ms=round(report_case.task_duration * 1000),
                    total_duration_ms=round(report_case.total_duration * 1000),
                )
            )

        for report_failure in pydantic_report.failures:
            case_id = self._report_case_id(report_failure)
            case = case_by_id[case_id]
            repeat_index = occurrences[case_id]
            occurrences[case_id] += 1
            error = str(report_failure.error_message)
            runtime_failures.append(error)
            score = EvaluationScore(
                score_id=self._score_id(
                    manifest.run_id, case_id, repeat_index, "runtime.execution"
                ),
                evaluator_id="runtime.execution",
                score=0.0,
                passed=False,
                explanation=f"Runtime execution failed: {error}",
                confidence=1.0,
                failure_category=FailureCategory.QUALITY,
                created_at=manifest.created_at,
            )
            scores.append(score)
            case_results.append(
                CaseEvaluationResult(
                    case_id=case.case_id,
                    repeat_index=repeat_index,
                    split=case.split,
                    risk=case.risk,
                    tags=case.tags,
                    workflow=str(case.metadata.get("workflow", "unspecified")),
                    score_ids=(score.score_id,),
                    passed=False,
                    mean_score=0.0,
                    failure_categories=(FailureCategory.QUALITY,),
                )
            )

        if not case_results:
            raise RuntimeError("Pydantic Evals returned no case results")
        case_results.sort(key=lambda result: (result.case_id, result.repeat_index))
        scores.sort(key=lambda score: score.score_id)
        report = LabEvaluationReport(
            run_id=manifest.run_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            candidate_id=candidate.candidate_id,
            pydantic_report_name=pydantic_report.name,
            repeat_count=repeat,
            evaluator_ids=tuple(evaluator.evaluator_id for evaluator in self.evaluators),
            case_results=tuple(case_results),
            scores=tuple(scores),
            aggregates=tuple(self._aggregate(case_results)),
            runtime_failures=tuple(runtime_failures),
            created_at=manifest.created_at,
        )
        return EvaluationRunResult(
            report=report, traces=tuple(traces), pydantic_report=pydantic_report
        )

    def _score_trace(
        self,
        manifest: RunManifest,
        case: EvaluationCaseRef,
        trace: AgentTrace,
        repeat_index: int,
    ) -> list[EvaluationScore]:
        result: list[EvaluationScore] = []
        context = EvaluationContext(case, trace)
        for evaluator in self.evaluators:
            try:
                evaluated = evaluator.evaluate(context)
            except Exception as exc:
                evaluated = EvaluationOutcome(
                    score=0.0,
                    passed=False,
                    explanation=(
                        f"Evaluator {evaluator.evaluator_id} raised {type(exc).__name__}: {exc}"
                    ),
                    confidence=0.0,
                    failure_category=FailureCategory.QUALITY,
                )
            result.append(
                EvaluationScore(
                    score_id=self._score_id(
                        manifest.run_id, case.case_id, repeat_index, evaluator.evaluator_id
                    ),
                    evaluator_id=evaluator.evaluator_id,
                    score=evaluated.score,
                    passed=evaluated.passed,
                    explanation=evaluated.explanation,
                    confidence=evaluated.confidence,
                    evidence_refs=evaluated.evidence_refs,
                    failure_category=evaluated.failure_category,
                    created_at=manifest.created_at,
                )
            )
        return result

    @staticmethod
    def _case_result(
        case: EvaluationCaseRef,
        trace: AgentTrace,
        repeat_index: int,
        scores: Sequence[EvaluationScore],
        *,
        task_duration_ms: int,
        total_duration_ms: int,
    ) -> CaseEvaluationResult:
        mean_score = sum(score.score for score in scores) / max(len(scores), 1)
        failures = tuple(
            sorted(
                {
                    score.failure_category
                    for score in scores
                    if not score.passed and score.failure_category is not None
                },
                key=lambda category: category.value,
            )
        )
        return CaseEvaluationResult(
            case_id=case.case_id,
            repeat_index=repeat_index,
            split=case.split,
            risk=case.risk,
            tags=case.tags,
            workflow=str(case.metadata.get("workflow", "unspecified")),
            trace_id=trace.trace_id,
            score_ids=tuple(score.score_id for score in scores),
            passed=all(score.passed for score in scores),
            mean_score=mean_score,
            failure_categories=failures,
            task_duration_ms=task_duration_ms,
            total_duration_ms=total_duration_ms,
        )

    @staticmethod
    def _score_id(run_id: str, case_id: str, repeat_index: int, evaluator_id: str) -> str:
        return f"{run_id}:{case_id}:repeat-{repeat_index}:{evaluator_id}"

    @staticmethod
    def _report_case_id(
        report_case: ReportCase[Any, Any, Any] | ReportCaseFailure[Any, Any, Any],
    ) -> str:
        metadata = report_case.metadata
        if isinstance(metadata, dict) and isinstance(metadata.get("lab_case_id"), str):
            return str(metadata["lab_case_id"])
        if report_case.source_case_name:
            return report_case.source_case_name
        return report_case.name

    @staticmethod
    def _aggregate(case_results: Sequence[CaseEvaluationResult]) -> list[AggregateGroup]:
        groups: dict[tuple[str, str], list[CaseEvaluationResult]] = defaultdict(list)
        for result in case_results:
            groups[("overall", "all")].append(result)
            groups[("split", result.split.value)].append(result)
            groups[("risk", result.risk.value)].append(result)
            groups[("workflow", result.workflow)].append(result)
            for tag in result.tags:
                groups[("tag", tag)].append(result)
            for category in result.failure_categories:
                groups[("failure_category", category.value)].append(result)

        dimension_order = {
            "overall": 0,
            "split": 1,
            "risk": 2,
            "tag": 3,
            "workflow": 4,
            "failure_category": 5,
        }
        aggregate: list[AggregateGroup] = []
        for (dimension, key), results in sorted(
            groups.items(), key=lambda item: (dimension_order[item[0][0]], item[0][1])
        ):
            failure_categories = tuple(
                sorted(
                    {category for result in results for category in result.failure_categories},
                    key=lambda category: category.value,
                )
            )
            failure_count = sum(not result.passed for result in results)
            aggregate.append(
                AggregateGroup(
                    dimension=dimension,
                    key=key,
                    case_count=len(results),
                    passed_count=sum(result.passed for result in results),
                    pass_rate=sum(result.passed for result in results) / len(results),
                    mean_score=sum(result.mean_score for result in results) / len(results),
                    failure_count=failure_count,
                    failure_categories=failure_categories,
                )
            )
        return aggregate
