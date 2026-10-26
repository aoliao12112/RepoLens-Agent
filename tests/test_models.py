from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from repolens.models import (
    AnalysisRequest,
    AnalysisTask,
    ClaimType,
    DueDiligenceReport,
    Evidence,
    EvidenceKind,
    ReviewFinding,
    RiskSeverity,
    TaskError,
    TaskStatus,
)

EXAMPLE_REPORT = Path(__file__).parents[1] / "data" / "examples" / "report.json"


def test_example_report_is_valid_and_serializable() -> None:
    report = DueDiligenceReport.model_validate_json(EXAMPLE_REPORT.read_text(encoding="utf-8"))

    assert report.task_id == UUID("11111111-1111-4111-8111-111111111111")
    assert len(report.sections) == 4
    assert DueDiligenceReport.model_validate_json(report.model_dump_json()) == report


def test_score_must_be_between_zero_and_one_hundred() -> None:
    raw = EXAMPLE_REPORT.read_text(encoding="utf-8").replace(
        '"overall_score": 82', '"overall_score": 101'
    )

    with pytest.raises(ValidationError, match="less than or equal to 100"):
        DueDiligenceReport.model_validate_json(raw)


def test_fact_finding_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="事实结论必须至少包含一条证据"):
        ReviewFinding(
            finding_id="archived",
            title="仓库已归档",
            description="仓库处于只读归档状态",
            severity=RiskSeverity.high,
            claim_type=ClaimType.fact,
        )


def test_unverified_risk_can_be_marked_for_confirmation() -> None:
    finding = ReviewFinding(
        finding_id="license-review",
        title="许可证需要确认",
        description="API 未识别许可证，需要人工检查文件",
        severity=RiskSeverity.high,
        claim_type=ClaimType.needs_confirmation,
    )

    assert finding.evidence == []


def test_evidence_rejects_invalid_source_url() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="bad-url",
            claim="示例",
            kind=EvidenceKind.web_page,
            source_url="not-a-url",
            field_or_path="README.md",
        )


def test_failed_task_requires_error_summary() -> None:
    request = AnalysisRequest(
        repository_url="https://github.com/openai/openai-agents-python",
        target="评估是否适合二次开发",
    )

    with pytest.raises(ValidationError, match="失败任务必须包含错误摘要"):
        AnalysisTask(request=request, status=TaskStatus.failed)

    task = AnalysisTask(
        request=request,
        status=TaskStatus.failed,
        error=TaskError(code="upstream_timeout", message="请求超时", retryable=True),
    )
    assert task.error is not None


def test_datetime_must_include_timezone() -> None:
    raw = EXAMPLE_REPORT.read_text(encoding="utf-8").replace(
        '"generated_at": "2026-10-26T08:00:00+00:00"',
        '"generated_at": "2026-10-26T08:00:00"',
    )

    with pytest.raises(ValidationError, match="时间必须包含时区"):
        DueDiligenceReport.model_validate_json(raw)


def test_confirmed_report_requires_confirmation_time() -> None:
    raw = EXAMPLE_REPORT.read_text(encoding="utf-8").replace(
        '"status": "awaiting_confirmation"', '"status": "confirmed"', 1
    )

    with pytest.raises(ValidationError, match="已确认报告必须记录确认时间"):
        DueDiligenceReport.model_validate_json(raw)
