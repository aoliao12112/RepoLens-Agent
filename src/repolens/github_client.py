from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from statistics import median
from time import perf_counter
from typing import Any, Self
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from .errors import (
    GitHubRateLimitError,
    RepoLensError,
    RepositoryUnavailableError,
    UpstreamResponseError,
    UpstreamTemporaryError,
    UpstreamTimeoutError,
)
from .models import (
    Evidence,
    EvidenceKind,
    IssueSignals,
    LanguageBreakdown,
    LanguagesAndTree,
    LicenseStatus,
    ReadmeAndLicense,
    RecentActivity,
    RepositoryMetadata,
    RepositoryRef,
    RepositorySnapshot,
    RepositoryTreeSignals,
    ToolStatus,
)
from .repository_signals import classify_tree_paths, summarize_issue_themes
from .settings import RepoLensSettings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_INACCESSIBLE_STATUS_CODES = {401, 403, 404}
_Sleep = Callable[[float], Awaitable[None]]
_Now = Callable[[], datetime]


class GitHubClient:
    def __init__(
        self,
        settings: RepoLensSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: _Sleep = asyncio.sleep,
        now: _Now | None = None,
    ) -> None:
        self._settings = settings or RepoLensSettings()
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "RepoLens-Agent",
        }
        if self._settings.github_token is not None:
            token = self._settings.github_token.get_secret_value()
            self._headers["Authorization"] = f"Bearer {token}"
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url="https://api.github.com")
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrency)
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_repository_metadata(self, repo: RepositoryRef) -> RepositoryMetadata:
        data = await self._request_json(
            "GET",
            f"/repos/{repo.full_name}",
            tool="get_repository_metadata",
            repository=repo.full_name,
        )
        if not isinstance(data, Mapping):
            raise UpstreamResponseError("GitHub API 返回的仓库元数据格式不正确")
        try:
            license_data = data.get("license") or {}
            license_spdx = (
                license_data.get("spdx_id") if isinstance(license_data, Mapping) else None
            )
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
                created_at=datetime.fromisoformat(data["created_at"]),
                updated_at=datetime.fromisoformat(data["updated_at"]),
                pushed_at=(
                    datetime.fromisoformat(data["pushed_at"])
                    if data.get("pushed_at")
                    else None
                ),
                license_spdx=license_spdx,
                evidence=self._metadata_evidence(repo, data),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise UpstreamResponseError("GitHub API 返回的仓库元数据缺少必要字段") from exc

    async def get_languages_and_tree(
        self,
        repo: RepositoryRef,
        default_branch: str,
    ) -> LanguagesAndTree:
        languages_payload, tree_payload = await asyncio.gather(
            self._request_json(
                "GET",
                f"/repos/{repo.full_name}/languages",
                tool="get_languages_and_tree.languages",
                repository=repo.full_name,
            ),
            self._request_json(
                "GET",
                f"/repos/{repo.full_name}/git/trees/{quote(default_branch, safe='')}",
                tool="get_languages_and_tree.tree",
                repository=repo.full_name,
                params={"recursive": 1},
            ),
        )
        if not isinstance(languages_payload, Mapping) or not isinstance(tree_payload, Mapping):
            raise UpstreamResponseError("GitHub API 返回的语言或目录树格式不正确")

        try:
            language_bytes = {
                str(language): int(byte_count)
                for language, byte_count in languages_payload.items()
                if int(byte_count) >= 0
            }
            total_bytes = sum(language_bytes.values())
            languages = [
                LanguageBreakdown(
                    language=language,
                    bytes=byte_count,
                    percentage=(byte_count / total_bytes * 100 if total_bytes else 0),
                )
                for language, byte_count in sorted(
                    language_bytes.items(), key=lambda item: (-item[1], item[0].lower())
                )
            ]
            raw_tree = tree_payload.get("tree") or []
            if not isinstance(raw_tree, list):
                raise TypeError("tree must be a list")
            paths = [
                item["path"]
                for item in raw_tree
                if isinstance(item, Mapping) and isinstance(item.get("path"), str)
            ]
            truncated = bool(tree_payload.get("truncated", False))
        except (TypeError, ValueError, ValidationError) as exc:
            raise UpstreamResponseError("GitHub API 返回的语言或目录树字段不合法") from exc

        return LanguagesAndTree(
            languages=languages,
            tree=classify_tree_paths(paths, truncated=truncated),
            evidence=[
                self._api_evidence(
                    repo,
                    "languages",
                    "languages",
                    "仓库语言占比来自 GitHub Languages API。",
                    "language byte counts",
                ),
                self._api_evidence(
                    repo,
                    f"git/trees/{quote(default_branch, safe='')}",
                    "repository-tree",
                    "工程目录信号来自默认分支的递归目录树。",
                    "tree,truncated",
                ),
            ],
        )

    async def get_readme_and_license(self, repo: RepositoryRef) -> ReadmeAndLicense:
        readme_payload, license_payload = await asyncio.gather(
            self._request_json(
                "GET",
                f"/repos/{repo.full_name}/readme",
                tool="get_readme_and_license.readme",
                repository=repo.full_name,
                allow_not_found=True,
            ),
            self._request_json(
                "GET",
                f"/repos/{repo.full_name}/license",
                tool="get_readme_and_license.license",
                repository=repo.full_name,
                allow_not_found=True,
            ),
        )
        if readme_payload is not None and not isinstance(readme_payload, Mapping):
            raise UpstreamResponseError("GitHub API 返回的 README 格式不正确")
        if license_payload is not None and not isinstance(license_payload, Mapping):
            raise UpstreamResponseError("GitHub API 返回的许可证格式不正确")

        readme_text = self._decode_content(readme_payload)
        license_text = self._decode_content(license_payload)
        license_info = license_payload.get("license") if license_payload else None
        if not isinstance(license_info, Mapping):
            license_info = {}
        license_spdx = license_info.get("spdx_id")
        license_name = license_info.get("name")
        if license_payload is None:
            license_status = LicenseStatus.missing
        elif license_spdx and license_spdx not in {"NOASSERTION", "OTHER"}:
            license_status = LicenseStatus.detected
        elif license_payload.get("path") or license_text:
            license_status = LicenseStatus.file_unrecognized
        else:
            license_status = LicenseStatus.unknown

        evidence: list[Evidence] = []
        if readme_payload is not None:
            evidence.append(
                self._api_evidence(
                    repo,
                    "readme",
                    "readme",
                    "README 内容和路径来自 GitHub README API。",
                    "path,content,html_url",
                )
            )
        if license_payload is not None:
            evidence.append(
                self._api_evidence(
                    repo,
                    "license",
                    "license",
                    "许可证识别结果和文件路径来自 GitHub License API。",
                    "license.spdx_id,path,content",
                )
            )

        return ReadmeAndLicense(
            readme_text=readme_text,
            readme_path=self._optional_string(readme_payload, "path"),
            readme_url=self._optional_string(readme_payload, "html_url"),
            license_spdx=str(license_spdx) if license_spdx else None,
            license_name=str(license_name) if license_name else None,
            license_path=self._optional_string(license_payload, "path"),
            license_url=self._optional_string(license_payload, "html_url"),
            copyright_notice=self._find_copyright_notice(license_text),
            license_status=license_status,
            evidence=evidence,
        )

    async def get_recent_activity(
        self,
        repo: RepositoryRef,
        *,
        observation_days: int | None = None,
    ) -> RecentActivity:
        days = observation_days or self._settings.activity_window_days
        since = self._now() - timedelta(days=days)
        commits_result, contributors_result, releases_result = await asyncio.gather(
            self._paginate_json(
                f"/repos/{repo.full_name}/commits",
                tool="get_recent_activity.commits",
                repository=repo.full_name,
                params={"since": since.isoformat()},
            ),
            self._paginate_json(
                f"/repos/{repo.full_name}/contributors",
                tool="get_recent_activity.contributors",
                repository=repo.full_name,
                params={"anon": "true"},
            ),
            self._paginate_json(
                f"/repos/{repo.full_name}/releases",
                tool="get_recent_activity.releases",
                repository=repo.full_name,
            ),
        )
        commits, commits_truncated = commits_result
        contributors, contributors_truncated = contributors_result
        releases, releases_truncated = releases_result

        try:
            commit_dates = [
                self._parse_datetime(item["commit"]["author"]["date"])
                for item in commits
                if isinstance(item.get("commit"), Mapping)
                and isinstance(item["commit"].get("author"), Mapping)
                and item["commit"]["author"].get("date")
            ]
            contributions = [
                int(item["contributions"])
                for item in contributors
                if isinstance(item.get("contributions"), int)
                and int(item["contributions"]) >= 0
            ]
            release_dates = [
                self._parse_datetime(item["published_at"] or item["created_at"])
                for item in releases
                if (item.get("published_at") or item.get("created_at"))
                and not item.get("draft", False)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamResponseError("GitHub API 返回的活动记录字段不合法") from exc

        recent_release_dates = [date for date in release_dates if date >= since]
        contribution_total = sum(contributions)
        top_contributor_share = (
            max(contributions) / contribution_total if contribution_total else None
        )
        return RecentActivity(
            observation_days=days,
            commit_count=len(commits),
            commits_per_month=len(commits) / days * 30,
            contributor_count=len(contributors),
            top_contributor_share=top_contributor_share,
            release_count=len(recent_release_dates),
            latest_commit_at=max(commit_dates, default=None),
            latest_release_at=max(release_dates, default=None),
            truncated=commits_truncated or contributors_truncated or releases_truncated,
            evidence=[
                self._api_evidence(
                    repo,
                    "commits",
                    "recent-commits",
                    f"最近 {days} 天的提交记录来自 GitHub Commits API。",
                    "commit.author.date",
                ),
                self._api_evidence(
                    repo,
                    "contributors",
                    "contributors",
                    "贡献者数量和贡献集中度来自 GitHub Contributors API。",
                    "login,contributions",
                ),
                self._api_evidence(
                    repo,
                    "releases",
                    "releases",
                    "发布数量和最近发布时间来自 GitHub Releases API。",
                    "published_at,created_at,draft",
                ),
            ],
        )

    async def get_issue_signals(self, repo: RepositoryRef) -> IssueSignals:
        issues, truncated = await self._paginate_json(
            f"/repos/{repo.full_name}/issues",
            tool="get_issue_signals.issues",
            repository=repo.full_name,
            params={"state": "open", "sort": "updated", "direction": "desc"},
        )
        pull_requests = [item for item in issues if "pull_request" in item]
        plain_issues = [item for item in issues if "pull_request" not in item]
        stale_before = self._now() - timedelta(days=self._settings.issue_stale_days)
        try:
            stale_count = sum(
                1
                for item in plain_issues
                if item.get("updated_at")
                and self._parse_datetime(item["updated_at"]) < stale_before
            )
        except (TypeError, ValueError) as exc:
            raise UpstreamResponseError("GitHub API 返回的 Issue 时间字段不合法") from exc

        response_candidates = [
            item
            for item in plain_issues
            if item.get("comments", 0) > 0 and isinstance(item.get("number"), int)
        ][: self._settings.issue_response_sample_size]
        response_hours = await asyncio.gather(
            *(self._first_response_hours(repo, item) for item in response_candidates)
        )
        measured_hours = [hours for hours in response_hours if hours is not None]
        return IssueSignals(
            open_issue_count=len(plain_issues),
            open_pull_request_count=len(pull_requests),
            stale_issue_count=stale_count,
            median_first_response_hours=(median(measured_hours) if measured_hours else None),
            common_themes=summarize_issue_themes(plain_issues),
            truncated=truncated,
            evidence=[
                self._api_evidence(
                    repo,
                    "issues",
                    "open-issues",
                    "Open Issue、Pull Request、长期未处理问题和主题来自 GitHub Issues API。",
                    "pull_request,updated_at,title,body,comments",
                )
            ],
        )

    async def collect_repository_snapshot(self, repo: RepositoryRef) -> RepositorySnapshot:
        metadata = await self.get_repository_metadata(repo)
        results = await asyncio.gather(
            self.get_languages_and_tree(repo, metadata.default_branch),
            self.get_readme_and_license(repo),
            self.get_recent_activity(repo),
            self.get_issue_signals(repo),
            return_exceptions=True,
        )
        fallback_values = (
            LanguagesAndTree(tree=RepositoryTreeSignals()),
            ReadmeAndLicense(),
            RecentActivity(
                observation_days=self._settings.activity_window_days,
                commit_count=0,
                commits_per_month=0,
                contributor_count=0,
                release_count=0,
            ),
            IssueSignals(open_issue_count=0, open_pull_request_count=0, stale_issue_count=0),
        )
        names = (
            "get_languages_and_tree",
            "get_readme_and_license",
            "get_recent_activity",
            "get_issue_signals",
        )
        values: list[Any] = []
        statuses: dict[str, ToolStatus] = {"get_repository_metadata": ToolStatus.success}
        for name, result, fallback in zip(names, results, fallback_values, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    json.dumps(
                        {
                            "event": "github_collection_failure",
                            "tool": name,
                            "error_type": type(result).__name__,
                        },
                        separators=(",", ":"),
                    )
                )
                values.append(fallback)
                statuses[name] = ToolStatus.failed
            else:
                values.append(result)
                is_partial = bool(
                    getattr(result, "truncated", False)
                    or getattr(getattr(result, "tree", None), "truncated", False)
                )
                statuses[name] = ToolStatus.partial if is_partial else ToolStatus.success

        languages_and_tree, readme_and_license, recent_activity, issue_signals = values
        return RepositorySnapshot(
            repository=metadata,
            languages_and_tree=languages_and_tree,
            readme_and_license=readme_and_license,
            recent_activity=recent_activity,
            issue_signals=issue_signals,
            collection_status=statuses,
            collected_at=self._now(),
        )

    async def _paginate_json(
        self,
        path: str,
        *,
        tool: str,
        repository: str,
        params: Mapping[str, str | int] | None = None,
    ) -> tuple[list[Mapping[str, Any]], bool]:
        items: list[Mapping[str, Any]] = []
        page_size = 100
        for page in range(1, self._settings.github_max_pages + 1):
            page_params = dict(params or {})
            page_params.update({"per_page": page_size, "page": page})
            payload = await self._request_json(
                "GET",
                path,
                tool=tool,
                repository=repository,
                params=page_params,
            )
            if not isinstance(payload, list) or not all(
                isinstance(item, Mapping) for item in payload
            ):
                raise UpstreamResponseError("GitHub API 返回的分页数据格式不正确")
            items.extend(payload)
            if len(payload) < page_size:
                return items, False
        return items, True

    async def _first_response_hours(
        self,
        repo: RepositoryRef,
        issue: Mapping[str, Any],
    ) -> float | None:
        try:
            payload = await self._request_json(
                "GET",
                f"/repos/{repo.full_name}/issues/{issue['number']}/comments",
                tool="get_issue_signals.first_response",
                repository=repo.full_name,
                params={"per_page": 1, "page": 1},
            )
            if not isinstance(payload, list) or not payload:
                return None
            first_comment = payload[0]
            if not isinstance(first_comment, Mapping) or not first_comment.get("created_at"):
                return None
            created_at = self._parse_datetime(issue["created_at"])
            responded_at = self._parse_datetime(first_comment["created_at"])
            return max(0.0, (responded_at - created_at).total_seconds() / 3600)
        except (KeyError, TypeError, ValueError, RepoLensError):
            return None

    @staticmethod
    def _decode_content(payload: Mapping[str, Any] | None) -> str | None:
        if payload is None or not isinstance(payload.get("content"), str):
            return None
        try:
            decoded = base64.b64decode(payload["content"], validate=False)
            return decoded.decode("utf-8", errors="replace")
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_string(payload: Mapping[str, Any] | None, field: str) -> str | None:
        if payload is None:
            return None
        value = payload.get(field)
        return value if isinstance(value, str) else None

    @staticmethod
    def _find_copyright_notice(content: str | None) -> str | None:
        if not content:
            return None
        lines = [line.strip() for line in content.splitlines() if "copyright" in line.lower()]
        return "\n".join(lines[:5]) or None

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("GitHub 时间字段缺少时区")
        return parsed

    @staticmethod
    def _api_evidence(
        repo: RepositoryRef,
        endpoint: str,
        evidence_id: str,
        claim: str,
        field_or_path: str,
    ) -> Evidence:
        return Evidence(
            evidence_id=evidence_id,
            claim=claim,
            kind=EvidenceKind.github_api,
            source_url=f"https://api.github.com/repos/{repo.full_name}/{endpoint}",
            field_or_path=field_or_path,
        )

    @classmethod
    def _metadata_evidence(
        cls,
        repo: RepositoryRef,
        data: Mapping[str, Any],
    ) -> list[Evidence]:
        fields = {
            "repository-popularity": ("Star 和 Fork 数来自仓库元数据。", "stargazers_count,forks_count"),
            "repository-status": ("默认分支和归档状态来自仓库元数据。", "default_branch,archived"),
            "repository-timestamps": (
                "创建、更新和推送时间来自仓库元数据。",
                "created_at,updated_at,pushed_at",
            ),
            "repository-license": ("许可证标识来自仓库元数据。", "license.spdx_id"),
        }
        return [
            cls._api_evidence(repo, "", evidence_id, claim, field_or_path)
            for evidence_id, (claim, field_or_path) in fields.items()
            if data
        ]

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        tool: str,
        repository: str,
        params: Mapping[str, str | int] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        max_attempts = self._settings.request_max_retries + 1
        for attempt in range(max_attempts):
            started_at = perf_counter()
            try:
                async with self._semaphore:
                    response = await self._client.request(
                        method,
                        path,
                        params=params,
                        headers=self._headers,
                        timeout=self._settings.request_timeout_seconds,
                    )
            except httpx.TimeoutException as exc:
                self._log_attempt(tool, attempt, started_at, "error", "timeout")
                if attempt + 1 < max_attempts:
                    await self._sleep(self._retry_delay(attempt))
                    continue
                raise UpstreamTimeoutError() from exc
            except httpx.TransportError as exc:
                self._log_attempt(tool, attempt, started_at, "error", "transport_error")
                if attempt + 1 < max_attempts:
                    await self._sleep(self._retry_delay(attempt))
                    continue
                raise UpstreamTemporaryError() from exc

            if self._is_rate_limited(response):
                self._log_attempt(
                    tool,
                    attempt,
                    started_at,
                    "error",
                    "rate_limited",
                    response.status_code,
                )
                if attempt + 1 < max_attempts:
                    await self._sleep(self._retry_delay(attempt, response))
                    continue
                raise GitHubRateLimitError(reset_at=self._rate_limit_reset(response))

            if response.status_code in _RETRYABLE_STATUS_CODES:
                self._log_attempt(
                    tool,
                    attempt,
                    started_at,
                    "error",
                    "temporary_http_error",
                    response.status_code,
                )
                if attempt + 1 < max_attempts:
                    await self._sleep(self._retry_delay(attempt, response))
                    continue
                raise UpstreamTemporaryError(status_code=response.status_code)

            if response.status_code == 404 and allow_not_found:
                self._log_attempt(
                    tool,
                    attempt,
                    started_at,
                    "success",
                    "not_found",
                    response.status_code,
                )
                return None

            if response.status_code in _INACCESSIBLE_STATUS_CODES:
                self._log_attempt(
                    tool,
                    attempt,
                    started_at,
                    "error",
                    "repository_unavailable",
                    response.status_code,
                )
                raise RepositoryUnavailableError(repository, status_code=response.status_code)

            if response.is_error:
                self._log_attempt(
                    tool,
                    attempt,
                    started_at,
                    "error",
                    "unexpected_http_error",
                    response.status_code,
                )
                raise UpstreamResponseError(
                    "GitHub API 拒绝了请求",
                    status_code=response.status_code,
                )

            try:
                payload = response.json()
            except ValueError as exc:
                self._log_attempt(
                    tool,
                    attempt,
                    started_at,
                    "error",
                    "invalid_json",
                    response.status_code,
                )
                raise UpstreamResponseError(
                    "GitHub API 返回了无法解析的数据",
                    status_code=response.status_code,
                ) from exc

            self._log_attempt(tool, attempt, started_at, "success", None, response.status_code)
            return payload

        raise RuntimeError("GitHub 请求重试循环异常结束")

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        delay = self._settings.retry_base_delay_seconds * (2**attempt)
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(0.0, float(retry_after))
                except ValueError:
                    pass
            elif reset_at := self._rate_limit_reset(response):
                delay = max(0.0, (reset_at - datetime.now(UTC)).total_seconds())
        return min(delay, self._settings.retry_max_delay_seconds)

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code != 403:
            return False
        if response.headers.get("X-RateLimit-Remaining") == "0":
            return True
        return "rate limit" in response.text.lower()

    @staticmethod
    def _rate_limit_reset(response: httpx.Response) -> datetime | None:
        raw_value = response.headers.get("X-RateLimit-Reset")
        if not raw_value:
            return None
        try:
            return datetime.fromtimestamp(int(raw_value), tz=UTC)
        except (OverflowError, ValueError):
            return None

    @staticmethod
    def _log_attempt(
        tool: str,
        attempt: int,
        started_at: float,
        status: str,
        error_type: str | None,
        status_code: int | None = None,
    ) -> None:
        event = {
            "event": "github_tool_call",
            "tool": tool,
            "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            "status": status,
            "retry_count": attempt,
            "error_type": error_type,
            "status_code": status_code,
        }
        logger.info(json.dumps(event, ensure_ascii=False, separators=(",", ":")))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

