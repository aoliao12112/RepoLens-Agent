from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RepoLensSettings(BaseSettings):
    github_token: SecretStr | None = None
    request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    request_max_retries: int = Field(default=3, ge=0, le=8)
    retry_base_delay_seconds: float = Field(default=0.25, ge=0, le=30)
    retry_max_delay_seconds: float = Field(default=5.0, ge=0, le=60)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    github_max_pages: int = Field(default=5, ge=1, le=20)
    activity_window_days: int = Field(default=90, ge=7, le=3650)
    issue_stale_days: int = Field(default=180, ge=1, le=3650)
    issue_response_sample_size: int = Field(default=10, ge=0, le=50)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
