from __future__ import annotations

from uuid import UUID

from .database import Database
from .errors import ConfirmationError, TaskNotFoundError
from .models import (
    DueDiligenceReport,
    ReportConfirmation,
    ReportStatus,
    RiskSeverity,
)


class ConfirmationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(self, confirmation: ReportConfirmation) -> None:
        async with self._database.write_lock, self._database.connect() as connection:
            cursor = await connection.execute(
                "SELECT 1 FROM analysis_tasks WHERE task_id = ?",
                (str(confirmation.task_id),),
            )
            if await cursor.fetchone() is None:
                raise TaskNotFoundError(str(confirmation.task_id))
            await connection.execute(
                """
                INSERT INTO report_confirmations (task_id, confirmation_json, confirmed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    confirmation_json = excluded.confirmation_json,
                    confirmed_at = excluded.confirmed_at
                """,
                (
                    str(confirmation.task_id),
                    confirmation.model_dump_json(),
                    confirmation.confirmed_at.isoformat(),
                ),
            )
            await connection.commit()

    async def get(self, task_id: UUID) -> ReportConfirmation | None:
        async with self._database.connect() as connection:
            cursor = await connection.execute(
                "SELECT confirmation_json FROM report_confirmations WHERE task_id = ?",
                (str(task_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ReportConfirmation.model_validate_json(row["confirmation_json"])


class ConfirmationService:
    @staticmethod
    def confirm(
        report: DueDiligenceReport,
        confirmation: ReportConfirmation,
    ) -> DueDiligenceReport:
        if report.task_id != confirmation.task_id:
            raise ConfirmationError("确认记录与报告任务不匹配")
        if report.status != ReportStatus.awaiting_confirmation:
            raise ConfirmationError("只有待确认报告可以执行确认")
        if not report.evidence_verified:
            raise ConfirmationError("报告尚未通过证据校验")
        if not confirmation.target_confirmed:
            raise ConfirmationError("必须先确认分析目标")

        risk_ids = {risk.finding_id for risk in report.risks}
        accepted = set(confirmation.accepted_risk_ids)
        unknown = accepted - risk_ids
        if unknown:
            raise ConfirmationError(
                "确认记录包含不存在的风险 ID",
                details={"unknown_risk_ids": sorted(unknown)},
            )
        required = {
            risk.finding_id
            for risk in report.risks
            if risk.severity in {RiskSeverity.high, RiskSeverity.critical}
        }
        missing = required - accepted
        if missing:
            raise ConfirmationError(
                "必须确认全部高风险和严重风险后才能完成报告",
                details={"missing_risk_ids": sorted(missing)},
            )
        return DueDiligenceReport.model_validate(
            {
                **report.model_dump(),
                "status": ReportStatus.confirmed,
                "confirmed_at": confirmation.confirmed_at,
            }
        )

    async def confirm_and_record(
        self,
        report: DueDiligenceReport,
        confirmation: ReportConfirmation,
        repository: ConfirmationRepository,
    ) -> DueDiligenceReport:
        confirmed = self.confirm(report, confirmation)
        await repository.save(confirmation)
        return confirmed
