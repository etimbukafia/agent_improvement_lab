"""Risk-aware promotion contracts.

These contracts describe evidence and reviewer requirements.  They do not
make the human promotion decision.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AliasChoices, ConfigDict, Field, model_validator

from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    VersionString,
    require_aware_utc,
    utc_now,
)
from enterprise_agent_improvement_lab.contracts.experiments import ComponentChange


class RiskClass(StrEnum):
    """Operational risk classes used to select a promotion profile."""

    READ_ONLY_INFORMATIONAL = "read_only_informational"
    INTERNAL_PRODUCTIVITY = "internal_productivity"
    EXTERNAL_CUSTOMER_INTERACTION = "external_customer_interaction"
    WRITE_CAPABLE_OPERATIONAL = "write_capable_operational"
    FINANCIAL = "financial"
    REGULATED = "regulated"
    SECURITY_SENSITIVE = "security_sensitive"

    # Short names are kept as aliases for callers that use the plan's
    # vocabulary while the serialized values remain unambiguous.
    READ_ONLY = READ_ONLY_INFORMATIONAL
    EXTERNAL_CUSTOMER = EXTERNAL_CUSTOMER_INTERACTION


class PromotionEvidenceKind(StrEnum):
    """Evidence families that a promotion profile may require."""

    QUALITY = "quality"
    SECURITY = "security"
    AUTHORIZATION = "authorization"
    TENANT_BOUNDARY = "tenant_boundary"
    APPROVAL_BOUNDARY = "approval_boundary"
    STATE_INTEGRITY = "state_integrity"
    PROHIBITED_ACTION = "prohibited_action"
    POLICY = "policy"
    WORKFLOW = "workflow"
    BUSINESS_OUTCOME = "business_outcome"
    HOLDOUT = "holdout"
    COST = "cost"
    LATENCY = "latency"
    TOKEN_USAGE = "token_usage"
    TOOL_SIDE_EFFECT = "tool_side_effect"
    DELEGATION = "delegation"
    RELIABILITY = "reliability"

    APPROVAL = APPROVAL_BOUNDARY
    BUSINESS_OUTCOMES = BUSINESS_OUTCOME


class RequiredEvidence(ContractModel):
    """One evidence requirement in a promotion profile."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    evidence_id: str = Field(min_length=1)
    evidence_type: PromotionEvidenceKind = Field(
        validation_alias=AliasChoices("evidence_type", "evidence_kind", "kind", "type")
    )
    description: str = Field(min_length=1)
    required: bool = True
    hard: bool = True
    minimum_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    minimum_count: int = Field(default=1, ge=1)


class RequiredReviewerRole(ContractModel):
    """One reviewer role required before a risky candidate is promotable."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    role: str = Field(
        min_length=1,
        validation_alias=AliasChoices("role", "reviewer_role", "role_id"),
    )
    description: str = Field(default="Required independent review.", min_length=1)
    required: bool = True


class PromotionProfile(ContractModel):
    """Profile-driven promotion requirements for one risk class."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    profile_id: str = Field(min_length=1)
    version: VersionString = "1.0.0"
    risk_class: RiskClass = Field(
        validation_alias=AliasChoices("risk_class", "risk", "risk_classification")
    )
    required_evidence: tuple[RequiredEvidence, ...] = ()
    required_reviewer_roles: tuple[RequiredReviewerRole, ...] = ()
    require_holdout: bool = False
    minimum_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    allow_human_override: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_profile(self) -> "PromotionProfile":
        evidence_ids = [item.evidence_id for item in self.required_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Promotion evidence IDs must be unique")
        roles = [item.role for item in self.required_reviewer_roles]
        if len(roles) != len(set(roles)):
            raise ValueError("Promotion reviewer roles must be unique")
        return self

    @classmethod
    def for_risk_class(
        cls,
        risk_class: RiskClass,
        *,
        profile_id: str | None = None,
    ) -> "PromotionProfile":
        """Return the deterministic default profile for a risk class."""

        return default_promotion_profile(risk_class, profile_id=profile_id)


class PromotionEvidence(ContractModel):
    """Observed evidence supplied to a risk-aware promotion evaluation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    evidence_id: str = Field(min_length=1)
    evidence_type: PromotionEvidenceKind = Field(
        validation_alias=AliasChoices("evidence_type", "evidence_kind", "kind", "type")
    )
    passed: bool
    summary: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    observed: float | int | str | bool | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_evidence(self) -> "PromotionEvidence":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("Promotion evidence references must be unique")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("Promotion evidence references must be non-empty")
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


class RiskAwarePromotionEvaluation(ContractModel):
    """Computed eligibility that must still receive a human decision."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    evaluation_id: str | None = Field(default=None, min_length=1)
    candidate_id: str = Field(min_length=1)
    comparison_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    risk_class: RiskClass
    evidence: tuple[PromotionEvidence, ...] = ()
    missing_evidence_ids: tuple[str, ...] = ()
    failed_evidence_ids: tuple[str, ...] = ()
    reviewer_roles: tuple[str, ...] = ()
    missing_reviewer_roles: tuple[str, ...] = ()
    baseline_candidate_id: str | None = Field(default=None, min_length=1)
    baseline_manifest_id: str | None = Field(default=None, min_length=1)
    candidate_manifest_id: str | None = Field(default=None, min_length=1)
    baseline_manifest_digest: str | None = Field(default=None, min_length=1)
    candidate_manifest_digest: str | None = Field(default=None, min_length=1)
    baseline_environment_snapshot_id: str | None = Field(default=None, min_length=1)
    candidate_environment_snapshot_id: str | None = Field(default=None, min_length=1)
    component_changes: tuple[ComponentChange, ...] = ()
    component_change_refs: tuple[str, ...] = ()
    eligible: bool
    human_decision_required: bool = True
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_evaluation(self) -> "RiskAwarePromotionEvaluation":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Promotion evidence IDs must be unique in an evaluation")
        for name, values in (
            ("missing_evidence_ids", self.missing_evidence_ids),
            ("failed_evidence_ids", self.failed_evidence_ids),
            ("reviewer_roles", self.reviewer_roles),
            ("missing_reviewer_roles", self.missing_reviewer_roles),
            ("component_change_refs", self.component_change_refs),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty values")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique values")
        component_keys = [
            (change.component_type, change.component_id, change.relationship)
            for change in self.component_changes
        ]
        if len(component_keys) != len(set(component_keys)):
            raise ValueError("component changes must identify unique relationships")
        if self.eligible and (
            self.missing_evidence_ids or self.failed_evidence_ids or self.missing_reviewer_roles
        ):
            raise ValueError("Eligible promotion evaluation cannot have failed requirements")
        if not self.human_decision_required:
            raise ValueError("A risk-aware promotion evaluation always requires human decision")
        if self.evaluation_id is None:
            object.__setattr__(
                self,
                "evaluation_id",
                f"promotion-evaluation:{self.candidate_id}:{self.comparison_id}:{self.profile_id}",
            )
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self


RequiredEvidenceType = PromotionEvidenceKind
ReviewerRole = RequiredReviewerRole


def default_promotion_profile(
    risk_class: RiskClass,
    *,
    profile_id: str | None = None,
) -> PromotionProfile:
    """Return deterministic baseline requirements for a risk class."""

    common = [
        RequiredEvidence(
            evidence_id="quality",
            evidence_type=PromotionEvidenceKind.QUALITY,
            description="Quality evidence is available and passes.",
        ),
        RequiredEvidence(
            evidence_id="security",
            evidence_type=PromotionEvidenceKind.SECURITY,
            description="No security regression is present.",
        ),
    ]
    reviewers: list[RequiredReviewerRole] = []
    require_holdout = False
    if risk_class in {
        RiskClass.READ_ONLY_INFORMATIONAL,
        RiskClass.EXTERNAL_CUSTOMER_INTERACTION,
        RiskClass.WRITE_CAPABLE_OPERATIONAL,
        RiskClass.FINANCIAL,
        RiskClass.REGULATED,
    }:
        common.append(
            RequiredEvidence(
                evidence_id="holdout",
                evidence_type=PromotionEvidenceKind.HOLDOUT,
                description="Holdout evidence is available and non-declining.",
            )
        )
        require_holdout = True
    if risk_class in {
        RiskClass.WRITE_CAPABLE_OPERATIONAL,
        RiskClass.FINANCIAL,
        RiskClass.REGULATED,
        RiskClass.SECURITY_SENSITIVE,
    }:
        common.extend(
            (
                RequiredEvidence(
                    evidence_id="authorization",
                    evidence_type=PromotionEvidenceKind.AUTHORIZATION,
                    description="Authorization boundaries pass for every checked action.",
                    minimum_pass_rate=1.0,
                ),
                RequiredEvidence(
                    evidence_id="approval-boundary",
                    evidence_type=PromotionEvidenceKind.APPROVAL_BOUNDARY,
                    description="Approval boundaries pass for every checked action.",
                    minimum_pass_rate=1.0,
                ),
                RequiredEvidence(
                    evidence_id="state-integrity",
                    evidence_type=PromotionEvidenceKind.STATE_INTEGRITY,
                    description="No prohibited or invalid state mutation is present.",
                    minimum_pass_rate=1.0,
                ),
            )
        )
        reviewers.append(
            RequiredReviewerRole(
                role="security_reviewer",
                description="Independent security review is required.",
            )
        )
    if risk_class in {RiskClass.FINANCIAL, RiskClass.REGULATED}:
        reviewers.append(
            RequiredReviewerRole(
                role="business_owner",
                description="The accountable business owner must review the evidence.",
            )
        )
    if risk_class == RiskClass.SECURITY_SENSITIVE:
        reviewers.append(
            RequiredReviewerRole(
                role="security_reviewer",
                description="Independent security review is required.",
            )
        )
    # Keep the profile valid when the same role was selected by two rules.
    unique_roles = tuple({role.role: role for role in reviewers}.values())
    return PromotionProfile(
        profile_id=profile_id or f"promotion:{risk_class.value}",
        risk_class=risk_class,
        required_evidence=tuple(common),
        required_reviewer_roles=unique_roles,
        require_holdout=require_holdout,
    )


__all__ = [
    "PromotionEvidence",
    "PromotionEvidenceKind",
    "PromotionProfile",
    "RequiredEvidence",
    "RequiredEvidenceType",
    "RequiredReviewerRole",
    "ReviewerRole",
    "RiskAwarePromotionEvaluation",
    "RiskClass",
    "default_promotion_profile",
]
