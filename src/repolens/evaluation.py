from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, field_validator

from .evidence_verification import EvidenceVerifier
from .github_client import GitHubClient
from .github_url import parse_github_repository_url
from .models import LicenseStatus, require_timezone
from .rule_analysis import RuleBasedAnalyzer
from .settings import RepoLensSettings


class EvaluationAnnotation(BaseModel):
    archived: bool
    primary_language: str | None
    license_status: LicenseStatus
    has_tests: bool
    annotated_at: datetime
    source_urls: list[HttpUrl] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=1_000)

    _annotated_at_has_timezone = field_validator("annotated_at")(require_timezone)


class EvaluationCase(BaseModel):
    repository_url: HttpUrl
    target: str = Field(min_length=3, max_length=2_000)
    category: str = Field(min_length=1, max_length=80)
    annotation: EvaluationAnnotation | None = None


class EvaluationMetrics(BaseModel):
    total: int
    succeeded: int
    failed: int
    structured_output_rate: float
    evidence_coverage_rate: float | None
    field_accuracy: float | None
    annotated_field_count: int
    average_duration_ms: float | None
    average_known_cost_usd: float | None
    cost_observation_count: int


def load_dataset(path: Path) -> list[EvaluationCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[EvaluationCase]).validate_python(payload)


def calculate_metrics(results: list[dict[str, Any]]) -> EvaluationMetrics:
    succeeded = [item for item in results if item.get("status") == "success"]
    evidence_claims = 0
    evidence_backed = 0
    exact_fields = 0
    annotated_fields = 0
    durations: list[float] = []
    known_costs: list[float] = []

    for item in results:
        if isinstance(item.get("duration_ms"), (int, float)):
            durations.append(float(item["duration_ms"]))
        report = item.get("report")
        if not isinstance(report, dict):
            continue
        for section in report.get("sections", []):
            for finding in section.get("findings", []):
                if finding.get("claim_type") != "needs_confirmation":
                    evidence_claims += 1
                    if finding.get("evidence"):
                        evidence_backed += 1
        for usage in report.get("model_usage", []):
            cost = usage.get("estimated_cost_usd")
            if isinstance(cost, (int, float)):
                known_costs.append(float(cost))

        annotation = item.get("annotation")
        snapshot = item.get("snapshot")
        if not isinstance(annotation, dict) or not isinstance(snapshot, dict):
            continue
        repository = snapshot.get("repository", {})
        content = snapshot.get("readme_and_license", {})
        tree = snapshot.get("languages_and_tree", {}).get("tree", {})
        comparisons = (
            (repository.get("archived"), annotation.get("archived")),
            (repository.get("primary_language"), annotation.get("primary_language")),
            (content.get("license_status"), annotation.get("license_status")),
            (bool(tree.get("test_paths")), annotation.get("has_tests")),
        )
        for actual, expected in comparisons:
            annotated_fields += 1
            exact_fields += int(actual == expected)

    total = len(results)
    return EvaluationMetrics(
        total=total,
        succeeded=len(succeeded),
        failed=total - len(succeeded),
        structured_output_rate=len(succeeded) / total if total else 0,
        evidence_coverage_rate=(evidence_backed / evidence_claims if evidence_claims else None),
        field_accuracy=(exact_fields / annotated_fields if annotated_fields else None),
        annotated_field_count=annotated_fields,
        average_duration_ms=(sum(durations) / len(durations) if durations else None),
        average_known_cost_usd=(sum(known_costs) / len(known_costs) if known_costs else None),
        cost_observation_count=len(known_costs),
    )


async def evaluate_case(case: EvaluationCase, settings: RepoLensSettings) -> dict[str, Any]:
    started = time.perf_counter()
    client: GitHubClient | None = None
    result: dict[str, Any] = {
        "repository_url": str(case.repository_url),
        "target": case.target,
        "category": case.category,
        "annotation": case.annotation.model_dump(mode="json") if case.annotation else None,
    }
    try:
        task_id = uuid4()
        client = await GitHubClient.create(settings, task_id=str(task_id))
        snapshot = await client.collect_repository_snapshot(
            parse_github_repository_url(str(case.repository_url))
        )
        report = RuleBasedAnalyzer().analyze(snapshot, case.target, task_id)
        report = EvidenceVerifier().verify(report, snapshot)
        result.update(
            status="success",
            snapshot=snapshot.model_dump(mode="json"),
            report=report.model_dump(mode="json"),
        )
    except Exception as exc:  # noqa: BLE001 - each failed case must be preserved.
        result.update(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
    finally:
        if client is not None:
            await client.close()
        result["duration_ms"] = round((time.perf_counter() - started) * 1_000, 2)
    return result


async def run_evaluation(
    dataset_path: Path,
    output_directory: Path,
    *,
    limit: int | None = None,
) -> EvaluationMetrics:
    cases = load_dataset(dataset_path)
    if limit is not None:
        cases = cases[:limit]
    output_directory.mkdir(parents=True, exist_ok=True)
    settings = RepoLensSettings()
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        result = await evaluate_case(case, settings)
        results.append(result)
        name = parse_github_repository_url(str(case.repository_url)).full_name.replace("/", "__")
        (output_directory / f"{index:02d}__{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    metrics = calculate_metrics(results)
    (output_directory / "metrics.json").write_text(
        metrics.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reproducible RepoLens evaluations")
    parser.add_argument("--dataset", type=Path, default=Path("data/evaluation/repositories.json"))
    parser.add_argument("--output", type=Path, default=Path("data/evaluation/results/latest"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    cases = load_dataset(args.dataset)
    if args.validate_only:
        annotated = sum(case.annotation is not None for case in cases)
        print(json.dumps({"cases": len(cases), "annotated": annotated}, ensure_ascii=False))
        return
    metrics = asyncio.run(run_evaluation(args.dataset, args.output, limit=args.limit))
    print(metrics.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
