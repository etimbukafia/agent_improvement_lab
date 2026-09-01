"""Bounded enterprise candidate builder for the calculator example."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from enterprise_agent_improvement_lab.candidate_builders import (
    CandidateBuildResult,
    PromptCandidateBuilder,
)
from enterprise_agent_improvement_lab.contracts.candidates import (
    CandidateArtifact,
    EnterpriseAgentCandidate,
    ImprovementScope,
)


class CalculatorCandidateGenerator:
    """Create the one prompt change allowed by the calculator demo scope."""

    builder = PromptCandidateBuilder()

    def build(
        self,
        parent_candidate: EnterpriseAgentCandidate,
        scope: ImprovementScope,
        *,
        base_artifact: CandidateArtifact,
        source_failure_ids: Sequence[str],
        candidate_id: str = "calculator-candidate",
        candidate_version: str = "1.1.0",
        created_at: datetime | None = None,
    ) -> CandidateBuildResult:
        """Build a typed candidate from confirmed failure evidence."""

        return self.builder.build(
            parent_candidate,
            scope,
            source_failure_ids=source_failure_ids,
            base_artifact=base_artifact,
            replacement_content=(
                "Use the calculator tool for every arithmetic expression. Report its result."
            ),
            candidate_id=candidate_id,
            candidate_version=candidate_version,
            created_at=created_at,
            rationale="Tell the agent to use the calculator tool for every expression.",
            expected_effect="The agent selects the calculator tool before answering arithmetic.",
        )


__all__ = ["CalculatorCandidateGenerator"]
