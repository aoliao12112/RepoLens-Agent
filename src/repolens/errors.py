from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    invalid_input = "invalid_input"
    repository_unavailable = "repository_unavailable"
    github_rate_limited = "github_rate_limited"
    upstream_timeout = "upstream_timeout"
    upstream_temporary_failure = "upstream_temporary_failure"
    upstream_invalid_response = "upstream_invalid_response"
    analysis_failure = "analysis_failure"


class RepoLensError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class InputValidationError(RepoLensError, ValueError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=ErrorCode.invalid_input, retryable=False, details=details)


class RepositoryUnavailableError(RepoLensError):
    def __init__(self, repository: str, *, status_code: int | None = None) -> None:
        details: dict[str, Any] = {"repository": repository}
        if status_code is not None:
            details["status_code"] = status_code
        super().__init__(
            f"仓库不存在、不可访问或无权读取：{repository}",
            code=ErrorCode.repository_unavailable,
            retryable=False,
            details=details,
        )


class GitHubRateLimitError(RepoLensError):
    def __init__(self, *, reset_at: datetime | None = None) -> None:
        details = {"reset_at": reset_at.isoformat()} if reset_at else {}
        super().__init__(
            "GitHub API 请求额度已耗尽，请在限流解除后重试",
            code=ErrorCode.github_rate_limited,
            retryable=True,
            details=details,
        )


class UpstreamTimeoutError(RepoLensError):
    def __init__(self, service: str = "GitHub API") -> None:
        super().__init__(
            f"{service} 请求超时，请稍后重试",
            code=ErrorCode.upstream_timeout,
            retryable=True,
            details={"service": service},
        )


class UpstreamTemporaryError(RepoLensError):
    def __init__(self, service: str = "GitHub API", *, status_code: int | None = None) -> None:
        details: dict[str, Any] = {"service": service}
        if status_code is not None:
            details["status_code"] = status_code
        super().__init__(
            f"{service} 暂时不可用，请稍后重试",
            code=ErrorCode.upstream_temporary_failure,
            retryable=True,
            details=details,
        )


class UpstreamResponseError(RepoLensError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        details = {"status_code": status_code} if status_code is not None else {}
        super().__init__(
            message,
            code=ErrorCode.upstream_invalid_response,
            retryable=False,
            details=details,
        )


class AnalysisError(RepoLensError):
    def __init__(self, message: str = "分析过程失败", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=ErrorCode.analysis_failure,
            retryable=False,
            details=details,
        )
