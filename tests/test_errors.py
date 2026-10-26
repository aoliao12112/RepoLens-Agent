from datetime import UTC, datetime

from repolens.errors import (
    ErrorCode,
    GitHubRateLimitError,
    RepositoryUnavailableError,
    UpstreamTemporaryError,
)


def test_repository_unavailable_error_is_not_retryable() -> None:
    error = RepositoryUnavailableError("owner/repository")

    assert error.code == ErrorCode.repository_unavailable
    assert error.retryable is False
    assert error.to_dict()["details"] == {"repository": "owner/repository"}


def test_rate_limit_error_serializes_reset_time() -> None:
    reset_at = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
    error = GitHubRateLimitError(reset_at=reset_at)

    assert error.retryable is True
    assert error.to_dict()["details"]["reset_at"] == "2026-10-26T08:00:00+00:00"


def test_temporary_error_exposes_status_code() -> None:
    error = UpstreamTemporaryError(status_code=503)

    assert error.code == ErrorCode.upstream_temporary_failure
    assert error.to_dict()["details"]["status_code"] == 503
