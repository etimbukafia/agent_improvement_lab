from datetime import datetime

import pytest
from pydantic import ValidationError

from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifact,
    CandidateArtifactKind,
)
from enterprise_agent_improvement_lab.contracts.cases import DatasetVersion
from enterprise_agent_improvement_lab.contracts.sessions import SessionSummary
from enterprise_agent_improvement_lab.contracts.traces import ExecutionTraceSummary


def test_dataset_rejects_duplicate_case_ids(dataset, case):
    with pytest.raises(ValidationError, match="Duplicate case IDs"):
        DatasetVersion(
            dataset_id=dataset.dataset_id,
            version=dataset.version,
            description=dataset.description,
            cases=(case, case.model_copy(update={"input": {"question": "different"}})),
            provenance=dataset.provenance,
            created_at=dataset.created_at,
        )


def test_dataset_rejects_case_from_another_version(dataset, case):
    wrong_case = case.model_copy(update={"dataset_version": "2.0.0"})
    with pytest.raises(ValidationError, match="wrong dataset or version"):
        DatasetVersion(
            dataset_id=dataset.dataset_id,
            version=dataset.version,
            description=dataset.description,
            cases=(wrong_case,),
            provenance=dataset.provenance,
            created_at=dataset.created_at,
        )


def test_candidate_artifact_calculates_and_protects_checksum(artifact):
    assert len(artifact.content_sha256) == 64
    with pytest.raises(ValidationError, match="does not match content"):
        CandidateArtifact(
            artifact_id="prompt-2",
            name="bad",
            version="1.0.0",
            kind=CandidateArtifactKind.SYSTEM_PROMPT,
            content="Answer clearly.",
            content_sha256="0" * 64,
            created_at=artifact.created_at,
        )


def test_candidate_artifact_rejects_naive_timestamp():
    with pytest.raises(ValidationError, match="UTC offset"):
        CandidateArtifact(
            artifact_id="prompt-3",
            name="naive",
            version="1.0.0",
            kind=CandidateArtifactKind.SYSTEM_PROMPT,
            content="Answer clearly.",
            created_at=datetime(2026, 8, 23),
        )


def test_execution_trace_summary_requires_only_safe_fields(created_at):
    summary = ExecutionTraceSummary(
        execution_id="execution-1",
        agent_id="agent-1",
        agent_version="1.0.0",
        candidate_id="candidate-1",
        trigger_kind="message",
        case_id="case-1",
        started_at=created_at,
    )
    dumped = summary.model_dump()
    assert "input_text" not in dumped
    assert "result_summary" not in dumped
    with pytest.raises(ValidationError):
        ExecutionTraceSummary(
            execution_id="execution-2",
            agent_id="agent-1",
            agent_version="1.0.0",
            candidate_id="candidate-1",
            trigger_kind="message",
            case_id="case-1",
            started_at=created_at,
            input_text="secret prompt",
        )


def test_session_summary_requires_only_safe_fields(created_at):
    summary = SessionSummary(session_id="session-1", started_at=created_at)
    dumped = summary.model_dump()
    assert "input_text" not in dumped
    assert "tool_result" not in dumped
    with pytest.raises(ValidationError):
        SessionSummary(
            session_id="session-2",
            started_at=created_at,
            tool_result="secret result",
        )
