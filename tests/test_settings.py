import pytest
from pydantic import ValidationError

from repolens.settings import RepoLensSettings


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("REQUEST_MAX_RETRIES", "2")
    monkeypatch.setenv("MAX_CONCURRENCY", "7")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")

    settings = RepoLensSettings(_env_file=None)

    assert settings.request_timeout_seconds == 12.5
    assert settings.request_max_retries == 2
    assert settings.max_concurrency == 7
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "secret-token"
    assert "secret-token" not in repr(settings)


def test_settings_reject_invalid_concurrency() -> None:
    with pytest.raises(ValidationError):
        RepoLensSettings(max_concurrency=0, _env_file=None)
