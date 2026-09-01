"""Local, disposable evaluation environments for enterprise cases."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol

from enterprise_agent_improvement_lab.contracts.cases import (
    EnterpriseEvaluationCase,
    FixtureReference,
)
from enterprise_agent_improvement_lab.contracts.evaluation_environment import (
    ExternalServiceCall,
    ExternalServiceStubDefinition,
    StateChange,
    StateComparison,
    StateSnapshot,
)


class FixtureLoader(Protocol):
    """Load deterministic fixtures into one disposable environment."""

    async def load(self, fixture: FixtureReference, environment: "EvaluationEnvironment") -> None:
        """Load one declared fixture."""


class ExternalServiceStub(Protocol):
    """A controlled external service used only during evaluation."""

    definition: ExternalServiceStubDefinition

    async def start(self) -> None:
        """Start the stub."""

    async def stop(self) -> None:
        """Stop the stub."""

    def calls(self) -> tuple[ExternalServiceCall, ...]:
        """Return safe call evidence after the case."""


class ResetStrategy(Protocol):
    """Reset all disposable resources before a case starts."""

    async def reset(self, environment: "EvaluationEnvironment") -> None:
        """Reset the environment."""


class StateComparator(Protocol):
    """Compare immutable snapshots without modifying either snapshot."""

    def compare(self, before: StateSnapshot, after: StateSnapshot) -> StateComparison:
        """Return deterministic state differences."""


class EvaluationEnvironment(Protocol):
    """Provider-neutral lifecycle boundary for one enterprise case."""

    state: dict[str, Any]
    initial_snapshot: StateSnapshot | None
    final_snapshot: StateSnapshot | None
    state_comparison: StateComparison | None

    async def setup(self, case: EnterpriseEvaluationCase) -> None:
        """Reset, load fixtures, start stubs, and capture initial state."""

    async def teardown(self) -> None:
        """Capture final state and release disposable resources."""

    def snapshot(self, name: str) -> StateSnapshot:
        """Capture immutable state."""


class DeepStateComparator:
    """Compare JSON-like mappings by stable dotted paths."""

    def compare(self, before: StateSnapshot, after: StateSnapshot) -> StateComparison:
        changes: list[StateChange] = []
        _compare_values(before.state, after.state, "$", changes)
        return StateComparison(
            before_snapshot_id=before.snapshot_id,
            after_snapshot_id=after.snapshot_id,
            changes=tuple(changes),
        )


class LocalEvaluationEnvironment:
    """In-memory environment for safe deterministic evaluation tests."""

    def __init__(
        self,
        *,
        fixture_loader: FixtureLoader | None = None,
        reset_strategy: ResetStrategy | None = None,
        external_service_stubs: tuple[ExternalServiceStub, ...] = (),
        state_comparator: StateComparator | None = None,
        frozen_at: datetime | None = None,
    ) -> None:
        self.fixture_loader = fixture_loader
        self.reset_strategy = reset_strategy
        self.external_service_stubs = external_service_stubs
        self.comparator = state_comparator or DeepStateComparator()
        self.frozen_at = frozen_at.astimezone(timezone.utc) if frozen_at is not None else None
        self.state: dict[str, Any] = {}
        self.initial_snapshot: StateSnapshot | None = None
        self.final_snapshot: StateSnapshot | None = None
        self.state_comparison: StateComparison | None = None
        self.external_side_effects: tuple[ExternalServiceCall, ...] = ()
        self._case_id = "unknown"

    @property
    def now(self) -> datetime:
        """Return the controlled clock when one is configured."""

        return self.frozen_at or datetime.now(timezone.utc)

    async def setup(self, case: EnterpriseEvaluationCase) -> None:
        """Prepare a clean local state for one case."""

        self._case_id = case.case_id
        self.state = {}
        self.initial_snapshot = None
        self.final_snapshot = None
        self.state_comparison = None
        self.external_side_effects = ()
        if self.reset_strategy is not None:
            await self.reset_strategy.reset(self)
        self.state.update(deepcopy(case.initial_state))
        if self.fixture_loader is not None:
            for fixture in case.fixtures:
                await self.fixture_loader.load(fixture, self)
        for stub in self.external_service_stubs:
            await stub.start()
        self.initial_snapshot = self.snapshot("initial")

    async def teardown(self) -> None:
        """Capture final state before stopping every started stub."""

        try:
            self.final_snapshot = self.snapshot("final")
            if self.initial_snapshot is not None:
                self.state_comparison = self.comparator.compare(
                    self.initial_snapshot, self.final_snapshot
                )
        finally:
            for stub in reversed(self.external_service_stubs):
                try:
                    calls = getattr(stub, "calls", None)
                    if callable(calls):
                        self.external_side_effects += tuple(calls())
                finally:
                    await stub.stop()

    def snapshot(self, name: str) -> StateSnapshot:
        """Copy current state so later writes cannot change evidence."""

        return StateSnapshot(
            snapshot_id=f"{self._case_id}:{name}", captured_at=self.now, state=deepcopy(self.state)
        )


def _compare_values(before: Any, after: Any, path: str, changes: list[StateChange]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after), key=str):
            child = f"{path}.{key}"
            if key not in before:
                changes.append(StateChange(path=child, after=after[key], change_type="added"))
            elif key not in after:
                changes.append(StateChange(path=child, before=before[key], change_type="removed"))
            else:
                _compare_values(before[key], after[key], child, changes)
        return
    if before != after:
        changes.append(StateChange(path=path, before=before, after=after, change_type="changed"))


__all__ = [
    "DeepStateComparator",
    "EvaluationEnvironment",
    "ExternalServiceStub",
    "FixtureLoader",
    "LocalEvaluationEnvironment",
    "ResetStrategy",
    "StateComparator",
]
