"""Shared validation rules for Lab contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_SCHEMA_VERSION = "1.0"
VersionString = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")]


class ContractModel(BaseModel):
    """Base model for external Lab data."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    schema_version: str = Field(default=CONTRACT_SCHEMA_VERSION, pattern=r"^[0-9]+\.[0-9]+$")

    @field_validator("schema_version")
    @classmethod
    def check_schema_version(cls, value: str) -> str:
        if value != CONTRACT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema version {value!r}; expected {CONTRACT_SCHEMA_VERSION!r}"
            )
        return value


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""

    return datetime.now(timezone.utc)


def require_aware_utc(value: datetime) -> datetime:
    """Validate and normalize a timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a UTC offset")
    return value.astimezone(timezone.utc)
