"""Typed privacy and evidence-governance contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    VersionString,
    require_aware_utc,
    utc_now,
)
from enterprise_agent_improvement_lab.serialization import stable_json_dumps


class EvidenceKind(StrEnum):
    """Kinds of evidence that the Lab can reference."""

    TRACE = "trace"
    TRACE_SUMMARY = "trace_summary"
    STATE = "state"
    EVALUATION = "evaluation"
    FAILURE = "failure"
    PRODUCTION = "production"
    STAGE = "stage"
    ARTIFACT = "artifact"
    COMPARISON = "comparison"
    GENERIC = "generic"


class SensitiveDataClassification(StrEnum):
    """Sensitivity class assigned to one field."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    SECRET = "secret"


class RedactionAction(StrEnum):
    """Action applied to a sensitive field before persistence."""

    REMOVE = "remove"
    MASK = "mask"
    HASH = "hash"
    REFERENCE = "reference"


class RetentionAction(StrEnum):
    """Action used when governed evidence reaches its retention limit."""

    DELETE = "delete"
    ARCHIVE = "archive"
    HOLD = "hold"


class EvidenceRef(ContractModel):
    """A typed reference to evidence without copying its payload."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    evidence_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("evidence_id", "ref_id", "reference_id", "id"),
    )
    kind: EvidenceKind = Field(
        default=EvidenceKind.GENERIC,
        validation_alias=AliasChoices("kind", "evidence_kind", "type"),
    )
    source: str = Field(default="unknown", min_length=1)
    reference: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("reference", "uri", "location", "ref"),
    )
    checksum: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        validation_alias=AliasChoices("checksum", "sha256", "content_sha256"),
    )
    tenant_id: str | None = Field(default=None, min_length=1)
    classification: SensitiveDataClassification = Field(
        default=SensitiveDataClassification.INTERNAL,
        validation_alias=AliasChoices("classification", "sensitivity", "data_classification"),
    )
    retention_policy_id: str | None = Field(default=None, min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_evidence(self) -> "EvidenceRef":
        object.__setattr__(self, "created_at", require_aware_utc(self.created_at))
        if any(_is_sensitive_key(key) for key in self.metadata):
            raise ValueError("Evidence reference metadata cannot contain secret-bearing keys")
        return self

    @property
    def ref_id(self) -> str:
        """Return the stable reference identity."""

        return self.evidence_id

    @property
    def identity(self) -> str:
        """Return the stable evidence identity."""

        return self.evidence_id


class SensitiveField(ContractModel):
    """A field path that needs explicit privacy handling."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    path: str = Field(
        min_length=1,
        validation_alias=AliasChoices("path", "field", "field_path", "name"),
    )
    classification: SensitiveDataClassification = Field(
        default=SensitiveDataClassification.SECRET,
        validation_alias=AliasChoices("classification", "sensitivity", "data_classification"),
    )
    action: RedactionAction = Field(
        default=RedactionAction.MASK,
        validation_alias=AliasChoices("action", "redaction", "redaction_action"),
    )
    reason: str | None = None

    @property
    def field_path(self) -> str:
        """Return the field path using the descriptive name."""

        return self.path


class RedactionPolicy(ContractModel):
    """Rules for removing sensitive data before evidence persistence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    policy_id: str = Field(min_length=1)
    version: VersionString = "1.0.0"
    sensitive_fields: tuple[SensitiveField, ...] = Field(
        default=(),
        validation_alias=AliasChoices("sensitive_fields", "fields"),
    )
    default_action: RedactionAction = Field(
        default=RedactionAction.MASK,
        validation_alias=AliasChoices("default_action", "default_redaction"),
    )
    replacement: str = Field(default="[REDACTED]", min_length=1)
    redact_unknown_fields: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "RedactionPolicy":
        paths = [field.path for field in self.sensitive_fields]
        if len(paths) != len(set(paths)):
            raise ValueError("Redaction policy field paths must be unique")
        if any(_is_sensitive_key(key) for key in self.metadata):
            raise ValueError("Redaction policy metadata cannot contain secret-bearing keys")
        return self

    @classmethod
    def default(cls) -> "RedactionPolicy":
        """Return the deterministic baseline policy used by evidence stores."""

        return cls(policy_id="default-evidence-redaction")

    def redact(self, value: Any) -> Any:
        """Return a JSON-compatible value with sensitive data removed."""

        data = _json_value(value)
        for field in self.sensitive_fields:
            tokens = _path_tokens(field.path)
            if tokens:
                data = _apply_redaction(data, tokens, field.action, self.replacement)
        return _scrub_sensitive_values(data, self.default_action, self.replacement)

    def redact_model(self, model: BaseModel) -> BaseModel:
        """Return a validated redacted copy of a Pydantic model."""

        payload = self.redact(model.model_dump(mode="python", exclude_none=False))
        if not isinstance(payload, Mapping):
            raise ValueError("A redacted model payload must be a mapping")
        # Redaction can change fields whose value is derived from the payload.
        # Recreate those values through their model validators.
        model_name = type(model).__name__
        if model_name == "StateSnapshot":
            payload = dict(payload)
            payload.pop("checksum", None)
        elif model_name == "EnvironmentSnapshot":
            payload = dict(payload)
            payload.pop("snapshot_id", None)
            payload.pop("snapshot_sha256", None)
        elif model_name == "CandidateArtifact":
            payload = dict(payload)
            payload.pop("content_sha256", None)
        return type(model).model_validate(payload)


class RetentionPolicy(ContractModel):
    """Storage-independent rules for how long evidence can remain available."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    policy_id: str = Field(min_length=1)
    version: VersionString = "1.0.0"
    retention_days: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "retention_days",
            "retention_period_days",
            "duration_days",
            "days",
        ),
    )
    expires_at: datetime | None = None
    action: RetentionAction = RetentionAction.DELETE
    legal_hold: bool = False
    evidence_kinds: tuple[EvidenceKind, ...] = ()
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_policy(self) -> "RetentionPolicy":
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", require_aware_utc(self.expires_at))
        if any(_is_sensitive_key(key) for key in self.metadata):
            raise ValueError("Retention policy metadata cannot contain secret-bearing keys")
        if len(self.evidence_kinds) != len(set(self.evidence_kinds)):
            raise ValueError("Retention policy evidence kinds must be unique")
        return self

    def expiry_for(self, created_at: datetime) -> datetime | None:
        """Return the expiry instant for evidence created at ``created_at``."""

        created = require_aware_utc(created_at)
        if self.expires_at is not None:
            return self.expires_at
        if self.retention_days is None:
            return None
        return created + timedelta(days=self.retention_days)

    def is_expired(self, created_at: datetime, *, now: datetime | None = None) -> bool:
        """Return whether evidence is outside this policy."""

        if self.legal_hold:
            return False
        expiry = self.expiry_for(created_at)
        return expiry is not None and require_aware_utc(now or utc_now()) >= expiry

    def assert_persistable(
        self,
        created_at: datetime,
        *,
        now: datetime | None = None,
    ) -> None:
        """Reject evidence that has already reached its retention limit."""

        if self.is_expired(created_at, now=now):
            raise ValueError(f"Evidence is outside retention policy {self.policy_id}")


class TenantBoundary(ContractModel):
    """A tenant boundary that evidence must satisfy before persistence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    boundary_id: str = Field(
        default="default-tenant-boundary",
        min_length=1,
        validation_alias=AliasChoices("boundary_id", "id", "name"),
    )
    tenant_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("tenant_id", "tenant"),
    )
    allowed_tenant_ids: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("allowed_tenant_ids", "tenant_ids")
    )
    require_tenant_id: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_boundary(self) -> "TenantBoundary":
        values = (self.tenant_id, *self.allowed_tenant_ids)
        if any(not value for value in values):
            raise ValueError("Tenant boundary IDs must be non-empty")
        if len(values) != len(set(values)):
            raise ValueError("Tenant boundary IDs must be unique")
        if any(_is_sensitive_key(key) for key in self.metadata):
            raise ValueError("Tenant boundary metadata cannot contain secret-bearing keys")
        return self

    @property
    def tenant_ids(self) -> tuple[str, ...]:
        """Return all tenants accepted by this boundary."""

        return (self.tenant_id, *self.allowed_tenant_ids)

    def validate_value(self, value: Any) -> None:
        """Reject a value that contains a tenant outside this boundary."""

        observed = _tenant_values(value)
        if not observed:
            if self.require_tenant_id:
                raise ValueError(f"Evidence must identify tenant {self.tenant_id}")
            return
        invalid = sorted(set(observed) - set(self.tenant_ids))
        if invalid:
            raise ValueError(
                f"Evidence crosses tenant boundary {self.boundary_id}: {', '.join(invalid)}"
            )

    def allows(self, value: Any) -> bool:
        """Return whether a value satisfies this boundary."""

        try:
            self.validate_value(value)
        except ValueError:
            return False
        return True


def apply_governance(
    model: BaseModel,
    *,
    redaction_policy: RedactionPolicy | None = None,
    retention_policy: RetentionPolicy | None = None,
    tenant_boundary: TenantBoundary | None = None,
    now: datetime | None = None,
) -> BaseModel:
    """Apply tenant, retention, and redaction rules to one model."""

    if tenant_boundary is not None:
        tenant_boundary.validate_value(model)
    if retention_policy is not None:
        retention_policy.assert_persistable(_model_timestamp(model), now=now)
    if redaction_policy is None:
        return model
    return redaction_policy.redact_model(model)


def safe_summary(value: Any, *, max_length: int = 512) -> str:
    """Return a stable summary without raw secret-bearing values."""

    if max_length < 1:
        raise ValueError("max_length must be positive")
    redacted = RedactionPolicy.default().redact(value)
    if isinstance(redacted, str):
        summary = redacted
    else:
        summary = stable_json_dumps(redacted)
    if len(summary) <= max_length:
        return summary
    digest = sha256(summary.encode("utf-8")).hexdigest()[:16]
    prefix_length = max(0, max_length - len(digest) - 18)
    return f"{summary[:prefix_length]}… [sha256:{digest}]"


_SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "access_key",
        "accesskey",
        "authorization",
        "authorization_header",
        "bearer",
        "client_secret",
        "clientsecret",
        "cookie",
        "credential",
        "password",
        "private_key",
        "refresh_token",
        "secret_key",
        "secretkey",
        "secret_value",
        "secretvalue",
        "session_token",
        "sessiontoken",
        "access_token",
        "secret",
        "token",
        "jwt",
    }
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|password|secret(?:[_-]?(?:key|value))?|"
    r"client[_-]?secret|credential|authorization|bearer|private[_-]?key|"
    r"refresh[_-]?token|access[_-]?token|session[_-]?token|id[_-]?token|jwt)"
    r"\s*[:=]\s*[^\s,;]+"
)
_DELETE = object()


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().casefold().replace("-", "_")
    return (
        normalized in _SENSITIVE_KEY_NAMES
        or normalized.endswith("_secret")
        or normalized.startswith("secret_")
        or normalized.endswith("_password")
        or normalized.startswith("password_")
        or normalized.endswith("_credential")
        or normalized.startswith("credential_")
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return [_json_value(item) for item in sorted(value, key=str)]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not isfinite(value):
        return None
    return value


def _path_tokens(path: str) -> tuple[str, ...]:
    normalized = path.strip()
    if normalized.startswith("$"):
        normalized = normalized[1:]
    normalized = normalized.replace("[", ".").replace("]", "")
    normalized = normalized.replace("*", "*")
    return tuple(part for part in normalized.split(".") if part)


def _apply_redaction(
    value: Any,
    tokens: tuple[str, ...],
    action: RedactionAction,
    replacement: str,
) -> Any:
    if not tokens:
        return _redact_value(value, action, replacement)
    token = tokens[0]
    remaining = tokens[1:]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if token == "*" or str(key) == token:
                updated = _apply_redaction(item, remaining, action, replacement)
                if updated is not _DELETE:
                    result[str(key)] = updated
            else:
                result[str(key)] = item
        return result
    if isinstance(value, list):
        if token == "*":
            updated_list: list[Any] = []
            for item in value:
                updated = _apply_redaction(item, remaining, action, replacement)
                if updated is not _DELETE:
                    updated_list.append(updated)
            return updated_list
        updated_list = list(value)
        indexes = _list_index(token, len(updated_list))
        for index in reversed(tuple(indexes)):
            updated = _apply_redaction(updated_list[index], remaining, action, replacement)
            if updated is _DELETE:
                del updated_list[index]
            else:
                updated_list[index] = updated
        return updated_list
    return value


def _list_index(token: str, size: int) -> range:
    try:
        index = int(token)
    except ValueError:
        return range(0)
    return range(index, index + 1) if 0 <= index < size else range(0)


def _redact_value(value: Any, action: RedactionAction, replacement: str) -> Any:
    if action == RedactionAction.REMOVE:
        return _DELETE
    if action == RedactionAction.MASK:
        return replacement
    encoded = stable_json_dumps(value)
    digest = sha256(encoded.encode("utf-8")).hexdigest()
    if action == RedactionAction.HASH:
        return f"sha256:{digest}"
    return f"ref:sha256:{digest}"


def _scrub_sensitive_values(value: Any, action: RedactionAction, replacement: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                updated = _redact_value(item, action, replacement)
                if updated is not _DELETE:
                    result[str(key)] = updated
            else:
                result[str(key)] = _scrub_sensitive_values(item, action, replacement)
        return result
    if isinstance(value, list):
        return [_scrub_sensitive_values(item, action, replacement) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_TEXT.sub(r"\1=[REDACTED]", value)
    return value


def _tenant_values(value: Any) -> tuple[str, ...]:
    observed: list[str] = []
    if isinstance(value, BaseModel):
        return _tenant_values(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in {"tenant_id", "tenant", "owner_tenant_id"}:
                if isinstance(item, str) and item.strip():
                    observed.append(item.strip())
            observed.extend(_tenant_values(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            observed.extend(_tenant_values(item))
    return tuple(dict.fromkeys(observed))


def _model_timestamp(model: BaseModel) -> datetime:
    for field_name in ("created_at", "captured_at", "started_at", "occurred_at", "reviewed_at"):
        value = getattr(model, field_name, None)
        if isinstance(value, datetime):
            return value
    return utc_now()


__all__ = [
    "EvidenceKind",
    "EvidenceRef",
    "RedactionAction",
    "RedactionPolicy",
    "RetentionAction",
    "RetentionPolicy",
    "SensitiveDataClassification",
    "SensitiveField",
    "TenantBoundary",
    "apply_governance",
    "safe_summary",
]
