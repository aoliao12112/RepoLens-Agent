from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent_orchestration import build_orchestrator
from .analysis_service import AnalysisService
from .confirmation import ConfirmationRepository
from .database import Database
from .errors import ConfirmationError, ErrorCode, RepoLensError
from .evidence_verification import EvidenceVerifier
from .logging_config import configure_logging
from .models import AnalysisRequest, AnalysisTask, DueDiligenceReport, ReportConfirmation
from .rule_analysis import RuleBasedAnalyzer
from .settings import RepoLensSettings
from .tasks import TaskRepository

_WEB_DIRECTORY = Path(__file__).with_name("web")


class ConfirmationInput(BaseModel):
    target_confirmed: bool
    accepted_risk_ids: list[str] = Field(default_factory=list, max_length=100)
    notes: str | None = Field(default=None, max_length=2_000)


class HealthResponse(BaseModel):
    status: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool
    request_id: str


def create_app(
    *,
    settings: RepoLensSettings | None = None,
    service: AnalysisService | None = None,
) -> FastAPI:
    resolved_settings = settings or RepoLensSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        if service is not None:
            app.state.analysis_service = service
            yield
            return
        database = Database(resolved_settings.database_url)
        await database.initialize()
        tasks = TaskRepository(database)
        await tasks.fail_incomplete_tasks()
        app.state.analysis_service = AnalysisService(
            settings=resolved_settings,
            tasks=tasks,
            confirmations=ConfirmationRepository(database),
            orchestrator=build_orchestrator(resolved_settings, RuleBasedAnalyzer()),
            verifier=EvidenceVerifier(),
        )
        yield

    app = FastAPI(title="RepoLens Agent API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id[:128]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RepoLensError)
    async def repolens_error_handler(request: Request, exc: RepoLensError) -> JSONResponse:
        return _error_response(
            request,
            _status_code_for_error(exc.code),
            exc.code.value,
            exc.message,
            exc.retryable,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, _: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            422,
            ErrorCode.invalid_input.value,
            "请求参数格式不正确",
            False,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _: Exception) -> JSONResponse:
        return _error_response(request, 500, "internal_error", "服务内部错误", False)

    @app.post("/api/analyses", response_model=AnalysisTask, status_code=202)
    async def create_analysis(
        payload: AnalysisRequest,
        background_tasks: BackgroundTasks,
        request: Request,
    ) -> AnalysisTask:
        task = await _service(request).create_task(payload)
        background_tasks.add_task(_service(request).run, task.task_id)
        return task

    @app.get("/api/analyses/{task_id}", response_model=AnalysisTask)
    async def get_analysis(task_id: UUID, request: Request) -> AnalysisTask:
        return await _service(request).get_task(task_id)

    @app.get("/api/analyses/{task_id}/report", response_model=DueDiligenceReport)
    async def get_report(task_id: UUID, request: Request) -> DueDiligenceReport:
        report = await _service(request).get_report(task_id)
        if report is None:
            raise ConfirmationError("任务尚未生成报告")
        return report

    @app.post("/api/analyses/{task_id}/confirm", response_model=DueDiligenceReport)
    async def confirm_report(
        task_id: UUID,
        payload: ConfirmationInput,
        request: Request,
    ) -> DueDiligenceReport:
        confirmation = ReportConfirmation(
            task_id=task_id,
            target_confirmed=payload.target_confirmed,
            accepted_risk_ids=payload.accepted_risk_ids,
            notes=payload.notes,
            confirmed_at=datetime.now(UTC),
        )
        return await _service(request).confirm(task_id, confirmation)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/", include_in_schema=False)
    async def web_app() -> FileResponse:
        return FileResponse(_WEB_DIRECTORY / "index.html")

    app.mount("/static", StaticFiles(directory=_WEB_DIRECTORY), name="static")

    return app


def _service(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


def _status_code_for_error(code: ErrorCode) -> int:
    if code == ErrorCode.invalid_input:
        return 400
    if code in {ErrorCode.repository_unavailable, ErrorCode.task_not_found}:
        return 404
    if code == ErrorCode.github_rate_limited:
        return 429
    if code in {ErrorCode.upstream_timeout, ErrorCode.upstream_temporary_failure}:
        return 503
    if code in {ErrorCode.confirmation_invalid, ErrorCode.invalid_task_transition}:
        return 409
    return 500


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    payload = ErrorResponse(
        code=code,
        message=message,
        retryable=retryable,
        request_id=request_id,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


app = create_app()
