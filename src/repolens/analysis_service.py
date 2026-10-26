from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from .agent_orchestration import AgentOrchestrator
from .confirmation import ConfirmationRepository, ConfirmationService
from .errors import ConfirmationError, RepoLensError
from .evidence_verification import EvidenceVerifier
from .github_client import GitHubClient
from .github_url import parse_github_repository_url
from .models import (
    AnalysisRequest,
    AnalysisTask,
    DueDiligenceReport,
    ReportConfirmation,
    RepositoryRef,
    RepositorySnapshot,
    TaskError,
    TaskStatus,
)
from .settings import RepoLensSettings
from .tasks import TaskRepository

logger = logging.getLogger(__name__)


class SnapshotCollector(Protocol):
    async def collect_repository_snapshot(self, repo: RepositoryRef) -> RepositorySnapshot: ...

    async def close(self) -> None: ...


CollectorFactory = Callable[[str], Awaitable[SnapshotCollector]]


class AnalysisService:
    def __init__(
        self,
        *,
        settings: RepoLensSettings,
        tasks: TaskRepository,
        confirmations: ConfirmationRepository,
        orchestrator: AgentOrchestrator,
        verifier: EvidenceVerifier | None = None,
        collector_factory: CollectorFactory | None = None,
    ) -> None:
        self._settings = settings
        self._tasks = tasks
        self._confirmations = confirmations
        self._orchestrator = orchestrator
        self._verifier = verifier or EvidenceVerifier()
        self._collector_factory = collector_factory or self._default_collector_factory

    async def create_task(self, request: AnalysisRequest) -> AnalysisTask:
        parse_github_repository_url(str(request.repository_url))
        task = AnalysisTask(request=request)
        await self._tasks.create(task)
        return task

    async def run(self, task_id: UUID) -> None:
        task = await self._tasks.get(task_id)
        collector: SnapshotCollector | None = None
        try:
            await self._tasks.transition(
                task_id,
                TaskStatus.collecting,
                progress=10,
                current_stage="正在采集 GitHub 仓库数据",
            )
            repo = parse_github_repository_url(str(task.request.repository_url))
            collector = await self._collector_factory(str(task_id))
            snapshot = await collector.collect_repository_snapshot(repo)

            await self._tasks.transition(
                task_id,
                TaskStatus.reviewing,
                progress=55,
                current_stage="正在执行规则与专项审查",
            )
            report = await self._orchestrator.analyze(
                snapshot,
                task.request.target,
                task_id,
                enable_model=task.request.enable_model,
            )

            await self._tasks.transition(
                task_id,
                TaskStatus.verifying,
                progress=85,
                current_stage="正在校验报告证据",
            )
            verified = self._verifier.verify(report, snapshot)
            await self._tasks.save_report(verified)
            await self._tasks.transition(
                task_id,
                TaskStatus.awaiting_confirmation,
                progress=95,
                current_stage="等待用户确认目标与风险",
            )
        except Exception as exc:  # noqa: BLE001 - background task must persist every failure.
            await self._record_failure(task_id, exc)
        finally:
            if collector is not None:
                try:
                    await collector.close()
                except Exception as exc:  # noqa: BLE001 - cleanup cannot change task outcome.
                    logger.warning(
                        json.dumps(
                            {
                                "event": "collector_close_failed",
                                "task_id": str(task_id),
                                "error_type": type(exc).__name__,
                            },
                            separators=(",", ":"),
                        )
                    )

    async def get_task(self, task_id: UUID) -> AnalysisTask:
        return await self._tasks.get(task_id)

    async def get_report(self, task_id: UUID) -> DueDiligenceReport | None:
        return await self._tasks.get_report(task_id)

    async def confirm(
        self,
        task_id: UUID,
        confirmation: ReportConfirmation,
    ) -> DueDiligenceReport:
        if confirmation.task_id != task_id:
            raise ConfirmationError("确认记录与路径中的任务 ID 不一致")
        report = await self._tasks.get_report(task_id)
        if report is None:
            raise ConfirmationError("任务尚未生成报告")
        final_report = await ConfirmationService().confirm_and_record(
            report,
            confirmation,
            self._confirmations,
        )
        await self._tasks.save_report(final_report)
        await self._tasks.transition(
            task_id,
            TaskStatus.completed,
            progress=100,
            current_stage="分析报告已确认",
        )
        return final_report

    async def _default_collector_factory(self, task_id: str) -> SnapshotCollector:
        return await GitHubClient.create(self._settings, task_id=task_id)

    async def _record_failure(self, task_id: UUID, exc: Exception) -> None:
        if isinstance(exc, RepoLensError):
            error = TaskError(
                code=exc.code.value,
                message=exc.message,
                retryable=exc.retryable,
            )
        else:
            error = TaskError(
                code="internal_analysis_error",
                message="分析任务执行失败，请查看服务日志",
                retryable=False,
            )
        logger.error(
            json.dumps(
                {
                    "event": "analysis_task_failed",
                    "task_id": str(task_id),
                    "error_type": type(exc).__name__,
                },
                separators=(",", ":"),
            )
        )
        current = await self._tasks.get(task_id)
        if current.status not in {TaskStatus.completed, TaskStatus.failed}:
            await self._tasks.transition(
                task_id,
                TaskStatus.failed,
                progress=current.progress,
                current_stage="分析任务失败",
                error=error,
            )
