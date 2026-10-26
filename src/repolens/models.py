from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

Score = Annotated[int, Field(ge=0, le=100)]


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_timezone(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return value


class EvidenceKind(str, Enum):
    github_api = "github_api"
    repository_file = "repository_file"
    web_page = "web_page"


class RiskSeverity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ClaimType(str, Enum):
    fact = "fact"
    inference = "inference"
    needs_confirmation = "needs_confirmation"


class ReviewKind(str, Enum):
    maintenance = "maintenance"
    engineering = "engineering"
    license = "license"
    target_fit = "target_fit"


class ToolStatus(str, Enum):
    success = "success"
    partial = "partial"
    failed = "failed"
    skipped = "skipped"


class TaskStatus(str, Enum):
    queued = "queued"
    collecting = "collecting"
    reviewing = "reviewing"
    verifying = "verifying"
    awaiting_confirmation = "awaiting_confirmation"
    completed = "completed"
    failed = "failed"


class ReportStatus(str, Enum):
    draft = "draft"
    awaiting_confirmation = "awaiting_confirmation"
    confirmed = "confirmed"


class Recommendation(str, Enum):
    recommended = "recommended"
    cautious = "cautious"
    not_recommended = "not_recommended"


class LicenseStatus(str, Enum):
    detected = "detected"
    file_unrecognized = "file_unrecognized"
    missing = "missing"
    unknown = "unknown"


class Evidence(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=120)
    claim: str = Field(min_length=1, max_length=500)
    kind: EvidenceKind
    source_url: HttpUrl
    field_or_path: str = Field(min_length=1, max_length=500)
    excerpt: str | None = Field(default=None, max_length=1_000)


class RepositoryRef(BaseModel):
    owner: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class RepositoryMetadata(BaseModel):
    full_name: str = Field(min_length=3)
    html_url: HttpUrl
    description: str | None = None
    default_branch: str = Field(min_length=1)
    primary_language: str | None = None
    stars: int = Field(ge=0)
    forks: int = Field(ge=0)
    open_issues: int = Field(ge=0)
    archived: bool
    created_at: datetime
    updated_at: datetime
    pushed_at: datetime | None = None
    license_spdx: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    _created_at_has_timezone = field_validator("created_at")(require_timezone)
    _updated_at_has_timezone = field_validator("updated_at")(require_timezone)
    _pushed_at_has_timezone = field_validator("pushed_at")(require_timezone)


class LanguageBreakdown(BaseModel):
    language: str = Field(min_length=1)
    bytes: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)


class RepositoryTreeSignals(BaseModel):
    paths: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    test_paths: list[str] = Field(default_factory=list)
    ci_files: list[str] = Field(default_factory=list)
    docker_files: list[str] = Field(default_factory=list)
    documentation_paths: list[str] = Field(default_factory=list)
    example_paths: list[str] = Field(default_factory=list)
    entrypoint_files: list[str] = Field(default_factory=list)
    truncated: bool = False


class LanguagesAndTree(BaseModel):
    languages: list[LanguageBreakdown] = Field(default_factory=list)
    tree: RepositoryTreeSignals
    evidence: list[Evidence] = Field(default_factory=list)


class ReadmeAndLicense(BaseModel):
    readme_text: str | None = None
    readme_path: str | None = None
    readme_url: HttpUrl | None = None
    license_spdx: str | None = None
    license_name: str | None = None
    license_path: str | None = None
    license_url: HttpUrl | None = None
    copyright_notice: str | None = None
    license_status: LicenseStatus = LicenseStatus.unknown
    evidence: list[Evidence] = Field(default_factory=list)


class RecentActivity(BaseModel):
    observation_days: int = Field(gt=0, le=3650)
    commit_count: int = Field(ge=0)
    commits_per_month: float = Field(ge=0)
    contributor_count: int = Field(ge=0)
    top_contributor_share: float | None = Field(default=None, ge=0, le=1)
    release_count: int = Field(ge=0)
    latest_commit_at: datetime | None = None
    latest_release_at: datetime | None = None
    truncated: bool = False
    evidence: list[Evidence] = Field(default_factory=list)

    _latest_commit_has_timezone = field_validator("latest_commit_at")(require_timezone)
    _latest_release_has_timezone = field_validator("latest_release_at")(require_timezone)


class IssueTheme(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    count: int = Field(ge=1)
    sample_urls: list[HttpUrl] = Field(default_factory=list, max_length=5)


class IssueSignals(BaseModel):
    open_issue_count: int = Field(ge=0)
    open_pull_request_count: int = Field(ge=0)
    stale_issue_count: int = Field(ge=0)
    median_first_response_hours: float | None = Field(default=None, ge=0)
    common_themes: list[IssueTheme] = Field(default_factory=list)
    truncated: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


class RepositorySnapshot(BaseModel):
    repository: RepositoryMetadata
    languages_and_tree: LanguagesAndTree
    readme_and_license: ReadmeAndLicense
    recent_activity: RecentActivity
    issue_signals: IssueSignals
    collection_status: dict[str, ToolStatus] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=utc_now)

    _collected_at_has_timezone = field_validator("collected_at")(require_timezone)


class AnalysisRequest(BaseModel):
    repository_url: HttpUrl
    target: str = Field(min_length=3, max_length=2_000)
    enable_model: bool = True


class ReviewFinding(BaseModel):
    finding_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    severity: RiskSeverity
    claim_type: ClaimType
    evidence: list[Evidence] = Field(default_factory=list)
    rule_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_evidence_for_verified_claims(self) -> ReviewFinding:
        verified_high_risk = self.severity in {RiskSeverity.high, RiskSeverity.critical}
        if self.claim_type == ClaimType.fact and not self.evidence:
            raise ValueError("事实结论必须至少包含一条证据")
        if verified_high_risk and self.claim_type != ClaimType.needs_confirmation and not self.evidence:
            raise ValueError("已验证的高风险结论必须至少包含一条证据")
        return self


class ReviewSection(BaseModel):
    kind: ReviewKind
    score: Score
    summary: str = Field(min_length=1, max_length=2_000)
    findings: list[ReviewFinding] = Field(default_factory=list)
    status: ToolStatus = ToolStatus.success


class TaskError(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=1_000)
    retryable: bool = False


class AnalysisTask(BaseModel):
    task_id: UUID = Field(default_factory=uuid4)
    request: AnalysisRequest
    status: TaskStatus = TaskStatus.queued
    progress: Score = 0
    current_stage: str | None = Field(default=None, max_length=200)
    error: TaskError | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _created_at_has_timezone = field_validator("created_at")(require_timezone)
    _updated_at_has_timezone = field_validator("updated_at")(require_timezone)

    @model_validator(mode="after")
    def validate_error_state(self) -> AnalysisTask:
        if self.status == TaskStatus.failed and self.error is None:
            raise ValueError("失败任务必须包含错误摘要")
        if self.status != TaskStatus.failed and self.error is not None:
            raise ValueError("只有失败任务可以包含错误摘要")
        return self


class DueDiligenceReport(BaseModel):
    report_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    status: ReportStatus = ReportStatus.draft
    repository: RepositoryMetadata
    target: str = Field(min_length=3, max_length=2_000)
    overall_score: Score
    recommendation: Recommendation
    executive_summary: str = Field(min_length=1, max_length=4_000)
    sections: list[ReviewSection] = Field(min_length=4)
    risks: list[ReviewFinding] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    needs_human_review: list[str] = Field(default_factory=list)
    development_plan: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
    confirmed_at: datetime | None = None

    _generated_at_has_timezone = field_validator("generated_at")(require_timezone)
    _confirmed_at_has_timezone = field_validator("confirmed_at")(require_timezone)

    @model_validator(mode="after")
    def validate_confirmation_and_sections(self) -> DueDiligenceReport:
        section_kinds = [section.kind for section in self.sections]
        if len(section_kinds) != len(set(section_kinds)):
            raise ValueError("报告中不能包含重复审查章节")
        if set(section_kinds) != set(ReviewKind):
            raise ValueError("报告必须包含维护、工程、许可证和目标适配四个章节")
        if self.status == ReportStatus.confirmed and self.confirmed_at is None:
            raise ValueError("已确认报告必须记录确认时间")
        if self.status != ReportStatus.confirmed and self.confirmed_at is not None:
            raise ValueError("未确认报告不能记录确认时间")
        return self

