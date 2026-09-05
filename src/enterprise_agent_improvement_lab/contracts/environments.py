"""Immutable environment identities for reproducible evaluation runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from hashlib import sha256
from math import isfinite
from typing import Any, Self

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from enterprise_agent_improvement_lab.contracts.common import (
    ContractModel,
    require_aware_utc,
    utc_now,
)
from enterprise_agent_improvement_lab.serialization import stable_json_dumps

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
)
_JSON_SCALARS = (str, int, float, bool)


class SnapshotSetting(ContractModel):
    """One safe scalar setting retained in a snapshot."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=False,
        validate_assignment=True,
    )

    key: str = Field(min_length=1, validation_alias=AliasChoices("key", "name"))
    value: str | int | float | bool | None

    @field_validator("key")
    @classmethod
    def key_is_safe(cls, value: str) -> str:
        if _is_sensitive_key(value):
            raise ValueError("snapshot settings cannot contain secret-bearing keys")
        return value

    @field_validator("value")
    @classmethod
    def value_is_safe(
        cls, value: str | int | float | bool | None
    ) -> str | int | float | bool | None:
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("snapshot settings cannot contain non-finite numbers")
        return value


class SnapshotComponentHash(ContractModel):
    """Stable hash for one versioned environment component."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    component_id: str = Field(min_length=1, validation_alias=AliasChoices("component_id", "id"))
    version: str | None = None
    sha256: str = Field(
        validation_alias=AliasChoices("sha256", "hash", "content_sha256"),
        pattern=_SHA256_PATTERN,
    )

    @property
    def identity(self) -> str:
        """Return the stable component identity."""

        return (
            f"{self.component_id}@{self.version}" if self.version is not None else self.component_id
        )


class SnapshotVersionReference(ContractModel):
    """Exact version reference for an external fixture or service stub."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    component_id: str = Field(min_length=1, validation_alias=AliasChoices("component_id", "id"))
    version: str = Field(min_length=1)

    @property
    def identity(self) -> str:
        """Return the stable version reference."""

        return f"{self.component_id}@{self.version}"


class EnvironmentSnapshot(ContractModel):
    """Immutable identity of the runtime and registries used by one run.

    The snapshot stores references, versions, safe parameters, and hashes. It
    never stores credentials or raw runtime payloads. ``snapshot_id`` is derived
    from all reproducibility fields except capture time and the derived fields.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("snapshot_id", "environment_snapshot_id"),
    )
    snapshot_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
        validation_alias=AliasChoices("snapshot_sha256", "checksum", "identity_sha256"),
    )
    agent_registry_version: str = Field(min_length=1)
    prompt_registry_version: str = Field(min_length=1)
    skill_registry_version: str = Field(min_length=1)
    tool_registry_version: str = Field(min_length=1)
    policy_registry_version: str = Field(min_length=1)
    agent_definition_hash: str = Field(pattern=_SHA256_PATTERN)
    prompt_hashes: tuple[SnapshotComponentHash, ...] = Field(default=())
    skill_hashes: tuple[SnapshotComponentHash, ...] = Field(default=())
    tool_hashes: tuple[SnapshotComponentHash, ...] = Field(
        default=(),
        validation_alias=AliasChoices("tool_hashes", "tool_definition_hashes"),
    )
    policy_hashes: tuple[SnapshotComponentHash, ...] = Field(
        default=(),
        validation_alias=AliasChoices("policy_hashes", "policy_definition_hashes"),
    )
    runtime_name: str = Field(min_length=1)
    runtime_version: str = Field(min_length=1)
    provider: str | None = None
    provider_version: str | None = None
    model: str | None = None
    model_parameters: tuple[SnapshotSetting, ...] = ()
    feature_flags: tuple[SnapshotSetting, ...] = ()
    tenant_profile: str | None = None
    fixture_version: str = Field(
        default="unknown",
        min_length=1,
        validation_alias=AliasChoices("fixture_version", "test_fixture_version"),
    )
    external_service_stub_versions: tuple[SnapshotVersionReference, ...] = ()
    environment_name: str = Field(default="unknown", min_length=1)
    clock_mode: str = Field(default="wall", min_length=1)
    seed: int | None = None
    metadata: tuple[SnapshotSetting, ...] = Field(
        default=(),
        validation_alias=AliasChoices("metadata", "safe_metadata"),
    )
    # These values are safe exact provenance.  They identify the resolved
    # Harness build without copying complete registry records into the Lab.
    registry_snapshot_id: str | None = Field(default=None, min_length=1)
    resolved_manifest_id: str | None = Field(default=None, min_length=1)
    # Preserve the exact Harness digest; the runtime, not the Lab, defines its
    # algorithm and textual representation.
    resolved_manifest_digest: str | None = Field(default=None, min_length=1)
    agent_ref: str | None = Field(default=None, min_length=1)
    prompt_ref: str | None = Field(default=None, min_length=1)
    skill_refs: tuple[str, ...] = ()
    tool_refs: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    captured_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "agent_registry_version",
        "prompt_registry_version",
        "skill_registry_version",
        "tool_registry_version",
        "policy_registry_version",
        mode="before",
    )
    @classmethod
    def normalize_registry_version(cls, value: object) -> str:
        if isinstance(value, bool) or value is None:
            raise ValueError("registry versions must be non-empty values")
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("registry versions must be non-empty values")
        return normalized

    @field_validator("provider", "provider_version", "model", "tenant_profile", mode="before")
    @classmethod
    def normalize_optional_identity(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            profile_id = value.get("profile_id", value.get("id", value.get("name")))
            if isinstance(profile_id, str) and profile_id.strip():
                return profile_id
            safe = _safe_json_value(value)
            return f"ref:{sha256(stable_json_dumps(safe).encode('utf-8')).hexdigest()}"
        return str(value)

    @field_validator("prompt_hashes", "skill_hashes", "tool_hashes", "policy_hashes", mode="before")
    @classmethod
    def normalize_component_hashes(cls, value: object) -> tuple[SnapshotComponentHash, ...]:
        if value is None:
            return ()
        entries: list[SnapshotComponentHash] = []
        if isinstance(value, Mapping):
            for key, raw_hash in value.items():
                component_id, version = _split_component_identity(str(key))
                if isinstance(raw_hash, Mapping):
                    payload = dict(raw_hash)
                    payload.setdefault("component_id", component_id)
                    if version is not None:
                        payload.setdefault("version", version)
                else:
                    payload = {
                        "component_id": component_id,
                        "version": version,
                        "sha256": raw_hash,
                    }
                entries.append(SnapshotComponentHash.model_validate(payload))
        else:
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ValueError("component hashes must be a mapping or sequence")
            entries = [SnapshotComponentHash.model_validate(item) for item in value]
        return _sorted_unique_component_hashes(entries)

    @field_validator("model_parameters", "feature_flags", "metadata", mode="before")
    @classmethod
    def normalize_settings(cls, value: object) -> tuple[SnapshotSetting, ...]:
        if value is None:
            return ()
        entries: list[SnapshotSetting] = []
        if isinstance(value, Mapping):
            for key, raw_value in value.items():
                if _is_sensitive_key(str(key)):
                    continue
                entries.append(SnapshotSetting(key=str(key), value=_safe_setting_value(raw_value)))
        else:
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ValueError("snapshot settings must be a mapping or sequence")
            for item in value:
                if isinstance(item, SnapshotSetting):
                    entries.append(item)
                else:
                    payload = dict(item) if isinstance(item, Mapping) else item
                    if isinstance(payload, Mapping) and _is_sensitive_key(
                        str(payload.get("key", payload.get("name", "")))
                    ):
                        continue
                    entries.append(SnapshotSetting.model_validate(payload))
        entries.sort(key=lambda item: item.key)
        keys = [item.key for item in entries]
        if len(keys) != len(set(keys)):
            raise ValueError("snapshot setting keys must be unique")
        return tuple(entries)

    @field_validator("external_service_stub_versions", mode="before")
    @classmethod
    def normalize_stub_versions(cls, value: object) -> tuple[SnapshotVersionReference, ...]:
        if value is None:
            return ()
        entries: list[SnapshotVersionReference] = []
        if isinstance(value, Mapping):
            entries = [
                SnapshotVersionReference(component_id=str(key), version=str(version))
                for key, version in value.items()
            ]
        else:
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ValueError("external service stub versions must be a mapping or sequence")
            entries = [SnapshotVersionReference.model_validate(item) for item in value]
        entries.sort(key=lambda item: (item.component_id, item.version))
        identities = [item.identity for item in entries]
        if len(identities) != len(set(identities)):
            raise ValueError("external service stub references must be unique")
        return tuple(entries)

    @field_validator("captured_at")
    @classmethod
    def normalize_capture_timestamp(cls, value: datetime) -> datetime:
        return require_aware_utc(value)

    @model_validator(mode="after")
    def validate_identity(self) -> "EnvironmentSnapshot":
        for name, values in (
            ("skill_refs", self.skill_refs),
            ("tool_refs", self.tool_refs),
            ("policy_refs", self.policy_refs),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique references")
        digest = self._identity_digest()
        if self.snapshot_sha256 is not None and self.snapshot_sha256 != digest:
            raise ValueError("snapshot_sha256 does not match snapshot contents")
        expected_id = f"environment-{digest}"
        if self.snapshot_id is not None and self.snapshot_id != expected_id:
            raise ValueError("snapshot_id does not match snapshot contents")
        object.__setattr__(self, "snapshot_sha256", digest)
        object.__setattr__(self, "snapshot_id", expected_id)
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """Copy a snapshot while preserving its derived identity invariants."""

        if update is None:
            return super().model_copy(deep=deep)
        values = self.model_dump()
        values.pop("snapshot_id", None)
        values.pop("snapshot_sha256", None)
        values.update(update)
        return type(self).model_validate(values)

    @property
    def identity(self) -> str:
        """Return the deterministic environment identity."""

        assert self.snapshot_id is not None
        return self.snapshot_id

    @property
    def checksum(self) -> str:
        """Return the deterministic environment checksum."""

        assert self.snapshot_sha256 is not None
        return self.snapshot_sha256

    @property
    def snapshot_hash(self) -> str:
        """Return the checksum using the short hash name."""

        return self.checksum

    @property
    def snapshot_identity(self) -> str:
        """Return the identity using the explicit snapshot vocabulary."""

        return self.identity

    @property
    def environment_snapshot_id(self) -> str:
        """Return the identity using the RunManifest vocabulary."""

        return self.identity

    @property
    def tool_definition_hashes(self) -> tuple[SnapshotComponentHash, ...]:
        """Return tool hashes using the descriptive field name."""

        return self.tool_hashes

    @property
    def prompt_definition_hashes(self) -> tuple[SnapshotComponentHash, ...]:
        """Return prompt hashes using the descriptive field name."""

        return self.prompt_hashes

    @property
    def skill_definition_hashes(self) -> tuple[SnapshotComponentHash, ...]:
        """Return skill hashes using the descriptive field name."""

        return self.skill_hashes

    @property
    def policy_definition_hashes(self) -> tuple[SnapshotComponentHash, ...]:
        """Return policy hashes using the descriptive field name."""

        return self.policy_hashes

    @property
    def safe_metadata(self) -> tuple[SnapshotSetting, ...]:
        """Return metadata after secret-bearing keys were removed."""

        return self.metadata

    @property
    def comparison_identity(self) -> str:
        """Return the identity of the shared execution environment.

        Agent definition hashes are excluded because a baseline and candidate
        normally use different agent definitions. Prompt and skill registry
        state is also candidate-controlled during bounded materialization.
        Harness build and registry snapshot identifiers are preserved on the
        full snapshot but are excluded here because they identify the build
        graph, not the shared execution environment. Tool, policy, fixture,
        provider, and runtime state remain part of this identity.
        """

        payload = self._identity_payload()
        for field in (
            "agent_registry_version",
            "prompt_registry_version",
            "skill_registry_version",
            "agent_definition_hash",
            "prompt_hashes",
            "skill_hashes",
            "registry_snapshot_id",
            "resolved_manifest_id",
            "resolved_manifest_digest",
            "agent_ref",
            "prompt_ref",
            "skill_refs",
            "tool_refs",
            "policy_refs",
        ):
            payload.pop(field, None)
        digest = sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()
        return f"environment-compatible-{digest}"

    def is_compatible_with(self, other: "EnvironmentSnapshot") -> bool:
        """Return whether two runs used the same comparison environment."""

        return self.comparison_identity == other.comparison_identity

    @classmethod
    def for_legacy_manifest(
        cls,
        *,
        dataset_id: str,
        dataset_version: str,
        toolset: Sequence[str],
        runtime_name: str,
        runtime_version: str,
        provider: str | None,
        model: str | None,
        seed: int | None,
        captured_at: datetime,
    ) -> "EnvironmentSnapshot":
        """Create a stable compatibility snapshot for a legacy manifest.

        Legacy manifests do not contain registry records. Their snapshot uses
        explicit ``legacy`` markers and hashes only the stable references that
        the old manifest carried.
        """

        tool_hashes = tuple(
            SnapshotComponentHash(
                component_id=tool_id,
                sha256=sha256(f"legacy-tool:{tool_id}".encode("utf-8")).hexdigest(),
            )
            for tool_id in sorted(set(toolset))
        )
        return cls(
            agent_registry_version="legacy",
            prompt_registry_version="legacy",
            skill_registry_version="legacy",
            tool_registry_version="legacy",
            policy_registry_version="legacy",
            agent_definition_hash=sha256(b"legacy-agent-definition").hexdigest(),
            tool_hashes=tool_hashes,
            runtime_name=runtime_name,
            runtime_version=runtime_version,
            provider=provider,
            model=model,
            fixture_version=f"{dataset_id}@{dataset_version}",
            environment_name="legacy",
            clock_mode="wall",
            seed=seed,
            captured_at=captured_at,
        )

    def _identity_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"snapshot_id", "snapshot_sha256", "captured_at"},
        )

    def _identity_digest(self) -> str:
        return sha256(stable_json_dumps(self._identity_payload()).encode("utf-8")).hexdigest()


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        return True
    return normalized == "token" or normalized.endswith("_token") or normalized.startswith("token_")


def _safe_setting_value(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("snapshot settings cannot contain non-finite numbers")
        return value
    safe = _safe_json_value(value)
    return stable_json_dumps(safe)


def _safe_json_value(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, Enum):
        return _safe_json_value(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _safe_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _safe_json_value(model_dump(mode="json", exclude_none=False))
        except TypeError:
            return _safe_json_value(model_dump())
    raise ValueError("snapshot values must be JSON-compatible")


def _split_component_identity(value: str) -> tuple[str, str | None]:
    component_id, separator, version = value.rpartition("@")
    if separator and component_id and version:
        return component_id, version
    return value, None


def _sorted_unique_component_hashes(
    entries: Sequence[SnapshotComponentHash],
) -> tuple[SnapshotComponentHash, ...]:
    ordered = sorted(entries, key=lambda item: (item.component_id, item.version or ""))
    identities = [item.identity for item in ordered]
    if len(identities) != len(set(identities)):
        raise ValueError("component hashes must contain unique identities")
    return tuple(ordered)


__all__ = [
    "EnvironmentSnapshot",
    "SnapshotComponentHash",
    "SnapshotSetting",
    "SnapshotVersionReference",
]
