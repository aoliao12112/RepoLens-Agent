import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from test_rule_analysis import NOW, active_snapshot

from repolens.agent_orchestration import AgentOrchestrator
from repolens.analysis_service import AnalysisService
from repolens.confirmation import ConfirmationRepository
from repolens.database import Database
from repolens.errors import UpstreamTimeoutError
from repolens.models import (
    AnalysisRequest,
    AnalysisTask,
    ReportConfirmation,
    ReportStatus,
    RepositoryRef,
    RepositorySnapshot,
    TaskStatus,
)
from repolens.rule_analysis import RuleBasedAnalyzer
from repolens.settings import RepoLensSettings
from repolens.tasks import TaskRepository


def database_url(path: Any) -> str:
    return f"sqlite:///{path.as_posix()}"


class FakeCollector:
    def __init__(self, snapshot: RepositorySnapshot, *, fail: bool = False) -> None:
        self.snapshot = snapshot
        self.fail = fail
        self.closed = False

    async def collect_repository_snapshot(self, repo: RepositoryRef) -> RepositorySnapshot:
        if self.fail:
            raise UpstreamTimeoutError()
        return self.snapshot.model_copy(deep=True)

    async def close(self) -> None:
        self.closed = True


async def build_service(tmp_path: Any, *, collector: FakeCollector):
    database = Database(database_url(tmp_path / "service.db"))
    await database.initialize()
    tasks = TaskRepository(database)

    async def factory(_: str) -> FakeCollector:
        return collector

    service = AnalysisService(
        settings=RepoLensSettings(_env_file=None),
        tasks=tasks,
        confirmations=ConfirmationRepository(database),
        orchestrator=AgentOrchestrator(
            rule_analyzer=RuleBasedAnalyzer(now=lambda: NOW),
            reviewer=None,
        ),
        collector_factory=factory,
    )
    return service, tasks


@pytest.mark.asyncio
async def test_full_service_lifecycle_without_model(tmp_path: Any) -> None:
    collector = FakeCollector(active_snapshot())
    service, tasks = await build_service(tmp_path, collector=collector)
    task = await service.create_task(
        AnalysisRequest(
            repository_url="https://github.com/owner/repo",
            target="Python Agent 二次开发",
            enable_model=False,
        )
    )

    await service.run(task.task_id)

    stored = await service.get_task(task.task_id)
    report = await service.get_report(task.task_id)
    assert stored.status == TaskStatus.awaiting_confirmation
    assert stored.progress == 95
    assert report is not None and report.evidence_verified is True
    assert collector.closed is True

    confirmation = ReportConfirmation(
        task_id=task.task_id,
        target_confirmed=True,
        accepted_risk_ids=[
            risk.finding_id
            for risk in report.risks
            if risk.severity.value in {"high", "critical"}
        ],
        confirmed_at=NOW,
    )
    final_report = await service.confirm(task.task_id, confirmation)

    assert final_report.status == ReportStatus.confirmed
    assert (await tasks.get(task.task_id)).status == TaskStatus.completed


@pytest.mark.asyncio
async def test_collector_failure_is_persisted(tmp_path: Any) -> None:
    collector = FakeCollector(active_snapshot(), fail=True)
    service, _ = await build_service(tmp_path, collector=collector)
    task = await service.create_task(
        AnalysisRequest(
            repository_url="https://github.com/owner/repo",
            target="评估项目维护状态",
        )
    )

    await service.run(task.task_id)
    stored = await service.get_task(task.task_id)

    assert stored.status == TaskStatus.failed
    assert stored.error is not None
    assert stored.error.code == "upstream_timeout"
    assert stored.error.retryable is True
    assert collector.closed is True


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_separate_state(tmp_path: Any) -> None:
    collector = FakeCollector(active_snapshot())
    service, _ = await build_service(tmp_path, collector=collector)
    first = await service.create_task(
        AnalysisRequest(
            repository_url="https://github.com/owner/repo",
            target="Python Agent 项目",
            enable_model=False,
        )
    )
    second = await service.create_task(
        AnalysisRequest(
            repository_url="https://github.com/owner/repo",
            target="检查维护情况",
            enable_model=False,
        )
    )

    await asyncio.gather(service.run(first.task_id), service.run(second.task_id))
    first_report = await service.get_report(first.task_id)
    second_report = await service.get_report(second.task_id)

    assert first_report is not None and second_report is not None
    assert first_report.task_id != second_report.task_id
    assert first_report.target == "Python Agent 项目"
    assert second_report.target == "检查维护情况"


@pytest.mark.asyncio
async def test_incomplete_tasks_are_failed_on_restart(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "restart.db"))
    await database.initialize()
    tasks = TaskRepository(database)
    task = AnalysisTask(
        request=AnalysisRequest(
            repository_url="https://github.com/owner/repo",
            target="检查维护情况",
        ),
    )
    await tasks.create(task)

    changed = await tasks.fail_incomplete_tasks(
        updated_at=datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    )
    stored = await tasks.get(task.task_id)

    assert changed == 1
    assert stored.status == TaskStatus.failed
    assert stored.error is not None and stored.error.code == "process_interrupted"
