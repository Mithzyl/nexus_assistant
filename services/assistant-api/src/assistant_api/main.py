"""Composition root: build the app, its database, providers and Nexus client.

No request path starts a timer. Commute scheduling runs in separate processes;
this app only validates input, writes durable state and answers reads.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from assistant_api.commute.bark_router import router as bark_router
from assistant_api.commute.router import router as commute_router
from assistant_api.commute.schemas import ErrorEnvelope
from assistant_api.commute.runtime import (
    CommuteRuntime,
    build_runtime,
    validate_commute_configuration,
)
from assistant_api.core.config import Settings, get_settings
from assistant_api.core.errors import AssistantError
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.models import Base
from assistant_api.nexus_client import NexusError, NexusPilotClient
from assistant_api.routers import (
    assistant,
    auth,
    data_sources,
    internal_tools,
    matters,
    sync_ios,
)
from assistant_api.services.run_service import shutdown_background_tasks


def create_app(
    settings: Settings | None = None,
    *,
    nexus_transport: httpx.AsyncBaseTransport | None = None,
    commute_runtime: CommuteRuntime | None = None,
    bark_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build one isolated application instance.

    Tests pass explicit settings, an `httpx.MockTransport` and/or a fixed-clock
    commute runtime, which is why no module-level engine or client exists.
    """

    resolved = settings or get_settings()
    validate_commute_configuration(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine, factory = create_engine_and_factory(resolved.database_url)
        app.state.engine = engine
        app.state.session_factory = factory
        if resolved.allow_schema_create:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        app.state.nexus_client = NexusPilotClient(
            base_url=resolved.nexus_base_url,
            application_id=resolved.nexus_application_id,
            application_token=resolved.nexus_application_token.get_secret_value(),
            request_timeout_seconds=resolved.nexus_request_timeout_seconds,
            stream_timeout_seconds=resolved.nexus_stream_timeout_seconds,
            transport=nexus_transport,
        )
        app.state.commute_runtime = commute_runtime or build_runtime(resolved)
        app.state.bark_transport = bark_transport
        try:
            yield
        finally:
            await shutdown_background_tasks()
            await app.state.commute_runtime.aclose()
            await app.state.nexus_client.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Personal Assistant API",
        version="0.1.0",
        description=(
            "本地用户与业务数据服务；AI 执行通过 NexusPilot 应用接口完成。"
            "通勤提醒的天气/ETA/推送由服务端供应商适配器提供，"
            "定时任务运行在独立调度器与 worker 进程。"
        ),
        lifespan=lifespan,
        responses={
            400: {"model": ErrorEnvelope, "description": "请求无效"},
            401: {"model": ErrorEnvelope, "description": "未认证"},
            403: {"model": ErrorEnvelope, "description": "无权访问"},
            404: {"model": ErrorEnvelope, "description": "资源不存在"},
            409: {"model": ErrorEnvelope, "description": "冲突"},
            422: {"model": ErrorEnvelope, "description": "字段校验失败"},
            428: {"model": ErrorEnvelope, "description": "缺少 If-Match"},
            500: {"model": ErrorEnvelope, "description": "服务器错误"},
            503: {"model": ErrorEnvelope, "description": "供应商暂不可用"},
        },
    )
    app.state.settings = resolved

    app.include_router(auth.router)
    app.include_router(assistant.router)
    app.include_router(internal_tools.router)
    app.include_router(data_sources.router)
    app.include_router(data_sources.content_router)
    app.include_router(commute_router)
    app.include_router(bark_router)
    app.include_router(matters.router)
    app.include_router(sync_ios.router)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AssistantError)
    async def assistant_error_handler(request: Request, error: AssistantError) -> JSONResponse:
        """Return the stable error envelope for expected local failures."""

        return _error_response(
            request,
            error.status_code,
            error.code,
            error.message,
            retryable=bool(getattr(error, "retryable", False)),
        )

    @app.exception_handler(NexusError)
    async def nexus_error_handler(request: Request, error: NexusError) -> JSONResponse:
        """Translate remote failures without exposing remote internals."""

        status_code, code = _nexus_status(error)
        return _error_response(
            request,
            status_code,
            code,
            str(error),
            retryable=bool(error.retryable),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            422,
            "invalid_request",
            "请求字段格式或取值无效。",
            retryable=False,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        code = "not_found" if error.status_code == 404 else "http_error"
        if error.status_code == 405:
            code = "method_not_allowed"
        return _error_response(
            request,
            error.status_code,
            code,
            "请求无法处理。",
            retryable=False,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, _error: Exception) -> JSONResponse:
        return _error_response(
            request,
            500,
            "internal_error",
            "服务器暂时无法处理该请求。",
            retryable=True,
        )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        """Report liveness plus non-secret provider configuration state."""

        return {
            "status": "ok",
            "environment": resolved.environment,
            "nexus_application": resolved.nexus_application_id,
            "schema_auto_create": resolved.allow_schema_create,
            "commute_provider_mode": resolved.commute_provider_mode,
        }

    return app


def _nexus_status(error: NexusError) -> tuple[int, str]:
    """Map a remote failure to a local status that keeps the cause distinguishable."""

    if error.code == "nexus_timeout":
        return 504, error.code
    if error.code == "nexus_unavailable":
        return 503, error.code
    if error.code == "nexus_resource_not_found":
        return 404, error.code
    return 502, error.code


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool,
) -> JSONResponse:
    """Build one envelope shape for every error this service returns."""

    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
            },
            "request_id": request_id,
        },
        headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
    )


app = create_app()
