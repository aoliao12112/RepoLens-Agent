import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from repolens.errors import (
    GitHubRateLimitError,
    RepositoryUnavailableError,
    UpstreamResponseError,
    UpstreamTemporaryError,
    UpstreamTimeoutError,
)
from repolens.github_client import GitHubClient
from repolens.models import RepositoryRef
from repolens.settings import RepoLensSettings

Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


def repository_payload() -> dict[str, Any]:
    return {
        "full_name": "openai/openai-agents-python",
        "html_url": "https://github.com/openai/openai-agents-python",
        "description": "Agents SDK",
        "default_branch": "main",
        "language": "Python",
        "stargazers_count": 100,
        "forks_count": 10,
        "open_issues_count": 5,
        "archived": False,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2026-10-25T12:00:00Z",
        "pushed_at": "2026-10-25T10:00:00Z",
        "license": {"spdx_id": "MIT"},
    }


def make_http_client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )


def make_settings(**overrides: Any) -> RepoLensSettings:
    values = {
        "request_timeout_seconds": 1,
        "request_max_retries": 2,
        "retry_base_delay_seconds": 0.01,
        "retry_max_delay_seconds": 0.1,
        "max_concurrency": 2,
        "_env_file": None,
    }
    values.update(overrides)
    return RepoLensSettings(**values)


@pytest.mark.asyncio
async def test_get_repository_metadata_maps_response_and_headers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert request.url.path == "/repos/openai/openai-agents-python"
        return httpx.Response(200, json=repository_payload())

    http_client = make_http_client(handler)
    client = GitHubClient(make_settings(), client=http_client)
    metadata = await client.get_repository_metadata(
        RepositoryRef(owner="openai", name="openai-agents-python")
    )

    assert metadata.full_name == "openai/openai-agents-python"
    assert metadata.created_at == datetime(2025, 1, 1, tzinfo=UTC)
    await client.close()
    assert not http_client.is_closed
    await http_client.aclose()


@pytest.mark.asyncio
async def test_repository_metadata_follows_github_rename_redirect() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/old/repo":
            return httpx.Response(
                301,
                headers={"Location": "https://api.github.com/repos/new/repo"},
                json={"message": "Moved Permanently"},
            )
        payload = repository_payload()
        payload["full_name"] = "new/repo"
        payload["html_url"] = "https://github.com/new/repo"
        return httpx.Response(200, json=payload)

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(), client=http_client)
        metadata = await client.get_repository_metadata(RepositoryRef(owner="old", name="repo"))

    assert metadata.full_name == "new/repo"


@pytest.mark.asyncio
async def test_temporary_response_is_retried_then_succeeds() -> None:
    calls = 0
    delays: list[float] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json=repository_payload())

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(), client=http_client, sleep=fake_sleep)
        metadata = await client.get_repository_metadata(RepositoryRef(owner="openai", name="repo"))

    assert metadata.primary_language == "Python"
    assert calls == 2
    assert delays == [0.01]


@pytest.mark.asyncio
async def test_temporary_response_stops_after_configured_retries() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, json={"message": "bad gateway"})

    async def no_wait(_: float) -> None:
        return None

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(request_max_retries=1), client=http_client, sleep=no_wait)
        with pytest.raises(UpstreamTemporaryError) as exc_info:
            await client.get_repository_metadata(RepositoryRef(owner="openai", name="repo"))

    assert calls == 2
    assert exc_info.value.details["status_code"] == 502


@pytest.mark.asyncio
async def test_timeout_is_retried_and_mapped() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    async def no_wait(_: float) -> None:
        return None

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(request_max_retries=1), client=http_client, sleep=no_wait)
        with pytest.raises(UpstreamTimeoutError):
            await client.get_repository_metadata(RepositoryRef(owner="openai", name="repo"))

    assert calls == 2


@pytest.mark.asyncio
async def test_rate_limit_uses_headers_and_is_retried() -> None:
    reset_timestamp = 1_800_000_000
    calls = 0
    delays: list[float] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            403,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_timestamp),
                "Retry-After": "30",
            },
            json={"message": "API rate limit exceeded"},
        )

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(request_max_retries=1), client=http_client, sleep=fake_sleep)
        with pytest.raises(GitHubRateLimitError) as exc_info:
            await client.get_repository_metadata(RepositoryRef(owner="openai", name="repo"))

    assert calls == 2
    assert delays == [0.1]
    assert exc_info.value.details["reset_at"] == "2027-01-15T08:00:00+00:00"


@pytest.mark.asyncio
async def test_http_429_is_retried_then_succeeds() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "wait"})
        return httpx.Response(200, json=repository_payload())

    async def no_wait(_: float) -> None:
        return None

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(), client=http_client, sleep=no_wait)
        metadata = await client.get_repository_metadata(RepositoryRef(owner="openai", name="repo"))

    assert calls == 2
    assert metadata.full_name == "openai/openai-agents-python"


@pytest.mark.asyncio
async def test_not_found_is_not_retried() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"message": "Not Found"})

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(), client=http_client)
        with pytest.raises(RepositoryUnavailableError) as exc_info:
            await client.get_repository_metadata(RepositoryRef(owner="openai", name="missing"))

    assert calls == 1
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_invalid_json_is_mapped() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(), client=http_client)
        with pytest.raises(UpstreamResponseError):
            await client.get_repository_metadata(RepositoryRef(owner="openai", name="repo"))


@pytest.mark.asyncio
async def test_incomplete_metadata_is_mapped() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"full_name": "openai/repo"})

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(), client=http_client)
        with pytest.raises(UpstreamResponseError, match="缺少必要字段"):
            await client.get_repository_metadata(RepositoryRef(owner="openai", name="repo"))


@pytest.mark.asyncio
async def test_concurrency_never_exceeds_configured_limit() -> None:
    active = 0
    maximum_active = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=repository_payload())

    async with make_http_client(handler) as http_client:
        client = GitHubClient(make_settings(max_concurrency=2), client=http_client)
        await asyncio.gather(
            *(
                client.get_repository_metadata(RepositoryRef(owner="openai", name=f"repo-{i}"))
                for i in range(6)
            )
        )

    assert maximum_active == 2


@pytest.mark.asyncio
async def test_logs_are_structured_and_do_not_contain_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=repository_payload())

    settings = make_settings(github_token="top-secret-token")
    async with make_http_client(handler) as http_client:
        client = GitHubClient(settings, client=http_client)
        with caplog.at_level(logging.INFO, logger="repolens.github_client"):
            await client.get_repository_metadata(RepositoryRef(owner="openai", name="repo"))

    assert '"event":"github_tool_call"' in caplog.text
    assert '"status":"success"' in caplog.text
    assert "top-secret-token" not in caplog.text
