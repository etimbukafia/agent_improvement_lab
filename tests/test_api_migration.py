"""Public API migration checks for the enterprise contract surface."""

import importlib

import pytest

import enterprise_agent_improvement_lab as lab


def test_enterprise_contracts_are_the_public_runtime_surface() -> None:
    for name in (
        "ExecutionTrace",
        "EnterpriseAgentCandidate",
        "EnterpriseEvaluationCase",
        "EnterpriseEvaluationRunner",
        "CandidateArtifact",
        "CandidateComponentReference",
        "ChangeKind",
        "ImprovementScope",
    ):
        assert hasattr(lab, name)

    for removed_name in (
        "AgentTrace",
        "AgentCandidate",
        "EvaluationCaseRef",
        "PydanticEvalsRunner",
        "ComparisonRunner",
        "PromptArtifact",
        "PromptArtifactKind",
        "CandidateScope",
        "CandidateArtifactRef",
        "ChangeType",
        "EnterpriseDatasetVersion",
        "EventType",
    ):
        assert not hasattr(lab, removed_name)


@pytest.mark.parametrize(
    "module_name",
    ("enterprise_agent_improvement_lab.candidates", "enterprise_agent_improvement_lab.runner"),
)
def test_removed_legacy_modules_are_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
