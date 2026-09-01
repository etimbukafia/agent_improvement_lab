"""Provider-neutral evaluation runner ports and adapters."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

from enterprise_agent_improvement_lab.contracts.candidates import EnterpriseAgentCandidate
from enterprise_agent_improvement_lab.contracts.cases import (
    DatasetVersion,
    EnterpriseEvaluationCase,
)
from enterprise_agent_improvement_lab.contracts.common import utc_now
from enterprise_agent_improvement_lab.contracts.experiments import RunManifest
from enterprise_agent_improvement_lab.contracts.governance import safe_summary
from enterprise_agent_improvement_lab.contracts.lifecycle import CandidateStage
from enterprise_agent_improvement_lab.contracts.traces import (
    ExecutionTrace,
    MessageEvent,
    TriggerInfo,
)
from enterprise_agent_improvement_lab.enterprise_runner import (
    EnterpriseCaseResult,
    EnterpriseEvaluationRunner,
    EnterpriseEvaluationRunResult,
)
from enterprise_agent_improvement_lab.environment import (
    EvaluationEnvironment,
    LocalEvaluationEnvironment,
)
from enterprise_agent_improvement_lab.evaluators.base import LabEvaluator
from enterprise_agent_improvement_lab.runtime import EnterpriseRuntime

Task = Callable[[Any], Any | Awaitable[Any]]


class EvaluationRunner(Protocol):
    """Common async and sync boundary for Lab evaluation runners."""

    async def run(
        self,
        dataset: DatasetVersion,
        candidate: EnterpriseAgentCandidate,
        manifest: RunManifest,
        *,
        repeat: int = 1,
    ) -> EnterpriseEvaluationRunResult:
        """Run a dataset and return the standard Lab result."""

    def run_sync(
        self,
        dataset: DatasetVersion,
        candidate: EnterpriseAgentCandidate,
        manifest: RunManifest,
        *,
        repeat: int = 1,
    ) -> EnterpriseEvaluationRunResult:
        """Run a dataset from synchronous application code."""


class RunnerUnavailableError(RuntimeError):
    """Raised when an optional runner dependency is not installed."""


class LocalEvaluationRunner(EnterpriseEvaluationRunner):
    """Run cases through a provider-neutral local enterprise runtime."""

    runner_kind = "local"


class _ReplayRuntime:
    name = "replay"
    version = "1.0.0"

    def __init__(self, traces: Sequence[ExecutionTrace]) -> None:
        self._traces_by_case: dict[str, ExecutionTrace] = {}
        for trace in sorted(traces, key=lambda item: (item.case_id or "", item.execution_id)):
            if trace.case_id is not None and trace.case_id not in self._traces_by_case:
                self._traces_by_case[trace.case_id] = trace

    async def execute(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        environment: EvaluationEnvironment,
    ) -> ExecutionTrace:
        """Return stored evidence without invoking tools or a live runtime."""

        del environment
        trace = self._traces_by_case.get(case.case_id)
        if trace is None:
            raise KeyError(f"No stored trace exists for case {case.case_id}")
        if trace.candidate_id != candidate.candidate_id:
            raise ValueError(
                "Replay trace candidate identity does not match the requested candidate"
            )
        return trace


class ReplayRunner(LocalEvaluationRunner):
    """Evaluate stored traces deterministically without live execution."""

    runner_kind = "replay"
    allows_live_tools = False

    def __init__(
        self,
        traces: Mapping[str, ExecutionTrace] | Sequence[ExecutionTrace] | None = None,
        *,
        trace_store: Any | None = None,
        evaluators: Sequence[LabEvaluator] | None = None,
        environment_factory: Callable[[], EvaluationEnvironment] | None = None,
    ) -> None:
        if traces is None and trace_store is None:
            raise ValueError("ReplayRunner needs stored traces or a trace store")
        if trace_store is not None:
            loader = getattr(trace_store, "list", None)
            if not callable(loader):
                raise TypeError("trace_store must provide list()")
            values = tuple(loader())
        elif isinstance(traces, Mapping):
            values = tuple(traces.values())
        else:
            values = tuple(traces or ())
        if not all(isinstance(trace, ExecutionTrace) for trace in values):
            raise TypeError("ReplayRunner traces must be ExecutionTrace values")
        super().__init__(
            _ReplayRuntime(values),
            evaluators,
            environment_factory=environment_factory,
        )


class ShadowEvaluationEnvironment(LocalEvaluationEnvironment):
    """Disposable local environment used by shadow evaluation."""

    execution_mode = "shadow"
    production_side_effects = False

    def __init__(self, *, frozen_at: datetime | None = None) -> None:
        # Shadow evaluation has no external service handles by default.
        super().__init__(frozen_at=frozen_at, external_service_stubs=())


class ShadowEvaluationRunner(LocalEvaluationRunner):
    """Run the normal Lab evaluation contracts in an isolated shadow context."""

    runner_kind = "shadow"
    stage = CandidateStage.SHADOW

    def __init__(
        self,
        runtime: Any,
        evaluators: Sequence[LabEvaluator] | None = None,
        *,
        environment_factory: Callable[[], EvaluationEnvironment] | None = None,
        hooks: Any | None = None,
    ) -> None:
        super().__init__(
            cast(EnterpriseRuntime, runtime),
            evaluators,
            environment_factory=environment_factory or ShadowEvaluationEnvironment,
            hooks=hooks,
        )

    async def run_case(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        environment: EvaluationEnvironment,
    ) -> Any:
        """Reject an environment that declares production side effects."""

        if getattr(environment, "production_side_effects", False):
            raise ValueError("Shadow evaluation cannot use a production-side-effect environment")
        return await super().run_case(case, candidate, environment)


class _PydanticTaskRuntime:
    name = "pydantic-evals"
    version = "1.0.0"

    def __init__(self, task: Task) -> None:
        self.task = task
        self._occurrences: dict[str, int] = {}

    async def execute(
        self,
        case: EnterpriseEvaluationCase,
        candidate: EnterpriseAgentCandidate,
        environment: EvaluationEnvironment,
    ) -> ExecutionTrace:
        value = case.input if case.input is not None else case.input_text
        if value is None:
            value = case.trigger.model_dump(mode="json") if case.trigger is not None else {}
        result = self.task(value)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ExecutionTrace):
            return result
        occurrence = self._occurrences.get(case.case_id, 0)
        self._occurrences[case.case_id] = occurrence + 1
        now = getattr(environment, "now", None)
        timestamp = now if isinstance(now, datetime) else utc_now()
        execution_id = f"pydantic-evals:{candidate.candidate_id}:{case.case_id}:{occurrence}"
        return ExecutionTrace(
            execution_id=execution_id,
            agent_id=candidate.agent_id,
            agent_version=candidate.agent_version or candidate.version,
            candidate_id=candidate.candidate_id,
            case_id=case.case_id,
            trigger=case.trigger or TriggerInfo(kind="task"),
            started_at=timestamp,
            ended_at=timestamp,
            events=(
                MessageEvent(
                    event_id=f"{execution_id}:output",
                    sequence=0,
                    timestamp=timestamp,
                    message_id=f"{execution_id}:output",
                    role="assistant",
                    message_summary=safe_summary(result),
                ),
            ),
        )


class PydanticEvalsRunner:
    """Optional Pydantic Evals adapter that returns Lab evaluation contracts.

    The adapter does not expose Pydantic Evals types in the Lab core. It uses
    the package only to validate the translated dataset and delegates result
    construction to the provider-neutral Lab runner.
    """

    runner_kind = "pydantic_evals"

    def __init__(
        self,
        runtime: Any | Task | None = None,
        evaluators: Sequence[LabEvaluator] | None = None,
        *,
        task: Task | None = None,
        environment_factory: Callable[[], EvaluationEnvironment] | None = None,
        hooks: Any | None = None,
    ) -> None:
        if task is None and runtime is not None and not hasattr(runtime, "execute"):
            task = runtime if callable(runtime) else None
            runtime = None
        if runtime is None:
            if task is None:
                raise ValueError("PydanticEvalsRunner needs a runtime or task")
            runtime = _PydanticTaskRuntime(task)
        self._delegate = LocalEvaluationRunner(
            cast(EnterpriseRuntime, runtime),
            evaluators,
            environment_factory=environment_factory,
            hooks=hooks,
        )

    @staticmethod
    def to_pydantic_dataset(dataset: DatasetVersion) -> Any:
        """Translate Lab cases to the optional Pydantic Evals dataset type."""

        module = _pydantic_evals_module()
        cases = [
            module.Case(
                name=case.case_id,
                inputs=_pydantic_inputs(case),
                expected_output=_pydantic_expected_output(case),
                metadata={
                    "lab_case_id": case.case_id,
                    "trigger_kind": case.trigger.kind if case.trigger is not None else "unknown",
                },
            )
            for case in dataset.cases
        ]
        return module.Dataset(name=dataset.dataset_id, cases=cases)

    build_dataset = to_pydantic_dataset

    async def run(
        self,
        dataset: DatasetVersion,
        candidate: EnterpriseAgentCandidate,
        manifest: RunManifest,
        *,
        repeat: int = 1,
    ) -> EnterpriseEvaluationRunResult:
        """Run through the optional adapter and return Lab-native evidence."""

        pydantic_dataset = self.to_pydantic_dataset(dataset)

        async def task(value: Any) -> EnterpriseCaseResult:
            if not isinstance(value, EnterpriseEvaluationCase):
                raise TypeError("Pydantic Evals inputs must be EnterpriseEvaluationCase values")
            return await self._delegate.run_case(
                value,
                candidate,
                self._delegate.environment_factory(),
            )

        report = await pydantic_dataset.evaluate(
            task,
            name=manifest.run_id,
            task_name=manifest.runtime_name,
            progress=False,
            repeat=repeat,
        )
        case_by_id = {case.case_id: case for case in dataset.cases}
        results: list[EnterpriseCaseResult] = [
            report_case.output
            for report_case in report.cases
            if isinstance(report_case.output, EnterpriseCaseResult)
        ]
        for failure in report.failures:
            failed_case = failure.inputs
            case = (
                failed_case
                if isinstance(failed_case, EnterpriseEvaluationCase)
                else case_by_id.get(failure.source_case_name or failure.name)
            )
            if case is None:
                raise ValueError("Pydantic Evals returned a failure for an unknown case")
            error = RuntimeError(failure.error_message)
            results.append(
                EnterpriseCaseResult(
                    case=case,
                    trace=None,
                    outcomes=(),
                    initial_state=None,
                    final_state=None,
                    state_comparison=None,
                    failures=self._delegate._failures(case, None, (), error),
                    error=error,
                )
            )
        if len(results) != len(dataset.cases) * repeat:
            raise RuntimeError("Pydantic Evals returned an incomplete case result set")
        return self._delegate.build_result(
            dataset,
            candidate,
            manifest,
            results,
            repeat=repeat,
        )

    def run_sync(
        self,
        dataset: DatasetVersion,
        candidate: EnterpriseAgentCandidate,
        manifest: RunManifest,
        *,
        repeat: int = 1,
    ) -> EnterpriseEvaluationRunResult:
        """Run the optional adapter from synchronous application code."""

        import asyncio

        return asyncio.run(self.run(dataset, candidate, manifest, repeat=repeat))


def _pydantic_evals_module() -> Any:
    try:
        import pydantic_evals
    except ImportError as exc:
        raise RunnerUnavailableError(
            "Pydantic Evals is not installed; install the optional evals extra"
        ) from exc
    return pydantic_evals


def _pydantic_inputs(case: EnterpriseEvaluationCase) -> Any:
    return case


def _pydantic_expected_output(case: EnterpriseEvaluationCase) -> Any:
    if not case.expected_outputs:
        return None
    if len(case.expected_outputs) == 1 and case.expected_outputs[0].path in {"$", "answer"}:
        return case.expected_outputs[0].expected_value
    return {expectation.path: expectation.expected_value for expectation in case.expected_outputs}


__all__ = [
    "EvaluationRunner",
    "LocalEvaluationRunner",
    "PydanticEvalsRunner",
    "ReplayRunner",
    "RunnerUnavailableError",
    "ShadowEvaluationEnvironment",
    "ShadowEvaluationRunner",
]
