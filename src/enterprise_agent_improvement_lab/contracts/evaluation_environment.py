"""Provider-neutral contracts for isolated enterprise evaluation state."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    require_aware_utc,
    utc_now,
)
from enterprise_agent_improvement_lab.serialization import stable_json_dumps


class StateSnapshot(ContractModel):
    """Immutable state captured at a point in an evaluation case."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    snapshot_id: str = Field(min_length=1)
    captured_at: datetime = Field(default_factory=utc_now)
    state: dict[str, Any] = Field(default_factory=dict)
    checksum: str | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> "StateSnapshot":
        object.__setattr__(self, "captured_at", require_aware_utc(self.captured_at))
        digest = sha256(stable_json_dumps(self.state).encode("utf-8")).hexdigest()
        if self.checksum is not None and self.checksum != digest:
            raise ValueError("State snapshot checksum does not match state")
        object.__setattr__(self, "checksum", digest)
        return self


class StateChange(ContractModel):
    """One deterministic difference between two state snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    path: str = Field(min_length=1)
    before: Any = None
    after: Any = None
    change_type: str = Field(min_length=1)


class StateComparison(ContractModel):
    """The deterministic difference between two snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    before_snapshot_id: str = Field(min_length=1)
    after_snapshot_id: str = Field(min_length=1)
    changes: tuple[StateChange, ...] = ()

    @property
    def changed_paths(self) -> tuple[str, ...]:
        """Return changed paths in stable order."""

        return tuple(change.path for change in self.changes)


class ExternalServiceStubDefinition(ContractModel):
    """Safe identity for a controlled external service substitute."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    stub_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    service_name: str = Field(min_length=1)


class ExternalServiceCall(ContractModel):
    """One safe observed request to a controlled external service stub."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    stub_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    operation: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    request_summary: str | None = None
    response_summary: str | None = None

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "ExternalServiceCall":
        object.__setattr__(self, "occurred_at", require_aware_utc(self.occurred_at))
        return self


__all__ = [
    "ExternalServiceStubDefinition",
    "ExternalServiceCall",
    "StateChange",
    "StateComparison",
    "StateSnapshot",
]
