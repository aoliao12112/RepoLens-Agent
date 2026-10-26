from datetime import UTC, datetime
from typing import Any

import pytest
from test_rule_analysis import NOW, TASK_ID, active_snapshot

from repolens.confirmation import ConfirmationRepository, ConfirmationService
from repolens.database import Database
from repolens.errors import ConfirmationError
from repolens.evidence_verification import EvidenceVerifier
from repolens.models import (
    AnalysisRequest,
    AnalysisTask,
    ReportConfirmation,
    ReportStatus,
)
from repolens.rule_analysis import RuleBasedAnalyzer
from repolens.tasks import TaskRepository


def database_url(path: Any) -> str:
    return f"sqlite:///{path.as_posix()}"


def verified_archived_report():
    snapshot = active_snapshot()
    snapshot.repository.archived = True
    report = RuleBasedAnalyzer(now=lambda: NOW).analyze(
        snapshot, "Python Agent 二次开发", TASK_ID
    )
    return EvidenceVerifier().verify(report, snapshot)


def test_confirmation_requires_verified_report_and_target_acceptance() -> None:
    snapshot = active_snapshot()
    unverified = RuleBasedAnalyzer(now=lambda: NOW).analyze(
        snapshot, "Python Agent 二次开发", TASK_ID
    )
    confirmation = ReportConfirmation(
        task_id=TASK_ID,
        target_confirmed=True,
        confirmed_at=NOW,
    )

    with pytest.raises(ConfirmationError, match="尚未通过证据校验"):
        ConfirmationService.confirm(unverified, confirmation)

    verified = EvidenceVerifier().verify(unverified, snapshot)
    with pytest.raises(ConfirmationError, match="确认分析目标"):
        ConfirmationService.confirm(
            verified,
            confirmation.model_copy(update={"target_confirmed": False}),
        )


def test_confirmation_requires_all_high_risks() -> None:
    report = verified_archived_report()
    confirmation = ReportConfirmation(
        task_id=TASK_ID,
        target_confirmed=True,
        accepted_risk_ids=[],
        confirmed_at=NOW,
    )

    with pytest.raises(ConfirmationError) as exc_info:
        ConfirmationService.confirm(report, confirmation)

    assert "maintenance.archived" in exc_info.value.details["missing_risk_ids"]


@pytest.mark.asyncio
async def test_confirmation_is_persisted_and_report_becomes_final(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "confirmation.db"))
    await database.initialize()
    task_repository = TaskRepository(database)
    await task_repository.create(
        AnalysisTask(
            task_id=TASK_ID,
            request=AnalysisRequest(
                repository_url="https://github.com/owner/repo",
                target="Python Agent 二次开发",
            ),
            created_at=datetime(2026, 10, 26, 8, 0, tzinfo=UTC),
            updated_at=datetime(2026, 10, 26, 8, 0, tzinfo=UTC),
        )
    )
    repository = ConfirmationRepository(database)
    report = verified_archived_report()
    required = [
        risk.finding_id
        for risk in report.risks
        if risk.severity.value in {"high", "critical"}
    ]
    confirmation = ReportConfirmation(
        task_id=TASK_ID,
        target_confirmed=True,
        accepted_risk_ids=required,
        notes="已知仓库归档，仍用于只读研究。",
        confirmed_at=NOW,
    )

    final_report = await ConfirmationService().confirm_and_record(
        report, confirmation, repository
    )

    assert final_report.status == ReportStatus.confirmed
    assert final_report.confirmed_at == NOW
    assert await repository.get(TASK_ID) == confirmation
