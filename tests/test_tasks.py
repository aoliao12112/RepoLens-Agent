import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from repolens.database import Database
from repolens.errors import InvalidTaskTransitionError, TaskNotFoundError
from repolens.models import (
    AnalysisRequest,
    AnalysisTask,
    DueDiligenceReport,
    TaskError,
    TaskStatus,
)
from repolens.tasks import TaskRepository

EXAMPLE_REPORT = Path(__file__).parents[1] / "data" / "examples" / "report.json"
TASK_ID = UUID("11111111-1111-4111-8111-111111111111")


def database_url(path: Any) -> str:
    return f"sqlite:///{path.as_posix()}"


def task() -> AnalysisTask:
    return AnalysisTask(
        task_id=TASK_ID,
        request=AnalysisRequest(
            repository_url="https://github.com/owner/repo",
            target="评估是否适合二次开发",
        ),
        created_at=datetime(2026, 10, 26, 8, 0, tzinfo=UTC),
        updated_at=datetime(2026, 10, 26, 8, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_task_lifecycle_and_report_round_trip(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "tasks.db"))
    await database.initialize()
    repository = TaskRepository(database)
    await repository.create(task())

    collecting = await repository.transition(
        TASK_ID, TaskStatus.collecting, progress=10, current_stage="采集仓库数据"
    )
    assert collecting.progress == 10
    await repository.transition(TASK_ID, TaskStatus.reviewing, progress=55)
    await repository.transition(TASK_ID, TaskStatus.verifying, progress=80)
    await repository.transition(TASK_ID, TaskStatus.awaiting_confirmation, progress=95)
    completed = await repository.transition(TASK_ID, TaskStatus.completed, progress=99)
    assert completed.progress == 100

    report = DueDiligenceReport.model_validate_json(EXAMPLE_REPORT.read_text(encoding="utf-8"))
    await repository.save_report(report)
    assert await repository.get_report(TASK_ID) == report
    assert (await repository.get(TASK_ID)).status == TaskStatus.completed


@pytest.mark.asyncio
async def test_invalid_transition_and_progress_regression_are_rejected(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "invalid.db"))
    await database.initialize()
    repository = TaskRepository(database)
    await repository.create(task())

    with pytest.raises(InvalidTaskTransitionError):
        await repository.transition(TASK_ID, TaskStatus.reviewing, progress=20)

    await repository.transition(TASK_ID, TaskStatus.collecting, progress=20)
    with pytest.raises(ValueError, match="不能倒退"):
        await repository.transition(TASK_ID, TaskStatus.reviewing, progress=10)


@pytest.mark.asyncio
async def test_failed_task_persists_error_summary(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "failed.db"))
    await database.initialize()
    repository = TaskRepository(database)
    await repository.create(task())
    error = TaskError(code="upstream_timeout", message="GitHub 请求超时", retryable=True)

    await repository.transition(TASK_ID, TaskStatus.failed, progress=5, error=error)
    stored = await repository.get(TASK_ID)

    assert stored.status == TaskStatus.failed
    assert stored.error == error


@pytest.mark.asyncio
async def test_missing_task_raises_domain_error(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "missing.db"))
    await database.initialize()
    repository = TaskRepository(database)

    with pytest.raises(TaskNotFoundError):
        await repository.get(uuid4())


@pytest.mark.asyncio
async def test_concurrent_transition_allows_only_one_winner(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "race.db"))
    await database.initialize()
    repository = TaskRepository(database)
    await repository.create(task())

    results = await asyncio.gather(
        repository.transition(TASK_ID, TaskStatus.collecting, progress=10),
        repository.transition(TASK_ID, TaskStatus.collecting, progress=10),
        return_exceptions=True,
    )

    assert sum(isinstance(result, AnalysisTask) for result in results) == 1
    assert sum(isinstance(result, InvalidTaskTransitionError) for result in results) == 1
