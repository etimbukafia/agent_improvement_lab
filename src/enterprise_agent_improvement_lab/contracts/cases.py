"""Dataset and evaluation-case contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    VersionString,
    require_aware_utc,
)
from enterprise_agent_improvement_lab.contracts.traces import ApprovalDecision, TriggerInfo


class DatasetSplit(StrEnum):
    """Supported evaluation dataset splits."""

    SMOKE = "smoke"
    DEVELOPMENT = "development"
    REGRESSION = "regression"
    HOLDOUT = "holdout"
    SECURITY = "security"


class RiskLevel(StrEnum):
    """Risk attached to an evaluation case."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseProvenance(ContractModel):
    """Source information for a case or dataset."""

    source: str = Field(min_length=1)
    source_ref: str | None = None
    collected_at: datetime | None = None
    reviewer: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def normalize_timestamp(self) -> "CaseProvenance":
        if self.collected_at is not None:
            object.__setattr__(self, "collected_at", require_aware_utc(self.collected_at))
        return self


class NumericRange(ContractModel):
    """Inclusive or exclusive numeric bounds for one tool argument."""

    minimum: float | None = None
    maximum: float | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    @model_validator(mode="after")
    def validate_bounds(self) -> "NumericRange":
        if self.minimum is None and self.maximum is None:
            raise ValueError("NumericRange needs a minimum or maximum")
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError("minimum must not be greater than maximum")
            if self.minimum == self.maximum and not (
                self.minimum_inclusive and self.maximum_inclusive
            ):
                raise ValueError("Equal bounds must both be inclusive")
        return self


class OutputExpectation(ContractModel):
    """One typed expectation for an output value or output path."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    output_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("output_id", "id", "name"),
    )
    path: str = Field(default="$", min_length=1)
    expected_value: Any = Field(
        default=None,
        validation_alias=AliasChoices("expected_value", "expected", "value"),
    )
    operator: str = Field(default="equals", min_length=1)
    evidence_refs: tuple[str, ...] = ()
    description: str | None = None

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _validate_unique_strings("evidence_refs", value)
        return value

    @property
    def expected(self) -> Any:
        """Return the expected value using the legacy-friendly name."""

        return self.expected_value


class ActionExpectation(ContractModel):
    """One typed expected, optional, required, or prohibited action."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    action: str = Field(
        min_length=1,
        validation_alias=AliasChoices("action", "name", "action_name", "operation", "tool_name"),
    )
    action_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("action_id", "expectation_id"),
    )
    action_type: str = Field(
        default="action",
        min_length=1,
        validation_alias=AliasChoices("action_type", "kind", "type"),
    )
    target: str | None = Field(default=None, min_length=1)
    order: int | None = Field(default=None, ge=0)
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("arguments", "expected_arguments", "parameters"),
    )
    expected_result: Any = Field(
        default=None,
        validation_alias=AliasChoices("expected_result", "result"),
    )
    evidence_refs: tuple[str, ...] = ()
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_action_id_as_action(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        action_keys = {"action", "name", "action_name", "operation", "tool_name"}
        if not action_keys.intersection(data) and isinstance(data.get("action_id"), str):
            data["action"] = data["action_id"]
        return data

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _validate_unique_strings("evidence_refs", value)
        return value

    @property
    def name(self) -> str:
        """Return the action name using the legacy-friendly name."""

        return self.action

    @property
    def identity(self) -> str:
        """Return the stable identity of this action expectation."""

        if self.action_id is not None:
            return self.action_id
        target = self.target or "*"
        order = "*" if self.order is None else str(self.order)
        return f"{self.action_type}:{self.action}:{target}:{order}"

    @property
    def conflict_key(self) -> str:
        """Return the action identity used for required/prohibited conflicts."""

        return self.action.casefold()


class StateExpectation(ContractModel):
    """One typed expectation for a state path."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("path", "state_path", "state_key", "key"),
    )
    expected_value: Any = Field(
        default=None,
        validation_alias=AliasChoices("expected_value", "expected", "value"),
    )
    operator: str = Field(default="equals", min_length=1)
    scope: str = Field(
        default="final",
        min_length=1,
        validation_alias=AliasChoices("scope", "state_scope"),
    )
    evidence_refs: tuple[str, ...] = ()
    description: str | None = None

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _validate_unique_strings("evidence_refs", value)
        return value

    @property
    def state_path(self) -> str:
        """Return the state path using the explicit name."""

        return self.path

    @property
    def expected(self) -> Any:
        """Return the expected state value."""

        return self.expected_value


class ApprovalExpectation(ContractModel):
    """One typed approval requirement for an action or policy."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    approval_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("approval_id", "id", "name", "approval_name"),
    )
    action: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("action", "action_name", "operation"),
    )
    policy_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("policy_id", "approval_policy_id", "policy"),
    )
    decision: str = Field(default=ApprovalDecision.APPROVED.value, min_length=1)
    approver_role: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("approver_role", "role", "approver"),
    )
    order: int | None = Field(default=None, ge=0)
    evidence_refs: tuple[str, ...] = ()
    description: str | None = None

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _validate_unique_strings("evidence_refs", value)
        return value

    @model_validator(mode="after")
    def validate_approval(self) -> "ApprovalExpectation":
        if self.approval_id is None and self.action is None and self.policy_id is None:
            raise ValueError("An approval expectation needs an approval, action, or policy ID")
        return self

    @property
    def identity(self) -> str:
        """Return the stable identity of this approval expectation."""

        if self.approval_id is not None:
            return self.approval_id
        order = "*" if self.order is None else str(self.order)
        return f"{self.policy_id or '*'}:{self.action or '*'}:{order}"


class AuthorizationContext(ContractModel):
    """Typed authority available to an enterprise evaluation case."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    principal_id: str | None = Field(default=None, min_length=1)
    tenant_id: str | None = Field(default=None, min_length=1)
    roles: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_resources: tuple[str, ...] = ()
    policy_ids: tuple[str, ...] = ()

    @field_validator("roles", "allowed_tools", "allowed_resources", "policy_ids", mode="before")
    @classmethod
    def normalize_values(cls, value: object) -> tuple[str, ...]:
        return _normalize_string_sequence(value, "authorization values")


class InvariantExpectation(ContractModel):
    """One typed condition that must remain true during or after execution."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    invariant_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("invariant_id", "id", "name"),
    )
    description: str | None = None
    path: str | None = Field(
        default=None, min_length=1, validation_alias=AliasChoices("path", "state_path")
    )
    paths: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("paths", "state_paths"),
    )
    operator: str = Field(default="equals", min_length=1)
    expected_value: Any = Field(
        default=None,
        validation_alias=AliasChoices("expected_value", "expected", "value"),
    )
    prohibited_paths: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("paths", "prohibited_paths", "evidence_refs", mode="before")
    @classmethod
    def normalize_string_sequences(cls, value: object) -> tuple[str, ...]:
        return _normalize_string_sequence(value, "invariant paths")

    @model_validator(mode="after")
    def validate_invariant(self) -> "InvariantExpectation":
        paths = list(self.paths)
        if self.path is not None and self.path not in paths:
            paths.insert(0, self.path)
        _validate_unique_strings("paths", paths)
        _validate_unique_strings("prohibited_paths", self.prohibited_paths)
        _validate_unique_strings("evidence_refs", self.evidence_refs)
        if not paths and not self.prohibited_paths and not self.description:
            raise ValueError("An invariant expectation needs a state path or description")
        object.__setattr__(self, "paths", tuple(paths))
        return self

    @property
    def state_paths(self) -> tuple[str, ...]:
        """Return all state paths covered by this invariant."""

        return self.paths


class BusinessOutcomeExpectation(ContractModel):
    """One typed business result expected from an enterprise workflow."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    outcome_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("outcome_id", "id", "name", "outcome", "outcome_name"),
    )
    expected_value: Any = Field(
        default=None,
        validation_alias=AliasChoices("expected_value", "expected", "value"),
    )
    operator: str = Field(default="equals", min_length=1)
    required: bool = True
    state_path: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("state_path", "path", "observed_state_path"),
    )
    evidence_refs: tuple[str, ...] = ()
    description: str | None = None

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        _validate_unique_strings("evidence_refs", value)
        return value

    @property
    def outcome(self) -> str:
        """Return the business outcome name."""

        return self.outcome_id


class EvaluationBudget(ContractModel):
    """Optional deterministic limits for one evaluation case."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    max_duration_ms: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("max_duration_ms", "max_latency_ms"),
    )
    max_total_tokens: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("max_total_tokens", "max_tokens", "token_limit"),
    )
    max_cost: float | None = Field(
        default=None, ge=0.0, validation_alias=AliasChoices("max_cost", "cost_limit")
    )
    max_model_calls: int | None = Field(default=None, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_state_reads: int | None = Field(default=None, ge=0)
    max_state_mutations: int | None = Field(default=None, ge=0)
    max_delegations: int | None = Field(default=None, ge=0)
    max_events: int | None = Field(default=None, ge=0)

    @field_validator("max_cost")
    @classmethod
    def validate_cost(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("max_cost must be finite")
        return value


class FixtureReference(ContractModel):
    """Reference to a fixture version without executing or loading it."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    fixture_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("fixture_id", "id", "name"),
    )
    version: str = Field(default="unknown", min_length=1)
    kind: str = Field(default="data", min_length=1)
    reference: str | None = Field(
        default=None, min_length=1, validation_alias=AliasChoices("reference", "ref")
    )

    @property
    def identity(self) -> str:
        """Return the stable fixture identity."""

        return f"{self.fixture_id}@{self.version}"


class EnterpriseEvaluationCase(ContractModel):
    """Evaluation case for stateful, policy-aware enterprise behavior."""

    case_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: VersionString
    split: DatasetSplit
    risk: RiskLevel = Field(
        default=RiskLevel.MEDIUM,
        validation_alias=AliasChoices("risk", "risk_level"),
    )
    tags: tuple[str, ...] = ()
    trigger: TriggerInfo | None = None
    initial_state: dict[str, Any] = Field(default_factory=dict)
    fixtures: tuple[FixtureReference, ...] = ()
    input: dict[str, Any] | str | None = Field(
        default=None,
        validation_alias=AliasChoices("input", "payload", "event_input"),
    )
    input_text: str | None = Field(
        default=None, min_length=1, validation_alias=AliasChoices("input_text", "text")
    )
    expected_outputs: tuple[OutputExpectation, ...] = Field(
        default=(),
        validation_alias=AliasChoices("expected_outputs", "expected_output", "outputs", "expected"),
    )
    expected_actions: tuple[ActionExpectation, ...] = Field(
        default=(), validation_alias=AliasChoices("expected_actions", "actions")
    )
    optional_actions: tuple[ActionExpectation, ...] = ()
    required_actions: tuple[ActionExpectation, ...] = ()
    prohibited_actions: tuple[ActionExpectation, ...] = ()
    required_approvals: tuple[ApprovalExpectation, ...] = Field(
        default=(), validation_alias=AliasChoices("required_approvals", "approvals")
    )
    expected_final_state: tuple[StateExpectation, ...] = Field(
        default=(), validation_alias=AliasChoices("expected_final_state", "final_state")
    )
    state_invariants: tuple[InvariantExpectation, ...] = Field(
        default=(), validation_alias=AliasChoices("state_invariants", "invariants")
    )
    business_outcomes: tuple[BusinessOutcomeExpectation, ...] = Field(
        default=(), validation_alias=AliasChoices("business_outcomes", "outcomes")
    )
    budgets: EvaluationBudget | None = None
    tenant_context: dict[str, Any] | str | None = None
    authorization_context: AuthorizationContext | str | None = None
    security_context: dict[str, Any] | str | None = None
    policy_references: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("policy_references", "policy_refs", "policies")
    )
    provenance: CaseProvenance
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_action_shape(cls, value: object) -> object:
        """Read the old tool expectation shape without restoring its contract."""

        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "expected_actions" not in data and "actions" not in data:
            raw_expectations = data.get("tool_expectations")
            if raw_expectations is not None:
                if isinstance(raw_expectations, Mapping):
                    raw_expectations = (raw_expectations,)
                if isinstance(raw_expectations, Sequence) and not isinstance(
                    raw_expectations, (str, bytes)
                ):
                    actions: list[dict[str, Any]] = []
                    for raw in raw_expectations:
                        if not isinstance(raw, Mapping):
                            raise ValueError("tool_expectations must contain mappings")
                        source = dict(raw)
                        action: dict[str, Any] = {
                            "action": source.get("name", source.get("tool_name")),
                            "action_type": "tool_call",
                            "order": source.get("order"),
                            "arguments": dict(source.get("exact_arguments", {})),
                        }
                        required = source.get("required_arguments", ())
                        if required:
                            action["arguments"] = {
                                **action["arguments"],
                                "__required_arguments__": tuple(required),
                            }
                        protected = source.get("protected_arguments", ())
                        if protected:
                            action["arguments"] = {
                                **action["arguments"],
                                "__protected_arguments__": tuple(protected),
                            }
                        for key in (
                            "argument_types",
                            "allowed_values",
                            "patterns",
                            "numeric_ranges",
                        ):
                            if source.get(key):
                                action["arguments"] = {
                                    **action["arguments"],
                                    f"__{key}__": source[key],
                                }
                        actions.append(action)
                    data["expected_actions"] = actions
                data.pop("tool_expectations", None)
        return data

    @field_validator("initial_state", mode="before")
    @classmethod
    def normalize_initial_state(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("initial_state must be a mapping")
        return dict(value)

    @field_validator("tenant_context", "authorization_context", "security_context", mode="before")
    @classmethod
    def normalize_context_mapping(
        cls, value: object
    ) -> dict[str, Any] | str | AuthorizationContext | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("case contexts must be mappings or references")
        return dict(value)

    @field_validator("input", mode="before")
    @classmethod
    def normalize_input(cls, value: object) -> dict[str, Any] | str | None:
        if value is None or isinstance(value, str):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("case input must be a mapping, text, or null")
        return dict(value)

    @field_validator("fixtures", mode="before")
    @classmethod
    def normalize_fixtures(cls, value: object) -> tuple[FixtureReference, ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            known = {"fixture_id", "id", "name", "version", "kind", "reference", "ref"}
            if known.intersection(value):
                return (FixtureReference.model_validate(value),)
            entries: list[FixtureReference] = []
            for key, raw_version in sorted(value.items(), key=lambda item: str(item[0])):
                if isinstance(raw_version, Mapping):
                    payload = dict(raw_version)
                    payload.setdefault("fixture_id", str(key))
                    entries.append(FixtureReference.model_validate(payload))
                else:
                    entries.append(FixtureReference(fixture_id=str(key), version=str(raw_version)))
            return tuple(entries)
        if isinstance(value, str):
            return (FixtureReference(fixture_id=value),)
        if not isinstance(value, Sequence):
            raise ValueError("fixtures must be a mapping, string, or sequence")
        sequence_entries: list[FixtureReference] = []
        for item in value:
            if isinstance(item, str):
                sequence_entries.append(FixtureReference(fixture_id=item))
            else:
                sequence_entries.append(FixtureReference.model_validate(item))
        return tuple(sequence_entries)

    @field_validator("expected_outputs", mode="before")
    @classmethod
    def normalize_expected_outputs(cls, value: object) -> tuple[OutputExpectation, ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            known = {"output_id", "expected_value", "operator", "evidence_refs", "description"}
            aliases = {"id", "name", "path", "expected", "value"}
            if known.intersection(value) or (
                {"expected", "value"}.intersection(value) and aliases.intersection(value)
            ):
                return (OutputExpectation.model_validate(value),)
            return tuple(
                OutputExpectation(output_id=str(key), path=str(key), expected_value=raw_value)
                for key, raw_value in sorted(value.items(), key=lambda item: str(item[0]))
            )
        if isinstance(value, str):
            return (OutputExpectation(output_id=value),)
        if not isinstance(value, Sequence):
            raise ValueError("expected_outputs must be a mapping, string, or sequence")
        return tuple(OutputExpectation.model_validate(item) for item in value)

    @field_validator(
        "expected_actions",
        "optional_actions",
        "required_actions",
        "prohibited_actions",
        mode="before",
    )
    @classmethod
    def normalize_action_expectations(cls, value: object) -> tuple[ActionExpectation, ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            known = {
                "action",
                "name",
                "action_name",
                "operation",
                "action_id",
                "action_type",
                "kind",
                "type",
                "tool_name",
            }
            if known.intersection(value):
                return (ActionExpectation.model_validate(value),)
            entries: list[ActionExpectation] = []
            for action, details in sorted(value.items(), key=lambda item: str(item[0])):
                if isinstance(details, Mapping):
                    payload = dict(details)
                    payload.setdefault("action", str(action))
                    entries.append(ActionExpectation.model_validate(payload))
                else:
                    entries.append(ActionExpectation(action=str(action), expected_result=details))
            return tuple(entries)
        if isinstance(value, str):
            return (ActionExpectation(action=value),)
        if not isinstance(value, Sequence):
            raise ValueError("action expectations must be a mapping, string, or sequence")
        return tuple(
            item if isinstance(item, ActionExpectation) else ActionExpectation.model_validate(item)
            for item in value
        )

    @field_validator("required_approvals", mode="before")
    @classmethod
    def normalize_approval_expectations(cls, value: object) -> tuple[ApprovalExpectation, ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            known = {
                "approval_id",
                "id",
                "name",
                "approval_name",
                "action",
                "action_name",
                "operation",
                "policy_id",
                "approval_policy_id",
                "policy",
                "decision",
            }
            if known.intersection(value):
                return (ApprovalExpectation.model_validate(value),)
            return tuple(
                ApprovalExpectation(
                    approval_id=str(key),
                    action=(str(raw_value) if isinstance(raw_value, str) else None),
                )
                for key, raw_value in sorted(value.items(), key=lambda item: str(item[0]))
            )
        if isinstance(value, str):
            return (ApprovalExpectation(approval_id=value),)
        if not isinstance(value, Sequence):
            raise ValueError("approval expectations must be a mapping, string, or sequence")
        return tuple(
            item
            if isinstance(item, ApprovalExpectation)
            else ApprovalExpectation.model_validate(item)
            for item in value
        )

    @field_validator("expected_final_state", mode="before")
    @classmethod
    def normalize_state_expectations(cls, value: object) -> tuple[StateExpectation, ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            known = {
                "state_path",
                "expected_value",
                "operator",
                "scope",
                "evidence_refs",
                "description",
            }
            aliases = {"path", "state_key", "key", "expected", "value"}
            if known.intersection(value) or (
                {"expected", "value"}.intersection(value) and aliases.intersection(value)
            ):
                return (StateExpectation.model_validate(value),)
            return tuple(
                StateExpectation(path=str(key), expected_value=raw_value)
                for key, raw_value in sorted(value.items(), key=lambda item: str(item[0]))
            )
        if isinstance(value, str):
            return (StateExpectation(path=value),)
        if not isinstance(value, Sequence):
            raise ValueError("state expectations must be a mapping, string, or sequence")
        return tuple(
            item if isinstance(item, StateExpectation) else StateExpectation.model_validate(item)
            for item in value
        )

    @field_validator("state_invariants", mode="before")
    @classmethod
    def normalize_invariant_expectations(cls, value: object) -> tuple[InvariantExpectation, ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            known = {
                "invariant_id",
                "description",
                "paths",
                "state_paths",
                "operator",
                "prohibited_paths",
                "evidence_refs",
            }
            if known.intersection(value) or (
                {"id", "name"}.intersection(value)
                and {"path", "state_path", "paths", "state_paths"}.intersection(value)
            ):
                return (InvariantExpectation.model_validate(value),)
            entries: list[InvariantExpectation] = []
            for invariant_id, details in sorted(value.items(), key=lambda item: str(item[0])):
                payload = (
                    dict(details) if isinstance(details, Mapping) else {"description": str(details)}
                )
                payload.setdefault("invariant_id", str(invariant_id))
                entries.append(InvariantExpectation.model_validate(payload))
            return tuple(entries)
        if isinstance(value, str):
            return (InvariantExpectation(invariant_id=value, path=value),)
        if not isinstance(value, Sequence):
            raise ValueError("invariant expectations must be a mapping, string, or sequence")
        return tuple(
            item
            if isinstance(item, InvariantExpectation)
            else InvariantExpectation.model_validate(item)
            for item in value
        )

    @field_validator("business_outcomes", mode="before")
    @classmethod
    def normalize_business_outcomes(cls, value: object) -> tuple[BusinessOutcomeExpectation, ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            known = {
                "outcome_id",
                "expected_value",
                "operator",
                "required",
                "evidence_refs",
                "description",
            }
            if known.intersection(value) or (
                {"expected", "value"}.intersection(value)
                and {"id", "name", "outcome", "outcome_name"}.intersection(value)
            ):
                return (BusinessOutcomeExpectation.model_validate(value),)
            return tuple(
                BusinessOutcomeExpectation(outcome_id=str(key), expected_value=raw_value)
                for key, raw_value in sorted(value.items(), key=lambda item: str(item[0]))
            )
        if isinstance(value, str):
            return (BusinessOutcomeExpectation(outcome_id=value),)
        if not isinstance(value, Sequence):
            raise ValueError("business outcomes must be a mapping, string, or sequence")
        return tuple(
            item
            if isinstance(item, BusinessOutcomeExpectation)
            else BusinessOutcomeExpectation.model_validate(item)
            for item in value
        )

    @field_validator("policy_references", mode="before")
    @classmethod
    def normalize_policy_references(cls, value: object) -> tuple[str, ...]:
        return _normalize_string_sequence(value, "case strings")

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> tuple[str, ...]:
        return _normalize_string_sequence(value, "tags", unique=False)

    @model_validator(mode="after")
    def validate_case(self) -> "EnterpriseEvaluationCase":
        _validate_unique_strings("policy_references", self.policy_references)
        _validate_unique_id_values(
            "fixture references", (fixture.identity for fixture in self.fixtures)
        )
        _validate_unique_id_values(
            "output expectation IDs", (output.output_id for output in self.expected_outputs)
        )
        _validate_unique_id_values(
            "approval expectation IDs", (approval.identity for approval in self.required_approvals)
        )
        _validate_unique_id_values(
            "invariant IDs", (invariant.invariant_id for invariant in self.state_invariants)
        )
        _validate_unique_id_values(
            "business outcome IDs", (outcome.outcome_id for outcome in self.business_outcomes)
        )
        _validate_unique_id_values(
            "final state paths", (expectation.path for expectation in self.expected_final_state)
        )
        for name, expectations in (
            ("expected_actions", self.expected_actions),
            ("optional_actions", self.optional_actions),
            ("required_actions", self.required_actions),
            ("prohibited_actions", self.prohibited_actions),
        ):
            _validate_unique_id_values(name, (expectation.identity for expectation in expectations))

        prohibited = {expectation.conflict_key for expectation in self.prohibited_actions}
        for name, expectations in (
            ("expected_actions", self.expected_actions),
            ("optional_actions", self.optional_actions),
            ("required_actions", self.required_actions),
        ):
            if prohibited.intersection(expectation.conflict_key for expectation in expectations):
                raise ValueError(f"{name} cannot contain a prohibited action")
        required = {expectation.conflict_key for expectation in self.required_actions}
        optional = {expectation.conflict_key for expectation in self.optional_actions}
        if required.intersection(optional):
            raise ValueError("required_actions and optional_actions cannot overlap")
        prohibited_names = {
            expectation.action.casefold() for expectation in self.prohibited_actions
        }
        if any(
            approval.action is not None and approval.action.casefold() in prohibited_names
            for approval in self.required_approvals
        ):
            raise ValueError("required approvals cannot target prohibited actions")
        return self

    @property
    def expected(self) -> dict[str, Any]:
        """Return simple output expectations in the current case shape."""

        return {
            expectation.output_id: expectation.expected_value
            for expectation in self.expected_outputs
        }

    @property
    def final_state_expectations(self) -> tuple[StateExpectation, ...]:
        """Return final-state expectations using an explicit name."""

        return self.expected_final_state


def _normalize_string_sequence(
    value: object,
    name: str,
    *,
    unique: bool = True,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, Sequence):
        values = tuple(value)
    else:
        raise ValueError(f"{name} must be a string or sequence")
    normalized = tuple(str(item).strip() for item in values)
    if unique:
        _validate_unique_strings(name, normalized)
    elif any(not item for item in normalized):
        raise ValueError(f"{name} must contain non-empty values")
    return normalized


def _validate_unique_strings(name: str, values: Sequence[str]) -> None:
    if any(not value for value in values):
        raise ValueError(f"{name} must contain non-empty values")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must contain unique values")


def _validate_unique_id_values(name: str, values: Sequence[str] | Any) -> None:
    values_tuple = tuple(values)
    if len(values_tuple) != len(set(values_tuple)):
        raise ValueError(f"{name} must contain unique values")


class DatasetVersion(ContractModel):
    """A versioned collection of evaluation cases."""

    dataset_id: str = Field(min_length=1)
    version: VersionString
    description: str = Field(min_length=1)
    cases: tuple[EnterpriseEvaluationCase, ...] = Field(min_length=1)
    provenance: CaseProvenance
    parent_version: VersionString | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_case_references(self) -> "DatasetVersion":
        case_ids = [case.case_id for case in self.cases]
        duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"Duplicate case IDs: {', '.join(duplicates)}")

        invalid = [
            case.case_id
            for case in self.cases
            if case.dataset_id != self.dataset_id or case.dataset_version != self.version
        ]
        if invalid:
            joined = ", ".join(invalid)
            raise ValueError(f"Cases reference the wrong dataset or version: {joined}")

        if self.parent_version == self.version:
            raise ValueError("parent_version must differ from version")

        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        return self
