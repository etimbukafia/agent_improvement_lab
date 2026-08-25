"""Deterministic candidate generator for the calculator example."""

from __future__ import annotations

from agent_improvement_lab.contracts.candidates import (
    ArtifactEdit,
    CandidateGenerationRequest,
    GeneratedCandidatePlan,
)


class CalculatorCandidateGenerator:
    """Turn a confirmed tool-selection failure into one prompt edit."""

    def generate(self, request: CandidateGenerationRequest) -> GeneratedCandidatePlan:
        artifact = request.current_artifacts[0]
        return GeneratedCandidatePlan(
            rationale="Tell the agent to use the calculator tool for every expression.",
            change_summary={"behavior": "Use the calculator tool before answering."},
            artifact_edits=(
                ArtifactEdit(
                    base_artifact_id=artifact.artifact_id,
                    content=(
                        "Use the calculator tool for every arithmetic expression. "
                        "Report its result."
                    ),
                    change_paths=("$",),
                ),
            ),
            generator_id=request.generator_id,
        )


__all__ = ["CalculatorCandidateGenerator"]
