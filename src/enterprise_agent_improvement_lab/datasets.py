"""Load and validate JSON or YAML evaluation datasets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from enterprise_agent_improvement_lab.contracts.cases import DatasetVersion
from enterprise_agent_improvement_lab.serialization import stable_json_dumps


class DatasetLoadError(ValueError):
    """Raised when a dataset file is not valid."""


def validate_dataset_data(data: Any, *, source: str = "dataset") -> DatasetVersion:
    """Validate decoded dataset data and add a useful source to errors."""

    if not isinstance(data, dict):
        raise DatasetLoadError(f"{source} must contain one mapping")
    try:
        return DatasetVersion.model_validate(data)
    except ValidationError as exc:
        raise DatasetLoadError(f"Invalid dataset {source}: {exc}") from exc


def load_dataset(path: str | Path) -> DatasetVersion:
    """Load one JSON or YAML dataset file."""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise DatasetLoadError(f"Unsupported dataset format {source.suffix!r}; use JSON or YAML")
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetLoadError(f"Could not read dataset {source}: {exc}") from exc

    try:
        data = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise DatasetLoadError(f"Could not parse dataset {source}: {exc}") from exc
    return validate_dataset_data(data, source=str(source))


def dataset_to_json(dataset: DatasetVersion) -> str:
    """Return a stable JSON representation of a dataset."""

    return stable_json_dumps(dataset)
