from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from .database import Database
from .errors import InvalidTaskTransitionError, TaskNotFoundError
from .models import (
    AnalysisRequest,
    AnalysisTask,
    DueDiligenceReport,
    TaskError,
    TaskStatus,
)

_ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.queued: {TaskStatus.collecting, TaskStatus.failed},
    TaskStatus.collecting: {TaskStatus.reviewing, TaskStatus.failed},
    TaskStatus.reviewing: {TaskStatus.verifying, TaskStatus.failed},
    TaskStatus.verifying: {
        TaskStatus.awaiting_confirmation,
        TaskStatus.completed,
        TaskStatus.failed,
    },
    TaskStatus.awaiting_confirmation: {TaskStatus.completed, TaskStatus.failed},
    TaskStatus.completed: set(),
    TaskStatus.failed: set(),
}


class TaskRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(self, task: AnalysisTask) -> None:
        async with self._database.write_lock, self._database.connect() as connection:
            await connection.execute(
                """
                INSERT INTO analysis_tasks (
                    task_id, status, progress, current_stage, error_json,
                    request_json, report_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    str(task.task_id),
                    task.status.value,
                    task.progress,
                    task.current_stage,
                    task.error.model_dump_json() if task.error else None,
                    task.request.model_dump_json(),
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )
            await connection.commit()

    async def get(self, task_id: UUID) -> AnalysisTask:
        async with self._database.connect() as connection:
            cursor = await connection.execute(
                """
                SELECT task_id, status, progress, current_stage, error_json,
                       request_json, created_at, updated_at
                FROM analysis_tasks
                WHERE task_id = ?
                """,
                (str(task_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            raise TaskNotFoundError(str(task_id))
        return AnalysisTask(
            task_id=row["task_id"],
            request=AnalysisRequest.model_validate_json(row["request_json"]),
            status=row["status"],
            progress=row["progress"],
            current_stage=row["current_stage"],
            error=(TaskError.model_validate_json(row["error_json"]) if row["error_json"] else None),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def transition(
        self,
        task_id: UUID,
        status: TaskStatus,
        *,
        progress: int,
        current_stage: str | None = None,
        error: TaskError | None = None,
        updated_at: datetime | None = None,
    ) -> AnalysisTask:
        task = await self.get(task_id)
        if status not in _ALLOWED_TRANSITIONS[task.status]:
            raise InvalidTaskTransitionError(task.status.value, status.value)
        if progress < task.progress:
            raise ValueError("任务进度不能倒退")
        if status == TaskStatus.completed:
            progress = 100
        changed_at = updated_at or datetime.now(UTC)
        updated = AnalysisTask(
            task_id=task.task_id,
            request=task.request,
            status=status,
            progress=progress,
            current_stage=current_stage,
            error=error,
            created_at=task.created_at,
            updated_at=changed_at,
        )
        async with self._database.write_lock, self._database.connect() as connection:
            cursor = await connection.execute(
                """
                UPDATE analysis_tasks
                SET status = ?, progress = ?, current_stage = ?, error_json = ?, updated_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    updated.status.value,
                    updated.progress,
                    updated.current_stage,
                    updated.error.model_dump_json() if updated.error else None,
                    updated.updated_at.isoformat(),
                    str(task_id),
                    task.status.value,
                ),
            )
            if cursor.rowcount != 1:
                await connection.rollback()
                latest = await self.get(task_id)
                raise InvalidTaskTransitionError(latest.status.value, status.value)
            await connection.commit()
        return updated

    async def save_report(self, report: DueDiligenceReport) -> None:
        async with self._database.write_lock, self._database.connect() as connection:
            cursor = await connection.execute(
                "UPDATE analysis_tasks SET report_json = ? WHERE task_id = ?",
                (report.model_dump_json(), str(report.task_id)),
            )
            if cursor.rowcount != 1:
                await connection.rollback()
                raise TaskNotFoundError(str(report.task_id))
            await connection.commit()

    async def get_report(self, task_id: UUID) -> DueDiligenceReport | None:
        async with self._database.connect() as connection:
            cursor = await connection.execute(
                "SELECT report_json FROM analysis_tasks WHERE task_id = ?",
                (str(task_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            raise TaskNotFoundError(str(task_id))
        if row["report_json"] is None:
            return None
        return DueDiligenceReport.model_validate_json(row["report_json"])

    async def fail_incomplete_tasks(self, *, updated_at: datetime | None = None) -> int:
        changed_at = updated_at or datetime.now(UTC)
        error = TaskError(
            code="process_interrupted",
            message="服务进程在任务完成前退出，请重新创建分析任务",
            retryable=True,
        )
        active_statuses = (
            TaskStatus.queued.value,
            TaskStatus.collecting.value,
            TaskStatus.reviewing.value,
            TaskStatus.verifying.value,
        )
        placeholders = ",".join("?" for _ in active_statuses)
        async with self._database.write_lock, self._database.connect() as connection:
            cursor = await connection.execute(
                f"""
                UPDATE analysis_tasks
                SET status = ?, error_json = ?, current_stage = ?, updated_at = ?
                WHERE status IN ({placeholders})
                """,
                (
                    TaskStatus.failed.value,
                    error.model_dump_json(),
                    "任务因服务重启中断",
                    changed_at.isoformat(),
                    *active_statuses,
                ),
            )
            await connection.commit()
            return max(cursor.rowcount, 0)
