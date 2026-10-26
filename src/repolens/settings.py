from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RepoLensSettings(BaseSettings):
    github_token: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    agent_review_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    database_url: str = "sqlite:///./data/repolens.db"
    request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    request_max_retries: int = Field(default=3, ge=0, le=8)
    retry_base_delay_seconds: float = Field(default=0.25, ge=0, le=30)
    retry_max_delay_seconds: float = Field(default=5.0, ge=0, le=60)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    github_max_pages: int = Field(default=5, ge=1, le=20)
    activity_window_days: int = Field(default=90, ge=7, le=3650)
    issue_stale_days: int = Field(default=180, ge=1, le=3650)
    issue_response_sample_size: int = Field(default=10, ge=0, le=50)
    cache_data_version: str = Field(default="1", min_length=1, max_length=32)
    cache_metadata_ttl_seconds: int = Field(default=900, ge=0, le=604800)
    cache_tree_ttl_seconds: int = Field(default=3600, ge=0, le=604800)
    cache_content_ttl_seconds: int = Field(default=3600, ge=0, le=604800)
    cache_activity_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    cache_issue_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
