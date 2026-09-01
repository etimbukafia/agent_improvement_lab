"""Stable JSON serialization for Lab contracts."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class SerializationError(ValueError):
    """Raised when a contract cannot be serialized or loaded."""


def canonical_data(value: BaseModel | dict[str, Any] | list[Any] | tuple[Any, ...]) -> Any:
    """Convert a value to JSON-compatible data."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=False)
    if isinstance(value, tuple):
        return [canonical_data(item) for item in value]
    if isinstance(value, list):
        return [canonical_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): canonical_data(item) for key, item in value.items()}
    return value


def stable_json_dumps(value: BaseModel | dict[str, Any] | list[Any] | tuple[Any, ...]) -> str:
    """Serialize a value with stable key order and separators."""

    try:
        return json.dumps(
            canonical_data(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError(str(exc)) from exc


def stable_json_bytes(value: BaseModel | dict[str, Any] | list[Any] | tuple[Any, ...]) -> bytes:
    """Serialize a value to stable UTF-8 JSON bytes."""

    return stable_json_dumps(value).encode("utf-8")


def model_to_json(model: ModelT) -> str:
    """Serialize one Pydantic model."""

    return stable_json_dumps(model)


def model_from_json(model_type: type[ModelT], payload: str | bytes) -> ModelT:
    """Validate JSON into a Pydantic model."""

    try:
        return model_type.model_validate_json(payload)
    except (TypeError, ValueError) as exc:
        raise SerializationError(f"Could not validate {model_type.__name__}: {exc}") from exc


def content_sha256(content: str) -> str:
    """Return the SHA-256 digest of UTF-8 content."""

    return sha256(content.encode("utf-8")).hexdigest()


def write_json(path: str | Path, value: BaseModel | dict[str, Any] | list[Any]) -> None:
    """Write stable JSON with one final newline."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stable_json_dumps(value) + "\n", encoding="utf-8")


def read_json(path: str | Path) -> Any:
    """Read JSON from a file."""

    target = Path(path)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SerializationError(f"Could not read JSON from {target}: {exc}") from exc
