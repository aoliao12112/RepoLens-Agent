from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from uuid import UUID

from .models import (
    ClaimType,
    DueDiligenceReport,
    Evidence,
    LicenseStatus,
    Recommendation,
    ReportStatus,
    RepositorySnapshot,
    ReviewFinding,
    ReviewKind,
    ReviewSection,
    RiskSeverity,
    ToolStatus,
)

_Now = Callable[[], datetime]
_RISK_SEVERITIES = {RiskSeverity.medium, RiskSeverity.high, RiskSeverity.critical}


class RuleBasedAnalyzer:
    def __init__(self, *, now: _Now | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def analyze(
        self,
        snapshot: RepositorySnapshot,
        target: str,
        task_id: UUID,
    ) -> DueDiligenceReport:
        sections = [
            self.review_maintenance(snapshot),
            self.review_engineering(snapshot),
            self.review_license(snapshot),
            self.review_target_fit(snapshot, target),
        ]
        score_by_kind = {section.kind: section.score for section in sections}
        overall_score = round(
            score_by_kind[ReviewKind.maintenance] * 0.25
            + score_by_kind[ReviewKind.engineering] * 0.30
            + score_by_kind[ReviewKind.license] * 0.20
            + score_by_kind[ReviewKind.target_fit] * 0.25
        )
        recommendation = self._recommendation(snapshot, overall_score)
        risks = [
            finding
            for section in sections
            for finding in section.findings
            if finding.severity in _RISK_SEVERITIES
        ]
        needs_human_review = [
            finding.description
            for section in sections
            for finding in section.findings
            if finding.claim_type == ClaimType.needs_confirmation
        ]
        return DueDiligenceReport(
            task_id=task_id,
            status=ReportStatus.awaiting_confirmation,
            repository=snapshot.repository,
            target=target,
            overall_score=overall_score,
            recommendation=recommendation,
            executive_summary=self._executive_summary(snapshot, recommendation, overall_score),
            sections=sections,
            risks=risks,
            evidence=self._all_evidence(snapshot),
            needs_human_review=self._deduplicate(needs_human_review),
            development_plan=self._development_plan(snapshot),
            generated_at=self._now(),
        )

    def review_maintenance(self, snapshot: RepositorySnapshot) -> ReviewSection:
        metadata = snapshot.repository
        activity = snapshot.recent_activity
        findings: list[ReviewFinding] = []
        score = 100
        status_evidence = self._evidence(snapshot, "repository-status")
        timestamp_evidence = self._evidence(snapshot, "repository-timestamps")
        commit_evidence = self._evidence(snapshot, "recent-commits")
        activity_failed = (
            snapshot.collection_status.get("get_recent_activity") == ToolStatus.failed
        )
        issues_failed = snapshot.collection_status.get("get_issue_signals") == ToolStatus.failed

        if metadata.archived:
            score -= 60
            findings.append(
                self._finding(
                    "maintenance.archived",
                    "仓库已归档",
                    "GitHub 将该仓库标记为已归档，通常意味着项目已停止主动维护。",
                    RiskSeverity.critical,
                    ClaimType.fact,
                    status_evidence,
                    -60,
                )
            )
        else:
            findings.append(
                self._finding(
                    "maintenance.not_archived",
                    "仓库未归档",
                    "GitHub 当前未将该仓库标记为归档。",
                    RiskSeverity.info,
                    ClaimType.fact,
                    status_evidence,
                )
            )

        if metadata.pushed_at is None:
            score -= 25
            findings.append(
                self._finding(
                    "maintenance.no_push_time",
                    "缺少最近推送时间",
                    "仓库元数据未提供最近推送时间，维护状态需要人工确认。",
                    RiskSeverity.high,
                    ClaimType.needs_confirmation,
                    timestamp_evidence,
                    -25,
                )
            )
        else:
            inactive_days = max(0, (self._now() - metadata.pushed_at).days)
            if inactive_days > 365:
                score -= 35
                severity = RiskSeverity.high
                score_delta = -35
            elif inactive_days > 180:
                score -= 20
                severity = RiskSeverity.medium
                score_delta = -20
            elif inactive_days > 90:
                score -= 10
                severity = RiskSeverity.low
                score_delta = -10
            else:
                severity = RiskSeverity.info
                score_delta = 0
            findings.append(
                self._finding(
                    "maintenance.last_push",
                    "最近推送时间",
                    f"距最近一次代码推送约 {inactive_days} 天。",
                    severity,
                    ClaimType.fact,
                    timestamp_evidence,
                    score_delta,
                )
            )

        if activity_failed:
            findings.append(
                self._finding(
                    "maintenance.activity_unavailable",
                    "近期活动数据不可用",
                    "近期提交、贡献者和 Release 采集失败，相关维护判断需要重新采集或人工确认。",
                    RiskSeverity.medium,
                    ClaimType.needs_confirmation,
                    [],
                )
            )
        elif activity.commit_count == 0:
            score -= 20
            findings.append(
                self._finding(
                    "maintenance.no_recent_commits",
                    "观察窗口内没有提交",
                    f"最近 {activity.observation_days} 天未采集到代码提交。",
                    RiskSeverity.medium,
                    ClaimType.fact,
                    commit_evidence,
                    -20,
                )
            )
        else:
            findings.append(
                self._finding(
                    "maintenance.recent_commits",
                    "观察窗口内存在提交",
                    f"最近 {activity.observation_days} 天采集到 {activity.commit_count} 次提交。",
                    RiskSeverity.info,
                    ClaimType.fact,
                    commit_evidence,
                )
            )

        if (
            not activity_failed
            and activity.top_contributor_share is not None
            and activity.top_contributor_share >= 0.8
        ):
            score -= 10
            findings.append(
                self._finding(
                    "maintenance.contributor_concentration",
                    "贡献者集中度较高",
                    f"最大贡献者占比约 {activity.top_contributor_share:.0%}，存在关键人员风险。",
                    RiskSeverity.medium,
                    ClaimType.inference,
                    self._evidence(snapshot, "contributors"),
                    -10,
                )
            )

        if not activity_failed and activity.release_count == 0:
            score -= 5
            findings.append(
                self._finding(
                    "maintenance.no_recent_release",
                    "观察窗口内没有 Release",
                    f"最近 {activity.observation_days} 天未采集到正式 Release。",
                    RiskSeverity.low,
                    ClaimType.fact,
                    self._evidence(snapshot, "releases"),
                    -5,
                )
            )

        issues = snapshot.issue_signals
        if issues_failed:
            findings.append(
                self._finding(
                    "maintenance.issues_unavailable",
                    "Issue 数据不可用",
                    "Issue 与 Pull Request 信号采集失败，积压和响应速度需要重新采集或人工确认。",
                    RiskSeverity.medium,
                    ClaimType.needs_confirmation,
                    [],
                )
            )
        if not issues_failed and issues.stale_issue_count >= 5:
            score -= 10
            findings.append(
                self._finding(
                    "maintenance.stale_issues",
                    "长期未处理 Issue 较多",
                    f"采集到 {issues.stale_issue_count} 个长期未更新的 Open Issue。",
                    RiskSeverity.medium,
                    ClaimType.fact,
                    self._evidence(snapshot, "open-issues"),
                    -10,
                )
            )
        if (
            not issues_failed
            and issues.median_first_response_hours is not None
            and issues.median_first_response_hours > 168
        ):
            score -= 10
            findings.append(
                self._finding(
                    "maintenance.slow_issue_response",
                    "Issue 首次响应较慢",
                    f"抽样 Issue 的首次响应时间中位数约为 {issues.median_first_response_hours:.1f} 小时。",
                    RiskSeverity.medium,
                    ClaimType.inference,
                    self._evidence(snapshot, "open-issues"),
                    -10,
                )
            )
        if not issues_failed and issues.truncated:
            findings.append(
                self._finding(
                    "maintenance.issues_truncated",
                    "Issue 数据被截断",
                    "Issue 数量超过采集页数上限，积压和主题统计需要人工复核。",
                    RiskSeverity.medium,
                    ClaimType.needs_confirmation,
                    self._evidence(snapshot, "open-issues"),
                )
            )

        if not activity_failed and activity.truncated:
            findings.append(
                self._finding(
                    "maintenance.activity_truncated",
                    "活动数据被截断",
                    "活动记录超过采集页数上限，当前统计是下限，需要人工复核。",
                    RiskSeverity.medium,
                    ClaimType.needs_confirmation,
                    commit_evidence,
                )
            )

        score = self._clamp(score)
        return ReviewSection(
            kind=ReviewKind.maintenance,
            score=score,
            summary=f"维护度规则评分为 {score}/100。",
            findings=findings,
        )

    def review_engineering(self, snapshot: RepositorySnapshot) -> ReviewSection:
        tree = snapshot.languages_and_tree.tree
        evidence = self._evidence(snapshot, "repository-tree")
        if snapshot.collection_status.get("get_languages_and_tree") == ToolStatus.failed:
            return ReviewSection(
                kind=ReviewKind.engineering,
                score=50,
                summary="目录树采集失败，工程质量暂无法可靠评分。",
                findings=[
                    self._finding(
                        "engineering.tree_unavailable",
                        "目录树数据不可用",
                        "无法确认依赖、测试、CI、文档和入口文件，请重新采集或人工检查。",
                        RiskSeverity.high,
                        ClaimType.needs_confirmation,
                        [],
                    )
                ],
                status=ToolStatus.failed,
            )
        findings: list[ReviewFinding] = []
        score = 100
        checks = (
            ("dependencies", "依赖声明", tree.dependency_files, 15, RiskSeverity.high),
            ("tests", "测试目录或测试文件", tree.test_paths, 25, RiskSeverity.medium),
            ("ci", "持续集成配置", tree.ci_files, 20, RiskSeverity.medium),
            ("docker", "容器配置", tree.docker_files, 10, RiskSeverity.low),
            ("docs", "项目文档", tree.documentation_paths, 10, RiskSeverity.low),
            ("examples", "示例代码", tree.example_paths, 5, RiskSeverity.low),
            ("entrypoint", "常见入口文件", tree.entrypoint_files, 5, RiskSeverity.low),
        )
        for rule_name, label, paths, points, missing_severity in checks:
            if paths:
                findings.append(
                    self._finding(
                        f"engineering.{rule_name}.present",
                        f"检测到{label}",
                        f"检测到 {len(paths)} 个相关路径，例如 {paths[0]}。",
                        RiskSeverity.info,
                        ClaimType.fact,
                        evidence,
                    )
                )
            else:
                score -= points
                findings.append(
                    self._finding(
                        f"engineering.{rule_name}.missing",
                        f"未检测到{label}",
                        f"当前目录树中未检测到{label}，可能提高运行或维护成本。",
                        missing_severity,
                        ClaimType.fact,
                        evidence,
                        -points,
                    )
                )
        if tree.truncated:
            score = min(score, 70)
            findings.append(
                self._finding(
                    "engineering.tree_truncated",
                    "目录树不完整",
                    "GitHub 返回的递归目录树已截断，缺失信号不能视为确定不存在。",
                    RiskSeverity.medium,
                    ClaimType.needs_confirmation,
                    evidence,
                )
            )
        score = self._clamp(score)
        return ReviewSection(
            kind=ReviewKind.engineering,
            score=score,
            summary=f"工程基础规则评分为 {score}/100。",
            findings=findings,
        )

    def review_license(self, snapshot: RepositorySnapshot) -> ReviewSection:
        license_data = snapshot.readme_and_license
        if license_data.license_status == LicenseStatus.detected:
            score = 90
            finding = self._finding(
                "license.detected",
                "检测到明确许可证",
                f"GitHub 将许可证识别为 {license_data.license_spdx or '已知类型'}；具体义务仍需阅读原文。",
                RiskSeverity.info,
                ClaimType.fact,
                self._evidence(snapshot, "license"),
                -10,
            )
        elif license_data.license_status == LicenseStatus.missing:
            score = 10
            finding = self._finding(
                "license.missing",
                "未检测到许可证",
                "GitHub License API 未返回许可证文件，二次开发和分发存在显著合规风险。",
                RiskSeverity.critical,
                ClaimType.fact,
                self._evidence(snapshot, "license-missing"),
                -90,
            )
        elif license_data.license_status == LicenseStatus.file_unrecognized:
            score = 35
            finding = self._finding(
                "license.unrecognized",
                "许可证文件无法自动识别",
                "仓库存在许可证文件，但 SPDX 类型不明确，采用前必须人工阅读条款。",
                RiskSeverity.high,
                ClaimType.needs_confirmation,
                self._evidence(snapshot, "license"),
                -65,
            )
        else:
            score = 45
            finding = self._finding(
                "license.unknown",
                "许可证状态未知",
                "当前证据不足以确认许可证状态，采用前必须人工检查。",
                RiskSeverity.high,
                ClaimType.needs_confirmation,
                self._evidence(snapshot, "license", "license-missing"),
                -55,
            )
        return ReviewSection(
            kind=ReviewKind.license,
            score=score,
            summary=f"许可证规则评分为 {score}/100；本结果不是法律意见。",
            findings=[finding],
            status=(
                ToolStatus.failed
                if snapshot.collection_status.get("get_readme_and_license") == ToolStatus.failed
                else ToolStatus.success
            ),
        )

    def review_target_fit(self, snapshot: RepositorySnapshot, target: str) -> ReviewSection:
        target_text = target.lower()
        repository_text = " ".join(
            [
                snapshot.repository.primary_language or "",
                snapshot.repository.description or "",
                snapshot.readme_and_license.readme_text or "",
                *snapshot.languages_and_tree.tree.paths,
            ]
        ).lower()
        criteria = (
            ("python", {"python"}, {"python"}, "Python 技术栈"),
            ("agent", {"agent", "智能体"}, {"agent", "multi-agent", "智能体"}, "Agent 方向"),
            ("api", {"api", "接口", "后端"}, {"api", "fastapi", "server"}, "API/后端能力"),
            ("web", {"web", "网站", "前端"}, {"html", "javascript", "typescript", "web"}, "Web 能力"),
            ("docker", {"docker", "容器"}, {"dockerfile", "docker-compose"}, "容器化"),
            ("tests", {"test", "测试"}, {"tests/", "test_"}, "自动化测试"),
            ("ci", {"ci", "持续集成"}, {".github/workflows"}, "持续集成"),
        )
        requested = [item for item in criteria if any(word in target_text for word in item[1])]
        findings: list[ReviewFinding] = []
        if not requested:
            findings.append(
                self._finding(
                    "target.no_explicit_criteria",
                    "目标缺少可识别的技术条件",
                    "规则模式无法从目标中提取明确技术条件，需要人工确认关注重点。",
                    RiskSeverity.medium,
                    ClaimType.needs_confirmation,
                    [],
                )
            )
            score = 60
        else:
            matched = 0
            match_points = round(60 / len(requested))
            general_evidence = self._evidence(
                snapshot, "languages", "repository-tree", "readme", "readme-missing"
            )
            for rule_name, _, repository_keywords, label in requested:
                is_match = any(keyword in repository_text for keyword in repository_keywords)
                if is_match:
                    matched += 1
                    findings.append(
                        self._finding(
                            f"target.{rule_name}.match",
                            f"匹配{label}",
                            f"仓库公开信息中检测到与“{label}”相关的信号。",
                            RiskSeverity.info,
                            ClaimType.inference,
                            general_evidence,
                            match_points,
                        )
                    )
                else:
                    findings.append(
                        self._finding(
                            f"target.{rule_name}.gap",
                            f"缺少{label}信号",
                            f"仓库公开信息中未检测到足够的“{label}”信号。",
                            RiskSeverity.medium,
                            ClaimType.inference,
                            general_evidence,
                        )
                    )
            score = round(40 + 60 * matched / len(requested))

        if snapshot.repository.archived:
            score = min(score, 40)
        if snapshot.readme_and_license.license_status == LicenseStatus.missing:
            score = min(score, 50)
        score = self._clamp(score)
        return ReviewSection(
            kind=ReviewKind.target_fit,
            score=score,
            summary=f"目标适配规则评分为 {score}/100。",
            findings=findings,
        )

    @staticmethod
    def _recommendation(snapshot: RepositorySnapshot, overall_score: int) -> Recommendation:
        if snapshot.repository.archived:
            return Recommendation.not_recommended
        if overall_score < 50:
            return Recommendation.not_recommended
        if snapshot.readme_and_license.license_status in {
            LicenseStatus.missing,
            LicenseStatus.file_unrecognized,
            LicenseStatus.unknown,
        }:
            return Recommendation.cautious
        if overall_score >= 75:
            return Recommendation.recommended
        return Recommendation.cautious

    @staticmethod
    def _executive_summary(
        snapshot: RepositorySnapshot,
        recommendation: Recommendation,
        score: int,
    ) -> str:
        labels = {
            Recommendation.recommended: "推荐采用",
            Recommendation.cautious: "建议谨慎采用",
            Recommendation.not_recommended: "不推荐采用",
        }
        return (
            f"{snapshot.repository.full_name} 的规则综合评分为 {score}/100，"
            f"当前结论为“{labels[recommendation]}”。结论来自公开仓库事实与确定性规则，"
            "尚未执行仓库代码，也不构成法律意见。"
        )

    @staticmethod
    def _development_plan(snapshot: RepositorySnapshot) -> list[str]:
        plan = [
            "第 1 天：核对许可证原文，完成本地安装并运行最小示例。",
            "第 2 天：阅读入口、依赖和核心模块，绘制调用链。",
            "第 3 天：补充或运行测试，确认主要扩展点的基线行为。",
            "第 4 至 5 天：实现一个与目标直接相关的最小功能闭环。",
            "第 6 天：补充错误处理、日志和自动化评测。",
            "第 7 天：整理结果、风险和继续采用条件。",
        ]
        if snapshot.repository.archived:
            plan[0] = "第 1 天：优先寻找活跃替代项目；只有确认必须使用时才验证本仓库。"
        return plan

    @staticmethod
    def _finding(
        rule_id: str,
        title: str,
        description: str,
        severity: RiskSeverity,
        claim_type: ClaimType,
        evidence: list[Evidence],
        score_delta: int = 0,
    ) -> ReviewFinding:
        return ReviewFinding(
            finding_id=rule_id,
            rule_id=rule_id,
            title=title,
            description=description,
            severity=severity,
            claim_type=claim_type,
            evidence=evidence,
            score_delta=score_delta,
        )

    @staticmethod
    def _all_evidence(snapshot: RepositorySnapshot) -> list[Evidence]:
        evidence = [
            *snapshot.repository.evidence,
            *snapshot.languages_and_tree.evidence,
            *snapshot.readme_and_license.evidence,
            *snapshot.recent_activity.evidence,
            *snapshot.issue_signals.evidence,
        ]
        return list({item.evidence_id: item for item in evidence}.values())

    @classmethod
    def _evidence(cls, snapshot: RepositorySnapshot, *evidence_ids: str) -> list[Evidence]:
        selected = set(evidence_ids)
        return [item for item in cls._all_evidence(snapshot) if item.evidence_id in selected]

    @staticmethod
    def _deduplicate(values: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @staticmethod
    def _clamp(score: int) -> int:
        return max(0, min(100, score))
