import json
from pathlib import Path

import pytest

from enterprise_agent_improvement_lab.datasets import (
    DatasetLoadError,
    dataset_to_json,
    load_dataset,
)


def test_load_dataset_from_json_and_yaml(tmp_path: Path, dataset):
    json_path = tmp_path / "dataset.json"
    yaml_path = tmp_path / "dataset.yaml"
    json_path.write_text(dataset_to_json(dataset), encoding="utf-8")
    yaml_path.write_text(
        "\n".join(
            [
                "dataset_id: demo",
                "version: 1.0.0",
                "description: Small test dataset",
                "provenance:",
                "  source: test",
                "  source_ref: fixture-1",
                "  collected_at: '2026-08-23T12:00:00Z'",
                "created_at: '2026-08-23T12:00:00Z'",
                "cases:",
                "  - case_id: case-1",
                "    dataset_id: demo",
                "    dataset_version: 1.0.0",
                "    split: development",
                "    risk: low",
                "    input:",
                "      question: What is two plus two?",
                "    input_text: What is two plus two?",
                "    expected:",
                "      answer: '4'",
                "    provenance:",
                "      source: test",
                "      source_ref: fixture-1",
                "      collected_at: '2026-08-23T12:00:00Z'",
            ]
        ),
        encoding="utf-8",
    )

    assert load_dataset(json_path) == dataset
    assert load_dataset(yaml_path) == dataset


def test_load_dataset_reports_parse_and_extension_errors(tmp_path: Path):
    unsupported = tmp_path / "dataset.txt"
    unsupported.write_text("{}", encoding="utf-8")
    with pytest.raises(DatasetLoadError, match="JSON or YAML"):
        load_dataset(unsupported)

    invalid = tmp_path / "dataset.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(DatasetLoadError, match="Could not parse"):
        load_dataset(invalid)


def test_load_dataset_reports_invalid_references(tmp_path: Path, dataset):
    invalid = tmp_path / "dataset.json"
    payload = dataset.model_dump(mode="json")
    payload["cases"][0]["dataset_version"] = "2.0.0"
    invalid.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DatasetLoadError, match="wrong dataset or version"):
        load_dataset(invalid)
