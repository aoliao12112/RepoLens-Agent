import base64
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from repolens.github_client import GitHubClient
from repolens.models import LicenseStatus, RepositoryRef, ToolStatus
from repolens.settings import RepoLensSettings

REPO = RepositoryRef(owner="owner", name="repo")
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def settings(**overrides: Any) -> RepoLensSettings:
    values = {
        "request_max_retries": 0,
        "github_max_pages": 2,
        "activity_window_days": 90,
        "issue_stale_days": 180,
        "issue_response_sample_size": 10,
        "_env_file": None,
    }
    values.update(overrides)
    return RepoLensSettings(**values)


def http_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )


def metadata_payload() -> dict[str, Any]:
    return {
        "full_name": "owner/repo",
        "html_url": "https://github.com/owner/repo",
        "description": "Example",
        "default_branch": "main",
        "language": "Python",
        "stargazers_count": 20,
        "forks_count": 3,
        "open_issues_count": 4,
        "archived": False,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2026-10-25T00:00:00Z",
        "pushed_at": "2026-10-24T00:00:00Z",
        "license": {"spdx_id": "MIT"},
    }


@pytest.mark.asyncio
async def test_get_languages_and_tree_calculates_percentages_and_signals() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/languages"):
            return httpx.Response(200, json={"Python": 750, "HTML": 250})
        assert request.url.path.endswith("/git/trees/main")
        assert request.url.params["recursive"] == "1"
        return httpx.Response(
            200,
            json={
                "truncated": True,
                "tree": [
                    {"path": "pyproject.toml", "type": "blob"},
                    {"path": "tests/test_api.py", "type": "blob"},
                    {"path": ".github/workflows/ci.yml", "type": "blob"},
                ],
            },
        )

    async with http_client(handler) as transport_client:
        client = GitHubClient(settings(), client=transport_client, now=lambda: NOW)
        result = await client.get_languages_and_tree(REPO, "main")

    assert [(item.language, item.percentage) for item in result.languages] == [
        ("Python", 75.0),
        ("HTML", 25.0),
    ]
    assert result.tree.dependency_files == ["pyproject.toml"]
    assert result.tree.test_paths == ["tests/test_api.py"]
    assert result.tree.truncated is True
    assert {item.evidence_id for item in result.evidence} == {"languages", "repository-tree"}


@pytest.mark.asyncio
async def test_get_readme_and_detected_license_decodes_content() -> None:
    readme = base64.b64encode(b"# Example\n").decode()
    license_text = "MIT License\nCopyright 2026 Example"
    license_content = base64.b64encode(license_text.encode()).decode()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/readme"):
            return httpx.Response(
                200,
                json={
                    "path": "README.md",
                    "html_url": "https://github.com/owner/repo/blob/main/README.md",
                    "content": readme,
                },
            )
        return httpx.Response(
            200,
            json={
                "path": "LICENSE",
                "html_url": "https://github.com/owner/repo/blob/main/LICENSE",
                "content": license_content,
                "license": {"spdx_id": "MIT", "name": "MIT License"},
            },
        )

    async with http_client(handler) as transport_client:
        client = GitHubClient(settings(), client=transport_client)
        result = await client.get_readme_and_license(REPO)

    assert result.readme_text == "# Example\n"
    assert result.license_status == LicenseStatus.detected
    assert result.license_spdx == "MIT"
    assert result.copyright_notice == "Copyright 2026 Example"
    assert len(result.evidence) == 2


@pytest.mark.asyncio
async def test_missing_readme_and_license_are_valid_empty_results() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with http_client(handler) as transport_client:
        client = GitHubClient(settings(), client=transport_client)
        result = await client.get_readme_and_license(REPO)

    assert result.readme_text is None
    assert result.license_status == LicenseStatus.missing
    assert {item.evidence_id for item in result.evidence} == {
        "readme-missing",
        "license-missing",
    }


@pytest.mark.asyncio
async def test_unrecognized_license_file_is_distinguished_from_missing() -> None:
    content = base64.b64encode(b"Custom terms").decode()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/readme"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            200,
            json={
                "path": "LICENSE.txt",
                "html_url": "https://github.com/owner/repo/blob/main/LICENSE.txt",
                "content": content,
                "license": {"spdx_id": "NOASSERTION", "name": "Other"},
            },
        )

    async with http_client(handler) as transport_client:
        client = GitHubClient(settings(), client=transport_client)
        result = await client.get_readme_and_license(REPO)

    assert result.license_status == LicenseStatus.file_unrecognized


@pytest.mark.asyncio
async def test_get_recent_activity_calculates_frequency_and_concentration() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits"):
            assert "since" in request.url.params
            return httpx.Response(
                200,
                json=[
                    {"commit": {"author": {"date": "2026-10-25T00:00:00Z"}}},
                    {"commit": {"author": {"date": "2026-10-20T00:00:00Z"}}},
                    {"commit": {"author": {"date": "2026-10-01T00:00:00Z"}}},
                ],
            )
        if request.url.path.endswith("/contributors"):
            return httpx.Response(
                200,
                json=[{"login": "a", "contributions": 75}, {"login": "b", "contributions": 25}],
            )
        return httpx.Response(
            200,
            json=[
                {"published_at": "2026-10-10T00:00:00Z", "created_at": None, "draft": False},
                {"published_at": "2025-01-01T00:00:00Z", "created_at": None, "draft": False},
                {"published_at": "2026-10-20T00:00:00Z", "created_at": None, "draft": True},
            ],
        )

    async with http_client(handler) as transport_client:
        client = GitHubClient(settings(), client=transport_client, now=lambda: NOW)
        result = await client.get_recent_activity(REPO)

    assert result.commit_count == 3
    assert result.commits_per_month == 1.0
    assert result.contributor_count == 2
    assert result.top_contributor_share == 0.75
    assert result.release_count == 1
    assert result.latest_release_at == datetime(2026, 10, 10, tzinfo=UTC)
    assert result.truncated is False


@pytest.mark.asyncio
async def test_get_issue_signals_separates_prs_and_measures_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/1/comments"):
            return httpx.Response(200, json=[{"created_at": "2026-10-02T00:00:00Z"}])
        return httpx.Response(
            200,
            json=[
                {
                    "number": 1,
                    "title": "Install error",
                    "body": "Setup fails on Windows",
                    "html_url": "https://github.com/owner/repo/issues/1",
                    "created_at": "2026-10-01T00:00:00Z",
                    "updated_at": "2026-10-02T00:00:00Z",
                    "comments": 1,
                },
                {
                    "number": 2,
                    "title": "Documentation example",
                    "body": "Please add docs",
                    "html_url": "https://github.com/owner/repo/issues/2",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-02-01T00:00:00Z",
                    "comments": 0,
                },
                {
                    "number": 3,
                    "title": "Update code",
                    "body": "",
                    "html_url": "https://github.com/owner/repo/pull/3",
                    "created_at": "2026-10-01T00:00:00Z",
                    "updated_at": "2026-10-20T00:00:00Z",
                    "comments": 0,
                    "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/3"},
                },
            ],
        )

    async with http_client(handler) as transport_client:
        client = GitHubClient(settings(), client=transport_client, now=lambda: NOW)
        result = await client.get_issue_signals(REPO)

    assert result.open_issue_count == 2
    assert result.open_pull_request_count == 1
    assert result.stale_issue_count == 1
    assert result.median_first_response_hours == 24
    assert {theme.name for theme in result.common_themes} >= {
        "installation",
        "runtime_error",
        "documentation",
    }


@pytest.mark.asyncio
async def test_page_limit_marks_result_as_truncated() -> None:
    issues = [
        {
            "number": number,
            "title": "Question",
            "body": "",
            "html_url": f"https://github.com/owner/repo/issues/{number}",
            "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-20T00:00:00Z",
            "comments": 0,
        }
        for number in range(100)
    ]

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=issues)

    async with http_client(handler) as transport_client:
        client = GitHubClient(
            settings(github_max_pages=1, issue_response_sample_size=0),
            client=transport_client,
            now=lambda: NOW,
        )
        result = await client.get_issue_signals(REPO)

    assert result.open_issue_count == 100
    assert result.truncated is True


@pytest.mark.asyncio
async def test_snapshot_degrades_when_one_collector_fails() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/owner/repo":
            return httpx.Response(200, json=metadata_payload())
        if path.endswith("/languages"):
            return httpx.Response(500, json={"message": "failure"})
        if "/git/trees/" in path:
            return httpx.Response(200, json={"tree": [], "truncated": False})
        if path.endswith(("/readme", "/license")):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=[])

    async with http_client(handler) as transport_client:
        client = GitHubClient(settings(), client=transport_client, now=lambda: NOW)
        snapshot = await client.collect_repository_snapshot(REPO)

    assert snapshot.repository.full_name == "owner/repo"
    assert snapshot.collection_status["get_languages_and_tree"] == ToolStatus.failed
    assert snapshot.collection_status["get_readme_and_license"] == ToolStatus.success
    assert snapshot.languages_and_tree.languages == []
    assert snapshot.readme_and_license.license_status == LicenseStatus.missing
