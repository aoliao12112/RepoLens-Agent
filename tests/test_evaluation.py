from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from repolens.evaluation import calculate_metrics, load_dataset


def test_repository_evaluation_manifest_has_twenty_valid_cases() -> None:
    cases = load_dataset(Path("data/evaluation/repositories.json"))

    assert len(cases) == 20
    assert len({str(case.repository_url) for case in cases}) == 20
    assert {case.category for case in cases} >= {"active-python", "agent-python", "archived"}


def test_annotation_requires_a_timezone_and_source(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    path.write_text(
        json.dumps(
            [
                {
                    "repository_url": "https://github.com/example/project",
                    "target": "测试目标",
                    "category": "fixture",
                    "annotation": {
                        "archived": False,
                        "primary_language": "Python",
                        "license_status": "detected",
                        "has_tests": True,
                        "annotated_at": "2026-10-26T10:00:00",
                        "source_urls": [],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_dataset(path)


def test_metrics_only_report_cost_and_accuracy_when_observed() -> None:
    metrics = calculate_metrics(
        [
            {
                "status": "success",
                "duration_ms": 100,
                "annotation": {
                    "archived": False,
                    "primary_language": "Python",
                    "license_status": "detected",
                    "has_tests": True,
                },
                "snapshot": {
                    "repository": {"archived": False, "primary_language": "Python"},
                    "readme_and_license": {"license_status": "detected"},
                    "languages_and_tree": {"tree": {"test_paths": ["tests/"]}},
                },
                "report": {
                    "sections": [
                        {
                            "findings": [
                                {
                                    "claim_type": "fact",
                                    "evidence": [{"evidence_id": "repository-status"}],
                                },
                                {"claim_type": "needs_confirmation", "evidence": []},
                            ]
                        }
                    ],
                    "model_usage": [],
                },
            },
            {"status": "failed", "duration_ms": 300},
        ]
    )

    assert metrics.succeeded == 1
    assert metrics.failed == 1
    assert metrics.structured_output_rate == 0.5
    assert metrics.evidence_coverage_rate == 1
    assert metrics.field_accuracy == 1
    assert metrics.annotated_field_count == 4
    assert metrics.average_duration_ms == 200
    assert metrics.average_known_cost_usd is None
    assert metrics.cost_observation_count == 0


def test_metrics_handle_empty_results_without_inventing_values() -> None:
    metrics = calculate_metrics([])

    assert metrics.total == 0
    assert metrics.structured_output_rate == 0
    assert metrics.evidence_coverage_rate is None
    assert metrics.field_accuracy is None
    assert metrics.average_duration_ms is None
