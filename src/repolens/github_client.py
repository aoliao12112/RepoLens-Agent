from __future__ import annotations

from datetime import datetime

import httpx

from .models import RepositoryMetadata, RepositoryRef


class GitHubClient:
    def __init__(self, token: str | None = None, timeout_seconds: float = 20.0) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "RepoLens-Agent",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=headers,
            timeout=timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_repository_metadata(self, repo: RepositoryRef) -> RepositoryMetadata:
        response = await self._client.get(f"/repos/{repo.full_name}")
        if response.status_code == 404:
            raise ValueError(f"仓库不存在或不可访问：{repo.full_name}")
        response.raise_for_status()
        data = response.json()
        license_data = data.get("license") or {}
        return RepositoryMetadata(
            full_name=data["full_name"],
            html_url=data["html_url"],
            description=data.get("description"),
            default_branch=data["default_branch"],
            primary_language=data.get("language"),
            stars=data["stargazers_count"],
            forks=data["forks_count"],
            open_issues=data["open_issues_count"],
            archived=data["archived"],
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00")),
            pushed_at=(
                datetime.fromisoformat(data["pushed_at"].replace("Z", "+00:00"))
                if data.get("pushed_at")
                else None
            ),
            license_spdx=license_data.get("spdx_id"),
        )

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

