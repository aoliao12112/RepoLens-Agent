from typing import Any

import httpx
import pytest
from test_analysis_service import FakeCollector, build_service
from test_rule_analysis import active_snapshot

from repolens.api import create_app
from repolens.settings import RepoLensSettings


@pytest.mark.asyncio
async def test_api_analysis_report_and_confirmation_flow(tmp_path: Any) -> None:
    service, _ = await build_service(tmp_path, collector=FakeCollector(active_snapshot()))
    app = create_app(settings=RepoLensSettings(_env_file=None), service=service)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/analyses",
            json={
                "repository_url": "https://github.com/owner/repo",
                "target": "Python Agent 二次开发",
                "enable_model": False,
            },
            headers={"X-Request-ID": "request-123"},
        )
        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == "request-123"
        task_id = response.json()["task_id"]

        task_response = await client.get(f"/api/analyses/{task_id}")
        assert task_response.json()["status"] == "awaiting_confirmation"

        report_response = await client.get(f"/api/analyses/{task_id}/report")
        assert report_response.status_code == 200
        report = report_response.json()
        required = [
            risk["finding_id"]
            for risk in report["risks"]
            if risk["severity"] in {"high", "critical"}
        ]
        confirm_response = await client.post(
            f"/api/analyses/{task_id}/confirm",
            json={"target_confirmed": True, "accepted_risk_ids": required},
        )
        assert confirm_response.status_code == 200
        assert confirm_response.json()["status"] == "confirmed"


@pytest.mark.asyncio
async def test_api_rejects_non_github_url_with_stable_error(tmp_path: Any) -> None:
    service, _ = await build_service(tmp_path, collector=FakeCollector(active_snapshot()))
    app = create_app(settings=RepoLensSettings(_env_file=None), service=service)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/analyses",
            json={
                "repository_url": "https://gitlab.com/owner/repo",
                "target": "检查项目",
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"
    assert response.json()["retryable"] is False
    assert "request_id" in response.json()


@pytest.mark.asyncio
async def test_health_endpoint(tmp_path: Any) -> None:
    service, _ = await build_service(tmp_path, collector=FakeCollector(active_snapshot()))
    app = create_app(settings=RepoLensSettings(_env_file=None), service=service)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_web_app_and_static_assets_are_served(tmp_path: Any) -> None:
    service, _ = await build_service(tmp_path, collector=FakeCollector(active_snapshot()))
    app = create_app(settings=RepoLensSettings(_env_file=None), service=service)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/")
        script = await client.get("/static/app.js")
        styles = await client.get("/static/styles.css")

    assert page.status_code == 200
    assert "RepoLens Agent" in page.text
    assert 'id="analysis-form"' in page.text
    assert script.status_code == 200 and "pollTask" in script.text
    assert styles.status_code == 200 and "--lime" in styles.text
