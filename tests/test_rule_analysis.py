from datetime import UTC, datetime
from uuid import UUID

from repolens.models import (
    ClaimType,
    Evidence,
    EvidenceKind,
    IssueSignals,
    LanguageBreakdown,
    LanguagesAndTree,
    LicenseStatus,
    ReadmeAndLicense,
    RecentActivity,
    Recommendation,
    RepositoryMetadata,
    RepositorySnapshot,
    RepositoryTreeSignals,
    ReviewKind,
    ToolStatus,
)
from repolens.rule_analysis import RuleBasedAnalyzer

NOW = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
TASK_ID = UUID("11111111-1111-4111-8111-111111111111")


def evidence(evidence_id: str, field: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim=f"Evidence for {field}",
        kind=EvidenceKind.github_api,
        source_url="https://api.github.com/repos/owner/repo",
        field_or_path=field,
    )


def active_snapshot() -> RepositorySnapshot:
    return RepositorySnapshot(
        repository=RepositoryMetadata(
            full_name="owner/repo",
            html_url="https://github.com/owner/repo",
            description="Python framework for building reliable Agent API applications",
            default_branch="main",
            primary_language="Python",
            stars=100,
            forks=10,
            open_issues=5,
            archived=False,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 10, 25, tzinfo=UTC),
            pushed_at=datetime(2026, 10, 24, tzinfo=UTC),
            license_spdx="MIT",
            evidence=[
                evidence("repository-status", "archived,default_branch"),
                evidence("repository-timestamps", "created_at,updated_at,pushed_at"),
            ],
        ),
        languages_and_tree=LanguagesAndTree(
            languages=[LanguageBreakdown(language="Python", bytes=1000, percentage=100)],
            tree=RepositoryTreeSignals(
                paths=[
                    "pyproject.toml",
                    "src/app.py",
                    "tests/test_app.py",
                    ".github/workflows/ci.yml",
                    "Dockerfile",
                    "docs/guide.md",
                    "examples/basic.py",
                ],
                dependency_files=["pyproject.toml"],
                test_paths=["tests/test_app.py"],
                ci_files=[".github/workflows/ci.yml"],
                docker_files=["Dockerfile"],
                documentation_paths=["docs/guide.md"],
                example_paths=["examples/basic.py"],
                entrypoint_files=["src/app.py"],
            ),
            evidence=[
                evidence("languages", "language byte counts"),
                evidence("repository-tree", "tree"),
            ],
        ),
        readme_and_license=ReadmeAndLicense(
            readme_text="Build Python multi-agent APIs with tests and Docker.",
            readme_path="README.md",
            readme_url="https://github.com/owner/repo/blob/main/README.md",
            license_spdx="MIT",
            license_name="MIT License",
            license_path="LICENSE",
            license_url="https://github.com/owner/repo/blob/main/LICENSE",
            license_status=LicenseStatus.detected,
            evidence=[
                evidence("readme", "README.md"),
                evidence("license", "license.spdx_id"),
            ],
        ),
        recent_activity=RecentActivity(
            observation_days=90,
            commit_count=12,
            commits_per_month=4,
            contributor_count=4,
            top_contributor_share=0.5,
            release_count=2,
            latest_commit_at=datetime(2026, 10, 24, tzinfo=UTC),
            latest_release_at=datetime(2026, 10, 1, tzinfo=UTC),
            evidence=[
                evidence("recent-commits", "commit.author.date"),
                evidence("contributors", "contributions"),
                evidence("releases", "published_at"),
            ],
        ),
        issue_signals=IssueSignals(
            open_issue_count=4,
            open_pull_request_count=1,
            stale_issue_count=0,
            median_first_response_hours=8,
            evidence=[evidence("open-issues", "issues")],
        ),
        collected_at=NOW,
    )


def analyzer() -> RuleBasedAnalyzer:
    return RuleBasedAnalyzer(now=lambda: NOW)


def test_complete_active_repository_is_recommended() -> None:
    report = analyzer().analyze(
        active_snapshot(),
        "寻找适合 Python Agent API 开发并带有测试和 Docker 的项目",
        TASK_ID,
    )

    assert report.recommendation == Recommendation.recommended
    assert report.overall_score >= 90
    assert {section.kind for section in report.sections} == set(ReviewKind)
    assert len(report.development_plan) == 6


def test_archived_repository_is_never_recommended() -> None:
    snapshot = active_snapshot()
    snapshot.repository.archived = True

    report = analyzer().analyze(snapshot, "Python Agent 项目", TASK_ID)

    assert report.recommendation == Recommendation.not_recommended
    archived = next(finding for finding in report.risks if finding.rule_id == "maintenance.archived")
    assert archived.claim_type == ClaimType.fact
    assert archived.evidence


def test_missing_license_caps_recommendation_at_cautious() -> None:
    snapshot = active_snapshot()
    snapshot.readme_and_license = ReadmeAndLicense(
        license_status=LicenseStatus.missing,
        evidence=[evidence("license-missing", "HTTP 404")],
    )

    report = analyzer().analyze(snapshot, "Python Agent API 项目", TASK_ID)

    assert report.recommendation == Recommendation.cautious
    assert any(finding.rule_id == "license.missing" for finding in report.risks)


def test_missing_tests_are_explained_by_rule_and_evidence() -> None:
    snapshot = active_snapshot()
    snapshot.languages_and_tree.tree.test_paths = []

    section = analyzer().review_engineering(snapshot)
    finding = next(item for item in section.findings if item.rule_id == "engineering.tests.missing")

    assert section.score == 75
    assert finding.evidence[0].evidence_id == "repository-tree"


def test_unrecognized_target_requires_human_confirmation() -> None:
    report = analyzer().analyze(active_snapshot(), "看看这个项目怎么样", TASK_ID)

    assert report.needs_human_review
    target_section = next(section for section in report.sections if section.kind == ReviewKind.target_fit)
    assert target_section.score == 60


def test_failed_collectors_do_not_turn_fallback_zeroes_into_facts() -> None:
    snapshot = active_snapshot()
    snapshot.recent_activity = RecentActivity(
        observation_days=90,
        commit_count=0,
        commits_per_month=0,
        contributor_count=0,
        release_count=0,
    )
    snapshot.languages_and_tree = LanguagesAndTree(tree=RepositoryTreeSignals())
    snapshot.collection_status = {
        "get_recent_activity": ToolStatus.failed,
        "get_languages_and_tree": ToolStatus.failed,
        "get_issue_signals": ToolStatus.failed,
    }

    report = analyzer().analyze(snapshot, "Python Agent API 项目", TASK_ID)

    finding_ids = {finding.finding_id for section in report.sections for finding in section.findings}
    assert "maintenance.activity_unavailable" in finding_ids
    assert "maintenance.issues_unavailable" in finding_ids
    assert "engineering.tree_unavailable" in finding_ids
    assert "maintenance.no_recent_commits" not in finding_ids
    assert "engineering.tests.missing" not in finding_ids
    assert all(
        finding.claim_type == ClaimType.needs_confirmation
        for section in report.sections
        for finding in section.findings
        if finding.finding_id.endswith("unavailable")
    )


def test_rule_scores_are_deterministic_for_same_input() -> None:
    first = analyzer().analyze(active_snapshot(), "Python Agent API 项目", TASK_ID)
    second = analyzer().analyze(active_snapshot(), "Python Agent API 项目", TASK_ID)

    assert first.overall_score == second.overall_score
    assert first.recommendation == second.recommendation
    assert first.sections == second.sections


def test_every_fact_finding_has_evidence() -> None:
    report = analyzer().analyze(active_snapshot(), "Python Agent API 项目", TASK_ID)
    facts = [
        finding
        for section in report.sections
        for finding in section.findings
        if finding.claim_type == ClaimType.fact
    ]

    assert facts
    assert all(finding.evidence for finding in facts)
