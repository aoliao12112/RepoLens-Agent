from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl


class EvidenceKind(str, Enum):
    github_api = "github_api"
    repository_file = "repository_file"
    web_page = "web_page"


class Evidence(BaseModel):
    claim: str
    kind: EvidenceKind
    source_url: HttpUrl
    field_or_path: str


class RepositoryRef(BaseModel):
    owner: str = Field(min_length=1)
    name: str = Field(min_length=1)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class RepositoryMetadata(BaseModel):
    full_name: str
    html_url: HttpUrl
    description: str | None = None
    default_branch: str
    primary_language: str | None = None
    stars: int = Field(ge=0)
    forks: int = Field(ge=0)
    open_issues: int = Field(ge=0)
    archived: bool
    created_at: datetime
    updated_at: datetime
    pushed_at: datetime | None = None
    license_spdx: str | None = None


class Recommendation(str, Enum):
    recommended = "recommended"
    cautious = "cautious"
    not_recommended = "not_recommended"


class DueDiligenceReport(BaseModel):
    repository: RepositoryMetadata
    target: str
    recommendation: Recommendation
    summary: str
    strengths: list[str]
    risks: list[str]
    evidence: list[Evidence]
    needs_human_review: list[str]

