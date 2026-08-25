"""Evaluation case and aggregate report contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from agent_improvement_lab.contracts.cases import DatasetSplit, RiskLevel
from agent_improvement_lab.contracts.common import ContractModel, VersionString, require_aware_utc
from agent_improvement_lab.contracts.failures import EvaluationScore, FailureCategory


class CaseEvaluationResult(ContractModel):
    """Safe result metadata for one case repetition."""

    case_id: str = Field(min_length=1)
    repeat_index: int = Field(ge=0)
    split: DatasetSplit
    risk: RiskLevel
    tags: tuple[str, ...] = ()
    workflow: str = Field(min_length=1)
    trace_id: str | None = None
    score_ids: tuple[str, ...] = ()
    passed: bool
    mean_score: float = Field(ge=0.0, le=1.0)
    failure_categories: tuple[FailureCategory, ...] = ()
    task_duration_ms: int = Field(default=0, ge=0)
    total_duration_ms: int = Field(default=0, ge=0)


class AggregateGroup(ContractModel):
    """One deterministic aggregate grouped by a report dimension."""

    dimension: str = Field(min_length=1)
    key: str = Field(min_length=1)
    case_count: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    mean_score: float = Field(ge=0.0, le=1.0)
    failure_count: int = Field(ge=0)
    failure_categories: tuple[FailureCategory, ...] = ()

    @model_validator(mode="after")
    def validate_counts(self) -> "AggregateGroup":
        if self.passed_count > self.case_count:
            raise ValueError("passed_count cannot exceed case_count")
        if self.failure_count > self.case_count:
            raise ValueError("failure_count cannot exceed case_count")
        return self


class LabEvaluationReport(ContractModel):
    """Complete safe report for one Pydantic Evals experiment."""

    run_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: VersionString
    candidate_id: str = Field(min_length=1)
    pydantic_report_name: str = Field(min_length=1)
    repeat_count: int = Field(ge=1)
    evaluator_ids: tuple[str, ...] = ()
    case_results: tuple[CaseEvaluationResult, ...] = Field(min_length=1)
    scores: tuple[EvaluationScore, ...] = ()
    aggregates: tuple[AggregateGroup, ...] = ()
    runtime_failures: tuple[str, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def validate_score_references(self) -> "LabEvaluationReport":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        score_ids = [score.score_id for score in self.scores]
        if len(score_ids) != len(set(score_ids)):
            raise ValueError("Score IDs must be unique in a report")
        known_score_ids = set(score_ids)
        unknown = sorted(
            {
                score_id
                for result in self.case_results
                for score_id in result.score_ids
                if score_id not in known_score_ids
            }
        )
        if unknown:
            raise ValueError(f"Case results reference unknown score IDs: {', '.join(unknown)}")
        if len(self.evaluator_ids) != len(set(self.evaluator_ids)):
            raise ValueError("Evaluator IDs must be unique in a report")
        return self
