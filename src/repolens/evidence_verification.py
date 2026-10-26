from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

from .models import (
    ClaimType,
    DueDiligenceReport,
    Evidence,
    EvidenceKind,
    LicenseStatus,
    Recommendation,
    ReportStatus,
    RepositorySnapshot,
    ReviewFinding,
    ReviewSection,
    RiskSeverity,
)

_SEVERITY_RANK = {
    RiskSeverity.info: 0,
    RiskSeverity.low: 1,
    RiskSeverity.medium: 2,
    RiskSeverity.high: 3,
    RiskSeverity.critical: 4,
}
_RISK_SEVERITIES = {RiskSeverity.medium, RiskSeverity.high, RiskSeverity.critical}
_DIRECT_EVIDENCE = {EvidenceKind.github_api, EvidenceKind.repository_file}


class EvidenceVerifier:
    def verify(
        self,
        report: DueDiligenceReport,
        snapshot: RepositorySnapshot,
    ) -> DueDiligenceReport:
        catalog = {item.evidence_id: item for item in self._snapshot_evidence(snapshot)}
        warnings: list[str] = []
        verified_sections = [
            self._verify_section(section, catalog, snapshot.repository.full_name, warnings)
            for section in report.sections
        ]
        pending = [
            finding.description
            for section in verified_sections
            for finding in section.findings
            if finding.claim_type == ClaimType.needs_confirmation
        ]
        risk_candidates = [
            finding
            for section in verified_sections
            for finding in section.findings
            if finding.severity in _RISK_SEVERITIES
            and finding.claim_type != ClaimType.needs_confirmation
        ]
        merged_risks = self._merge_risks(risk_candidates)
        recommendation = self._consistent_recommendation(report, snapshot)
        valid_report_evidence = [
            canonical
            for item in report.evidence
            if (canonical := catalog.get(item.evidence_id)) is not None
            and self._same_evidence(item, canonical)
            and self._valid_source(canonical, snapshot.repository.full_name)
        ]
        referenced_evidence = [
            evidence
            for section in verified_sections
            for finding in section.findings
            for evidence in finding.evidence
        ]
        return DueDiligenceReport.model_validate(
            {
                **report.model_dump(),
                "status": ReportStatus.awaiting_confirmation,
                "recommendation": recommendation,
                "sections": verified_sections,
                "risks": merged_risks,
                "evidence": self._deduplicate_evidence(
                    [*valid_report_evidence, *referenced_evidence]
                ),
                "needs_human_review": list(
                    dict.fromkeys([*report.needs_human_review, *pending])
                ),
                "confirmed_at": None,
                "evidence_verified": True,
                "verification_warnings": list(dict.fromkeys(warnings)),
            }
        )

    def _verify_section(
        self,
        section: ReviewSection,
        catalog: dict[str, Evidence],
        repository: str,
        warnings: list[str],
    ) -> ReviewSection:
        findings = [
            self._verify_finding(finding, catalog, repository, warnings)
            for finding in section.findings
        ]
        return section.model_copy(update={"findings": findings})

    def _verify_finding(
        self,
        finding: ReviewFinding,
        catalog: dict[str, Evidence],
        repository: str,
        warnings: list[str],
    ) -> ReviewFinding:
        valid: list[Evidence] = []
        for evidence in finding.evidence:
            canonical = catalog.get(evidence.evidence_id)
            if canonical is None:
                warnings.append(f"{finding.finding_id}: evidence_id 不存在")
                continue
            if not self._same_evidence(evidence, canonical):
                warnings.append(f"{finding.finding_id}: evidence 内容与采集快照不一致")
                continue
            if not self._valid_source(canonical, repository):
                warnings.append(f"{finding.finding_id}: evidence 来源不属于目标仓库")
                continue
            valid.append(canonical)

        claim_type = finding.claim_type
        if claim_type == ClaimType.fact and not valid:
            claim_type = ClaimType.needs_confirmation
            warnings.append(f"{finding.finding_id}: 无有效证据的事实已转为待确认")
        if finding.severity in {RiskSeverity.high, RiskSeverity.critical}:
            direct = [item for item in valid if item.kind in _DIRECT_EVIDENCE]
            if not direct:
                claim_type = ClaimType.inference if valid else ClaimType.needs_confirmation
                warnings.append(f"{finding.finding_id}: 高风险结论缺少直接证据")
        return ReviewFinding(
            **{
                **finding.model_dump(exclude={"evidence", "claim_type"}),
                "claim_type": claim_type,
                "evidence": self._deduplicate_evidence(valid),
            }
        )

    @staticmethod
    def _same_evidence(left: Evidence, right: Evidence) -> bool:
        return left.model_dump(mode="json") == right.model_dump(mode="json")

    @staticmethod
    def _valid_source(evidence: Evidence, repository: str) -> bool:
        parsed = urlparse(str(evidence.source_url))
        host = (parsed.hostname or "").lower()
        expected_path = f"/repos/{repository}".lower()
        repository_path = f"/{repository}".lower()
        if evidence.kind == EvidenceKind.github_api:
            return host == "api.github.com" and parsed.path.lower().startswith(expected_path)
        return host in {"github.com", "www.github.com"} and parsed.path.lower().startswith(
            repository_path
        )

    @classmethod
    def _merge_risks(cls, risks: Iterable[ReviewFinding]) -> list[ReviewFinding]:
        grouped: dict[str, ReviewFinding] = {}
        for risk in risks:
            evidence_key = ",".join(sorted(item.evidence_id for item in risk.evidence))
            semantic_title = re.sub(r"\s+", "", risk.title).lower()
            key = risk.rule_id or f"{semantic_title}:{evidence_key}"
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = risk
                continue
            severity = max(
                (existing.severity, risk.severity), key=lambda item: _SEVERITY_RANK[item]
            )
            evidence = cls._deduplicate_evidence([*existing.evidence, *risk.evidence])
            descriptions = list(dict.fromkeys([existing.description, risk.description]))
            grouped[key] = existing.model_copy(
                update={
                    "severity": severity,
                    "description": " ".join(descriptions),
                    "evidence": evidence,
                    "score_delta": min(existing.score_delta, risk.score_delta),
                }
            )
        return sorted(
            grouped.values(),
            key=lambda item: (-_SEVERITY_RANK[item.severity], item.finding_id),
        )

    @staticmethod
    def _consistent_recommendation(
        report: DueDiligenceReport,
        snapshot: RepositorySnapshot,
    ) -> Recommendation:
        if snapshot.repository.archived:
            return Recommendation.not_recommended
        if (
            snapshot.readme_and_license.license_status != LicenseStatus.detected
            and report.recommendation == Recommendation.recommended
        ):
            return Recommendation.cautious
        return report.recommendation

    @staticmethod
    def _snapshot_evidence(snapshot: RepositorySnapshot) -> list[Evidence]:
        return [
            *snapshot.repository.evidence,
            *snapshot.languages_and_tree.evidence,
            *snapshot.readme_and_license.evidence,
            *snapshot.recent_activity.evidence,
            *snapshot.issue_signals.evidence,
        ]

    @staticmethod
    def _deduplicate_evidence(evidence: Iterable[Evidence]) -> list[Evidence]:
        return list({item.evidence_id: item for item in evidence}.values())
