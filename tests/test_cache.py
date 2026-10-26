import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from repolens.cache import CacheRepository, build_cache_key
from repolens.database import Database
from repolens.github_client import GitHubClient
from repolens.models import RepositoryRef
from repolens.settings import RepoLensSettings


def database_url(path: Any) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.mark.asyncio
async def test_database_initialize_is_idempotent(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "repolens.db"))

    await database.initialize()
    await database.initialize()

    async with database.connect() as connection:
        cursor = await connection.execute("PRAGMA user_version")
        version = await cursor.fetchone()
        cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = {row["name"] for row in await cursor.fetchall()}
    assert version[0] == 2
    assert {"response_cache", "analysis_tasks", "report_confirmations"} <= tables


@pytest.mark.asyncio
async def test_cache_returns_value_until_ttl_expires(tmp_path: Any) -> None:
    current = [datetime(2026, 10, 26, 8, 0, tzinfo=UTC)]
    database = Database(database_url(tmp_path / "cache.db"))
    await database.initialize()
    cache = CacheRepository(database, data_version="1", now=lambda: current[0])
    key = build_cache_key("GET", "/repos/owner/repo", {"page": 1}, "1")

    await cache.set(key, {"value": 42}, ttl_seconds=60)
    assert await cache.get(key) == {"value": 42}

    current[0] += timedelta(seconds=61)
    assert await cache.get(key) is None
    assert await cache.get(key) is None


@pytest.mark.asyncio
async def test_cache_discards_corrupt_or_wrong_version_rows(tmp_path: Any) -> None:
    now = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
    database = Database(database_url(tmp_path / "corrupt.db"))
    await database.initialize()
    async with database.connect() as connection:
        await connection.executemany(
            """
            INSERT INTO response_cache (
                cache_key, response_json, created_at, expires_at, data_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("corrupt", "not-json", now.isoformat(), (now + timedelta(hours=1)).isoformat(), "1"),
                ("old", "{}", now.isoformat(), (now + timedelta(hours=1)).isoformat(), "0"),
            ],
        )
        await connection.commit()
    cache = CacheRepository(database, data_version="1", now=lambda: now)

    assert await cache.get("corrupt") is None
    assert await cache.get("old") is None


@pytest.mark.asyncio
async def test_concurrent_cache_writes_do_not_lock_database(tmp_path: Any) -> None:
    database = Database(database_url(tmp_path / "concurrent.db"))
    await database.initialize()
    cache = CacheRepository(database, data_version="1")

    await asyncio.gather(*(cache.set(f"key-{i}", {"i": i}, 60) for i in range(20)))

    values = await asyncio.gather(*(cache.get(f"key-{i}") for i in range(20)))
    assert values == [{"i": i} for i in range(20)]


@pytest.mark.asyncio
async def test_github_client_uses_cache_without_second_http_call(
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "full_name": "owner/repo",
                "html_url": "https://github.com/owner/repo",
                "description": None,
                "default_branch": "main",
                "language": "Python",
                "stargazers_count": 1,
                "forks_count": 0,
                "open_issues_count": 0,
                "archived": False,
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2026-10-25T00:00:00Z",
                "pushed_at": "2026-10-25T00:00:00Z",
                "license": None,
            },
        )

    database = Database(database_url(tmp_path / "client-cache.db"))
    await database.initialize()
    cache = CacheRepository(database, data_version="1")
    settings = RepoLensSettings(
        request_max_retries=0,
        cache_metadata_ttl_seconds=60,
        _env_file=None,
    )
    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GitHubClient(settings, client=http_client, cache=cache, task_id="task-123")
        with caplog.at_level(logging.INFO, logger="repolens.github_client"):
            first = await client.get_repository_metadata(RepositoryRef(owner="owner", name="repo"))
            second = await client.get_repository_metadata(RepositoryRef(owner="owner", name="repo"))

    assert first == second
    assert calls == 1
    assert '"cache_hit":true' in caplog.text
    assert '"task_id":"task-123"' in caplog.text


@pytest.mark.asyncio
async def test_cache_failure_does_not_discard_valid_api_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenCache:
        data_version = "1"

        async def get(self, _: str) -> Any:
            raise OSError("database unavailable")

        async def set(self, _: str, __: Any, ___: int) -> None:
            raise OSError("database unavailable")

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "full_name": "owner/repo",
                "html_url": "https://github.com/owner/repo",
                "description": None,
                "default_branch": "main",
                "language": "Python",
                "stargazers_count": 1,
                "forks_count": 0,
                "open_issues_count": 0,
                "archived": False,
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2026-10-25T00:00:00Z",
                "pushed_at": "2026-10-25T00:00:00Z",
                "license": None,
            },
        )

    settings = RepoLensSettings(request_max_retries=0, _env_file=None)
    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GitHubClient(settings, client=http_client, cache=BrokenCache())
        with caplog.at_level(logging.WARNING, logger="repolens.github_client"):
            result = await client.get_repository_metadata(RepositoryRef(owner="owner", name="repo"))

    assert result.full_name == "owner/repo"
    assert caplog.text.count('"event":"cache_failure"') == 2


@pytest.mark.asyncio
async def test_github_client_factory_initializes_and_uses_cache(tmp_path: Any) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "full_name": "owner/repo",
                "html_url": "https://github.com/owner/repo",
                "description": None,
                "default_branch": "main",
                "language": "Python",
                "stargazers_count": 1,
                "forks_count": 0,
                "open_issues_count": 0,
                "archived": False,
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2026-10-25T00:00:00Z",
                "pushed_at": "2026-10-25T00:00:00Z",
                "license": None,
            },
        )

    config = RepoLensSettings(
        database_url=database_url(tmp_path / "factory.db"),
        request_max_retries=0,
        _env_file=None,
    )
    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ) as http_client:
        client = await GitHubClient.create(config, client=http_client)
        await client.get_repository_metadata(RepositoryRef(owner="owner", name="repo"))
        await client.get_repository_metadata(RepositoryRef(owner="owner", name="repo"))

    assert calls == 1
    assert (tmp_path / "factory.db").exists()
