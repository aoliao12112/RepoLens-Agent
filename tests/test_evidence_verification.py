from test_rule_analysis import NOW, TASK_ID, active_snapshot

from repolens.evidence_verification import EvidenceVerifier
from repolens.models import (
    ClaimType,
    DueDiligenceReport,
    Evidence,
    EvidenceKind,
    Recommendation,
    ReviewFinding,
    ReviewKind,
    RiskSeverity,
)
from repolens.rule_analysis import RuleBasedAnalyzer


def analyzer() -> RuleBasedAnalyzer:
    return RuleBasedAnalyzer(now=lambda: NOW)


def test_valid_rule_report_passes_evidence_verification() -> None:
    snapshot = active_snapshot()
    report = analyzer().analyze(snapshot, "Python Agent 二次开发", TASK_ID)

    verified = EvidenceVerifier().verify(report, snapshot)

    assert verified.evidence_verified is True
    assert verified.verification_warnings == []
    assert verified.evidence


def test_tampered_cross_repository_evidence_moves_high_risk_to_pending() -> None:
    snapshot = active_snapshot()
    snapshot.repository.archived = True
    report = analyzer().analyze(snapshot, "Python Agent 二次开发", TASK_ID)
    maintenance = next(
        section for section in report.sections if section.kind == ReviewKind.maintenance
    )
    archived = next(item for item in maintenance.findings if item.rule_id == "maintenance.archived")
    tampered_evidence = Evidence.model_validate(
        {
            **archived.evidence[0].model_dump(),
            "source_url": "https://api.github.com/repos/other/repository",
        }
    )
    tampered_finding = archived.model_copy(update={"evidence": [tampered_evidence]})
    changed_findings = [
        tampered_finding if item.finding_id == archived.finding_id else item
        for item in maintenance.findings
    ]
    changed_sections = [
        section.model_copy(update={"findings": changed_findings})
        if section.kind == ReviewKind.maintenance
        else section
        for section in report.sections
    ]
    tampered_report = DueDiligenceReport.model_validate(
        {**report.model_dump(), "sections": changed_sections}
    )

    verified = EvidenceVerifier().verify(tampered_report, snapshot)
    archived_after = next(
        item
        for section in verified.sections
        for item in section.findings
        if item.rule_id == "maintenance.archived"
    )

    assert archived_after.claim_type == ClaimType.needs_confirmation
    assert archived_after.evidence == []
    assert not any(item.rule_id == "maintenance.archived" for item in verified.risks)
    assert verified.verification_warnings


def test_indirect_web_evidence_cannot_support_high_risk_fact() -> None:
    snapshot = active_snapshot()
    web_evidence = Evidence.model_validate(
        {
            **snapshot.repository.evidence[0].model_dump(),
            "evidence_id": "web-only",
            "kind": EvidenceKind.web_page,
            "source_url": "https://github.com/owner/repo/issues/1",
        }
    )
    snapshot.repository.evidence.append(web_evidence)
    report = analyzer().analyze(snapshot, "Python Agent 二次开发", TASK_ID)
    maintenance = next(
        section for section in report.sections if section.kind == ReviewKind.maintenance
    )
    extra = ReviewFinding.model_validate(
        {
            **maintenance.findings[0].model_dump(),
            "finding_id": "web-high-risk",
            "rule_id": None,
            "severity": RiskSeverity.high,
            "claim_type": ClaimType.fact,
            "evidence": [web_evidence],
        }
    )
    sections = [
        section.model_copy(update={"findings": [*section.findings, extra]})
        if section.kind == ReviewKind.maintenance
        else section
        for section in report.sections
    ]
    report = DueDiligenceReport.model_validate(
        {**report.model_dump(), "sections": sections, "evidence": [*report.evidence, web_evidence]}
    )

    verified = EvidenceVerifier().verify(report, snapshot)
    finding = next(item for item in verified.risks if item.finding_id == "web-high-risk")

    assert finding.claim_type == ClaimType.inference


def test_duplicate_risks_are_merged() -> None:
    snapshot = active_snapshot()
    snapshot.repository.archived = True
    report = analyzer().analyze(snapshot, "Python Agent 二次开发", TASK_ID)
    archived = next(
        finding
        for section in report.sections
        for finding in section.findings
        if finding.rule_id == "maintenance.archived"
    )
    sections = [
        section.model_copy(update={"findings": [*section.findings, archived]})
        if section.kind == ReviewKind.engineering
        else section
        for section in report.sections
    ]
    report = DueDiligenceReport.model_validate({**report.model_dump(), "sections": sections})

    verified = EvidenceVerifier().verify(report, snapshot)

    assert sum(item.rule_id == "maintenance.archived" for item in verified.risks) == 1
    assert verified.recommendation == Recommendation.not_recommended
