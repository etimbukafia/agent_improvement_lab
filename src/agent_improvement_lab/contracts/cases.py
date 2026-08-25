"""Dataset and evaluation-case contracts."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_improvement_lab.contracts.common import ContractModel, VersionString, require_aware_utc


class DatasetSplit(StrEnum):
    """Supported evaluation dataset splits."""

    SMOKE = "smoke"
    DEVELOPMENT = "development"
    REGRESSION = "regression"
    HOLDOUT = "holdout"
    SECURITY = "security"


class RiskLevel(StrEnum):
    """Risk attached to an evaluation case."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseProvenance(ContractModel):
    """Source information for a case or dataset."""

    source: str = Field(min_length=1)
    source_ref: str | None = None
    collected_at: datetime | None = None
    reviewer: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "CaseProvenance":
        if self.collected_at is not None:
            object.__setattr__(self, "collected_at", require_aware_utc(self.collected_at))
        return self


class NumericRange(ContractModel):
    """Inclusive or exclusive numeric bounds for one tool argument."""

    minimum: float | None = None
    maximum: float | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    @model_validator(mode="after")
    def validate_bounds(self) -> "NumericRange":
        if self.minimum is None and self.maximum is None:
            raise ValueError("NumericRange needs a minimum or maximum")
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("minimum must not be greater than maximum")
            if self.minimum == self.maximum and not (
                self.minimum_inclusive and self.maximum_inclusive
            ):
                raise ValueError("Equal bounds must both be inclusive")
        return self


class ToolCallExpectation(ContractModel):
    """Expected behavior for one tool call in a case."""

    name: str = Field(min_length=1)
    order: int = Field(ge=0)
    required_arguments: tuple[str, ...] = ()
    exact_arguments: dict[str, Any] = Field(default_factory=dict)
    argument_types: dict[str, str] = Field(default_factory=dict)
    allowed_values: dict[str, tuple[Any, ...]] = Field(default_factory=dict)
    patterns: dict[str, str] = Field(default_factory=dict)
    numeric_ranges: dict[str, NumericRange] = Field(default_factory=dict)
    protected_arguments: tuple[str, ...] = ()

    @field_validator("patterns")
    @classmethod
    def validate_patterns(cls, value: dict[str, str]) -> dict[str, str]:
        for argument, pattern in value.items():
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Invalid pattern for argument {argument!r}: {exc}") from exc
        return value

    @model_validator(mode="after")
    def validate_argument_declarations(self) -> "ToolCallExpectation":
        undeclared_protected = set(self.protected_arguments) - set(self.exact_arguments)
        if undeclared_protected:
            names = ", ".join(sorted(undeclared_protected))
            raise ValueError(f"Protected arguments require exact values: {names}")
        if len(self.protected_arguments) != len(set(self.protected_arguments)):
            raise ValueError("protected_arguments must be unique")
        return self


class EvaluationCaseRef(ContractModel):
    """A complete case record passed to a runtime adapter."""

    case_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: VersionString
    split: DatasetSplit
    risk: RiskLevel = RiskLevel.MEDIUM
    tags: tuple[str, ...] = ()
    input: dict[str, Any]
    expected: dict[str, Any] = Field(default_factory=dict)
    tool_expectations: tuple[ToolCallExpectation, ...] = ()
    provenance: CaseProvenance
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetVersion(ContractModel):
    """A versioned collection of evaluation cases."""

    dataset_id: str = Field(min_length=1)
    version: VersionString
    description: str = Field(min_length=1)
    cases: tuple[EvaluationCaseRef, ...] = Field(min_length=1)
    provenance: CaseProvenance
    parent_version: VersionString | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_case_references(self) -> "DatasetVersion":
        case_ids = [case.case_id for case in self.cases]
        duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"Duplicate case IDs: {', '.join(duplicates)}")

        invalid = [
            case.case_id
            for case in self.cases
            if case.dataset_id != self.dataset_id or case.dataset_version != self.version
        ]
        if invalid:
            joined = ", ".join(invalid)
            raise ValueError(f"Cases reference the wrong dataset or version: {joined}")

        if self.parent_version == self.version:
            raise ValueError("parent_version must differ from version")

        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self
