from datetime import datetime, timezone

import pytest

from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifact,
    CandidateArtifactKind,
    CandidateArtifactReference,
    EnterpriseAgentCandidate,
)
from enterprise_agent_improvement_lab.contracts.cases import (
    CaseProvenance,
    DatasetSplit,
    DatasetVersion,
    EnterpriseEvaluationCase,
    OutputExpectation,
    RiskLevel,
)
from enterprise_agent_improvement_lab.contracts.experiments import (
    ExperimentRun,
    RunManifest,
    RunStatus,
)

UTC = timezone.utc


@pytest.fixture
def created_at() -> datetime:
    return datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def provenance(created_at: datetime) -> CaseProvenance:
    return CaseProvenance(source="test", source_ref="fixture-1", collected_at=created_at)


@pytest.fixture
def artifact(created_at: datetime) -> CandidateArtifact:
    return CandidateArtifact(
        artifact_id="prompt-1",
        name="baseline",
        version="1.0.0",
        kind=CandidateArtifactKind.SYSTEM_PROMPT,
        content="Answer clearly.",
        created_at=created_at,
    )


@pytest.fixture
def candidate(artifact: CandidateArtifact, created_at: datetime) -> EnterpriseAgentCandidate:
    return EnterpriseAgentCandidate(
        candidate_id="candidate-1",
        agent_id="agent-1",
        name="baseline",
        version="1.0.0",
        artifacts=(CandidateArtifactReference.from_artifact(artifact),),
        lineage={"parent_candidate_id": None},
        created_at=created_at,
    )


@pytest.fixture
def case(provenance: CaseProvenance) -> EnterpriseEvaluationCase:
    return EnterpriseEvaluationCase(
        case_id="case-1",
        dataset_id="demo",
        dataset_version="1.0.0",
        split=DatasetSplit.DEVELOPMENT,
        risk=RiskLevel.LOW,
        input={"question": "What is two plus two?"},
        input_text="What is two plus two?",
        expected_outputs=(
            OutputExpectation(output_id="answer", path="answer", expected_value="4"),
        ),
        provenance=provenance,
    )


@pytest.fixture
def dataset(
    case: EnterpriseEvaluationCase,
    provenance: CaseProvenance,
    created_at: datetime,
) -> DatasetVersion:
    return DatasetVersion(
        dataset_id="demo",
        version="1.0.0",
        description="Small test dataset",
        cases=(case,),
        provenance=provenance,
        created_at=created_at,
    )


@pytest.fixture
def experiment(created_at: datetime) -> ExperimentRun:
    manifest = RunManifest(
        run_id="run-1",
        dataset_id="demo",
        dataset_version="1.0.0",
        candidate_id="candidate-1",
        runtime_name="fake-runtime",
        runtime_version="1.0.0",
        provider="fake",
        model="fake-1",
        seed=7,
        created_at=created_at,
    )
    return ExperimentRun(
        run_id="run-1",
        experiment_id="experiment-1",
        manifest=manifest,
        status=RunStatus.COMPLETED,
        started_at=created_at,
        ended_at=created_at,
    )
