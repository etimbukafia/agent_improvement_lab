"""Evaluation case and aggregate report contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field, model_validator

from enterprise_agent_improvement_lab.contracts.cases import DatasetSplit, RiskLevel
from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    VersionString,
    require_aware_utc,
)
from enterprise_agent_improvement_lab.contracts.failures import (
    EvaluationFailure,
    EvaluationScore,
    FailureCategory,
)
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTrace


class EnterpriseCaseEvaluationResult(ContractModel):
    """Safe result metadata for one enterprise case repetition."""

    case_id: str = Field(min_length=1)
    repeat_index: int = Field(default=0, ge=0)
    split: DatasetSplit
    risk: RiskLevel
    trace_id: str | None = None
    score_ids: tuple[str, ...] = ()
    failure_ids: tuple[str, ...] = ()
    passed: bool
    mean_score: float = Field(ge=0.0, le=1.0)
    failure_categories: tuple[FailureCategory, ...] = ()
    dimensions: tuple[str, ...] = ()
    task_duration_ms: int = Field(default=0, ge=0)
    total_duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_result(self) -> "EnterpriseCaseEvaluationResult":
        for name, values in (
            ("score_ids", self.score_ids),
            ("failure_ids", self.failure_ids),
            ("dimensions", self.dimensions),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique values")
        return self


class EnterpriseAggregateGroup(ContractModel):
    """One deterministic aggregate for an enterprise report dimension."""

    dimension: str = Field(min_length=1)
    key: str = Field(min_length=1)
    case_count: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    mean_score: float = Field(ge=0.0, le=1.0)
    failure_count: int = Field(ge=0)
    failure_categories: tuple[FailureCategory, ...] = ()

    @model_validator(mode="after")
    def validate_counts(self) -> "EnterpriseAggregateGroup":
        if self.passed_count > self.case_count or self.failure_count > self.case_count:
            raise ValueError("enterprise aggregate counts cannot exceed case_count")
        return self


class EnterpriseEvaluationReport(ContractModel):
    """Typed report emitted by an enterprise evaluation runner."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    run_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: VersionString
    candidate_id: str = Field(min_length=1)
    repeat_count: int = Field(default=1, ge=1)
    evaluator_ids: tuple[str, ...] = ()
    case_results: tuple[EnterpriseCaseEvaluationResult, ...] = Field(min_length=1)
    scores: tuple[EvaluationScore, ...] = ()
    failures: tuple[EvaluationFailure, ...] = ()
    traces: tuple[ExecutionTrace, ...] = ()
    aggregates: tuple[EnterpriseAggregateGroup, ...] = ()
    environment_snapshot_id: str | None = Field(default=None, min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_report(self) -> "EnterpriseEvaluationReport":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        if len(self.evaluator_ids) != len(set(self.evaluator_ids)):
            raise ValueError("Evaluator IDs must be unique in an enterprise report")
        score_ids = {score.score_id for score in self.scores}
        referenced_scores = {
            score_id for result in self.case_results for score_id in result.score_ids
        }
        if not referenced_scores.issubset(score_ids):
            raise ValueError("Enterprise case results reference unknown score IDs")
        failure_ids = {failure.failure_id for failure in self.failures}
        referenced_failures = {
            failure_id for result in self.case_results for failure_id in result.failure_ids
        }
        if not referenced_failures.issubset(failure_ids):
            raise ValueError("Enterprise case results reference unknown failure IDs")
        trace_ids = {trace.trace_id for trace in self.traces}
        referenced_traces = {result.trace_id for result in self.case_results if result.trace_id}
        if not referenced_traces.issubset(trace_ids):
            raise ValueError("Enterprise case results reference unknown trace IDs")
        return self
