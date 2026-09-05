"""Small Skill-versus-Tool candidate example.

The payment tools already exist.  The candidate adds a reusable duplicate
charge skill and keeps the agent's executable tool authority unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from enterprise_agent_improvement_lab import (
    CandidateArtifact,
    CandidateArtifactKind,
    ChangeKind,
    EnterpriseAgentCandidate,
    ImprovementScope,
    SkillCandidateBuilder,
)


def build_skill_candidate() -> tuple[EnterpriseAgentCandidate, CandidateArtifact]:
    """Return a candidate that adds a skill but no new payment tool."""

    created_at = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    prompt = CandidateArtifact(
        artifact_id="payment-prompt-v1",
        name="payment prompt",
        version="1.0.0",
        kind=CandidateArtifactKind.SYSTEM_PROMPT,
        content="Use approved payment controls and report verified outcomes.",
        registry_reference="prompt:payment-prompt@1.0.0",
        created_at=created_at,
    )
    parent = EnterpriseAgentCandidate(
        candidate_id="payment-baseline",
        agent_id="payment-agent",
        version="1.0.0",
        artifacts=(prompt.to_reference(),),
        prompt_ref=prompt.to_component_reference(),
        tool_refs=(
            "tool:payments.charge@1.0.0",
            "tool:payments.lookup@1.0.0",
        ),
        rationale="Payment tools are already approved and available.",
        created_at=created_at,
    )
    scope = ImprovementScope(
        scope_id="payment-skill-scope",
        allowed_change_kinds=(ChangeKind.SKILL_ADDITION,),
        allowed_agents=(parent.agent_id,),
        allowed_skills=("duplicate-charge",),
    )
    result = SkillCandidateBuilder().build(
        parent,
        scope,
        source_failure_ids=("failure:duplicate-charge",),
        target_id="duplicate-charge",
        target_version="1.0.0",
        target_registry_reference="skill:duplicate-charge@1.0.0",
        replacement_content=json.dumps(
            {
                "skill_id": "duplicate-charge",
                "version": "1.0.0",
                "name": "Duplicate charge prevention",
                "description": "Detect and prevent duplicate payment charges.",
                "supported_operations": ["charge"],
                "required_tool_refs": ["tool:payments.charge@1.0.0"],
            },
            sort_keys=True,
        ),
        created_at=created_at,
    )
    return result.candidate, next(
        artifact
        for artifact in result.artifacts
        if artifact.kind == CandidateArtifactKind.SKILL_CONFIGURATION
    )


if __name__ == "__main__":
    candidate, skill = build_skill_candidate()
    print(
        json.dumps(
            {
                "candidate_id": candidate.candidate_id,
                "skill_refs": [reference.identity for reference in candidate.skill_refs],
                "tool_refs": [reference.identity for reference in candidate.tool_refs],
                "skill_artifact": skill.registry_reference,
            },
            indent=2,
        )
    )
