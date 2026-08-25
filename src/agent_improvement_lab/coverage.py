"""Coverage-matrix contract, loading, and validation."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import Field, ValidationError, model_validator

from agent_improvement_lab.contracts.common import ContractModel, VersionString
from agent_improvement_lab.serialization import stable_json_dumps


class CoverageGate(StrEnum):
    """Review gate attached to one covered requirement."""

    REQUIRED = "required"
    SOFT = "soft"
    HARD = "hard"


class CoverageRequirement(ContractModel):
    """One requirement-to-evaluator mapping."""

    requirement_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evaluator_ids: tuple[str, ...] = Field(min_length=1)
    evidence_fields: tuple[str, ...] = Field(min_length=1)
    case_ids: tuple[str, ...] = Field(min_length=1)
    gate: CoverageGate = CoverageGate.SOFT
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_unique_values(self) -> "CoverageRequirement":
        if len(self.evaluator_ids) != len(set(self.evaluator_ids)):
            raise ValueError("evaluator_ids must be unique within a requirement")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("case_ids must be unique within a requirement")
        return self


class CoverageMatrix(ContractModel):
    """Versioned artifact that links requirements to evaluator evidence."""

    matrix_id: str = Field(min_length=1)
    version: VersionString
    requirements: tuple[CoverageRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_requirement_ids(self) -> "CoverageMatrix":
        requirement_ids = [requirement.requirement_id for requirement in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Coverage requirement IDs must be unique")
        return self


class CoverageValidationError(ValueError):
    """Raised when a coverage artifact references unknown records."""


def validate_coverage_matrix(
    matrix: CoverageMatrix,
    *,
    evaluator_ids: Iterable[str] = (),
    case_ids: Iterable[str] = (),
) -> CoverageMatrix:
    """Validate evaluator and case references against the active run."""

    known_evaluators = set(evaluator_ids)
    known_cases = set(case_ids)
    unknown_evaluators = sorted(
        {
            evaluator_id
            for requirement in matrix.requirements
            for evaluator_id in requirement.evaluator_ids
            if known_evaluators and evaluator_id not in known_evaluators
        }
    )
    unknown_cases = sorted(
        {
            case_id
            for requirement in matrix.requirements
            for case_id in requirement.case_ids
            if known_cases and case_id not in known_cases
        }
    )
    errors: list[str] = []
    if unknown_evaluators:
        errors.append(f"Unknown evaluator IDs: {', '.join(unknown_evaluators)}")
    if unknown_cases:
        errors.append(f"Unknown case IDs: {', '.join(unknown_cases)}")
    if errors:
        raise CoverageValidationError("; ".join(errors))
    return matrix


def load_coverage_matrix(path: str | Path) -> CoverageMatrix:
    """Load and validate a JSON or YAML coverage artifact."""

    source = Path(path)
    if source.suffix.casefold() not in {".json", ".yaml", ".yml"}:
        raise CoverageValidationError("Coverage artifacts must use JSON or YAML")
    try:
        raw = source.read_text(encoding="utf-8")
        data = json.loads(raw) if source.suffix.casefold() == ".json" else yaml.safe_load(raw)
        return CoverageMatrix.model_validate(data)
    except (OSError, json.JSONDecodeError, yaml.YAMLError, ValidationError) as exc:
        raise CoverageValidationError(f"Invalid coverage artifact {source}: {exc}") from exc


def coverage_matrix_to_json(matrix: CoverageMatrix) -> str:
    """Return a stable JSON artifact."""

    return stable_json_dumps(matrix)
