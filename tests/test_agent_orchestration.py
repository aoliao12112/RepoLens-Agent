import asyncio
from collections.abc import Sequence

import pytest
from test_rule_analysis import NOW, TASK_ID, active_snapshot

from repolens.agent_orchestration import (
    AgentOrchestrator,
    ReviewAgentResult,
    ReviewPlanner,
    build_orchestrator,
)
from repolens.models import (
    AnalysisMode,
    ClaimType,
    Evidence,
    ModelFinding,
    ModelReview,
    ModelUsage,
    ReviewKind,
)
from repolens.rule_analysis import RuleBasedAnalyzer
from repolens.settings import RepoLensSettings


class FakeReviewer:
    def __init__(
        self,
        *,
        fail_kind: ReviewKind | None = None,
        invalid_evidence_kind: ReviewKind | None = None,
        delay: float = 0.01,
    ) -> None:
        self.fail_kind = fail_kind
        self.invalid_evidence_kind = invalid_evidence_kind
        self.delay = delay
        self.active = 0
        self.maximum_active = 0

    async def review(
        self,
        kind: ReviewKind,
        snapshot: object,
        target: str,
        evidence: Sequence[Evidence],
    ) -> ReviewAgentResult:
        del snapshot, target
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if kind == self.fail_kind:
                raise RuntimeError("model failed")
            evidence_ids = (
                ["fabricated-evidence"]
                if kind == self.invalid_evidence_kind
                else [evidence[0].evidence_id]
            )
            return ReviewAgentResult(
                review=ModelReview(
                    kind=kind,
                    score=80,
                    summary=f"{kind.value} model summary",
                    findings=[
                        ModelFinding(
                            title="模型补充",
                            description="基于允许证据给出的补充推断。",
                            severity="medium",
                            claim_type=ClaimType.inference,
                            evidence_ids=evidence_ids,
                        )
                    ],
                ),
                usage=ModelUsage(model="test-model", input_tokens=10, output_tokens=5),
            )
        finally:
            self.active -= 1


def rules() -> RuleBasedAnalyzer:
    return RuleBasedAnalyzer(now=lambda: NOW)


def test_planner_changes_reviews_based_on_target() -> None:
    planner = ReviewPlanner()

    basic = {item.kind for item in planner.plan("看看维护情况").reviews}
    development = {item.kind for item in planner.plan("用于 Python Agent 二次开发").reviews}

    assert basic == {ReviewKind.maintenance, ReviewKind.target_fit}
    assert development == set(ReviewKind)


@pytest.mark.asyncio
async def test_no_reviewer_returns_complete_rule_report() -> None:
    orchestrator = AgentOrchestrator(rule_analyzer=rules(), reviewer=None)

    report = await orchestrator.analyze(active_snapshot(), "Python Agent 二次开发", TASK_ID)

    assert report.analysis_mode == AnalysisMode.rule_only
    assert report.degraded_reviews == []
    assert len(report.sections) == 4


@pytest.mark.asyncio
async def test_specialists_run_in_parallel_and_merge_results() -> None:
    reviewer = FakeReviewer()
    orchestrator = AgentOrchestrator(rule_analyzer=rules(), reviewer=reviewer)

    report = await orchestrator.analyze(active_snapshot(), "Python Agent 二次开发", TASK_ID)

    assert reviewer.maximum_active == 4
    assert report.analysis_mode == AnalysisMode.hybrid
    assert report.degraded_reviews == []
    assert len(report.model_usage) == 4
    assert all("Agent 补充" in section.summary for section in report.sections)
    assert any(finding.finding_id.startswith("model.") for finding in report.risks)


@pytest.mark.asyncio
async def test_one_specialist_failure_degrades_only_that_review() -> None:
    reviewer = FakeReviewer(fail_kind=ReviewKind.license)
    orchestrator = AgentOrchestrator(rule_analyzer=rules(), reviewer=reviewer)

    report = await orchestrator.analyze(active_snapshot(), "Python Agent 二次开发", TASK_ID)

    assert report.analysis_mode == AnalysisMode.hybrid
    assert report.degraded_reviews == [ReviewKind.license]
    license_section = next(
        section for section in report.sections if section.kind == ReviewKind.license
    )
    assert "Agent 补充" not in license_section.summary
    assert len(report.model_usage) == 3


@pytest.mark.asyncio
async def test_unknown_evidence_id_causes_review_fallback() -> None:
    reviewer = FakeReviewer(invalid_evidence_kind=ReviewKind.engineering)
    orchestrator = AgentOrchestrator(rule_analyzer=rules(), reviewer=reviewer)

    report = await orchestrator.analyze(active_snapshot(), "Python Agent 二次开发", TASK_ID)

    assert ReviewKind.engineering in report.degraded_reviews
    engineering = next(
        section for section in report.sections if section.kind == ReviewKind.engineering
    )
    assert not any(item.finding_id.startswith("model.") for item in engineering.findings)


@pytest.mark.asyncio
async def test_timeout_degrades_reviews_instead_of_failing_report() -> None:
    reviewer = FakeReviewer(delay=0.05)
    orchestrator = AgentOrchestrator(
        rule_analyzer=rules(), reviewer=reviewer, timeout_seconds=0.001
    )

    report = await orchestrator.analyze(active_snapshot(), "检查维护情况", TASK_ID)

    assert report.analysis_mode == AnalysisMode.rule_only
    assert set(report.degraded_reviews) == {ReviewKind.maintenance, ReviewKind.target_fit}


def test_builder_disables_model_when_api_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    orchestrator = build_orchestrator(RepoLensSettings(_env_file=None), rules())

    assert orchestrator.model_enabled is False
