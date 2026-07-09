"""FastAPI 应用入口 — 主控内部 REST API（Tauri shell 调用）。

仅本地监听（默认 ``127.0.0.1:8666``），不直接对外暴露。

完整接口见 ``docs/api/master-rest.openapi.yaml``。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from matrix import __version__
from matrix.api.routes import (
    accounts as accounts_routes,
    agent_runs as agent_runs_routes,
    alerts as alerts_routes,
    analytics as analytics_routes,
    chat as chat_routes,
    devices as devices_routes,
    goals as goals_routes,
    health as health_routes,
    interactions as interactions_routes,
    kb as kb_routes,
    logs as logs_routes,
    metrics as metrics_routes,
    notes as notes_routes,
    personas as personas_routes,
    settings as settings_routes,
)
from matrix.agent._default_repository import DefaultAgentRepository
from matrix.agent.bootstrap import build_agent_services, build_run_manager
from matrix.agent.run_manager import init_manager
from matrix.agent.runner import AgentRunWorker, set_worker
from matrix.api.schemas import ErrorDetail, ErrorResponse
from matrix.db import create_engine
from matrix.llm.embeddings import EmbeddingClient
from matrix.llm.router import get_default_client
from matrix.monitoring import setup_monitoring, shutdown_tracing
from matrix.monitoring.logging import get_logger
from matrix.monitoring.middleware import install_middleware

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# App 工厂：方便测试用临时 DB 启动
# ---------------------------------------------------------------------------


def create_app(
    *,
    database_url: str | None = None,
    enable_monitoring_middleware: bool = True,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """构造 FastAPI app。

    Args:
        database_url: 覆盖 ``DATABASE_URL``。测试时可传 ``sqlite+aiosqlite:///:memory:``。
        enable_monitoring_middleware: 是否装 monitoring middleware（测试可关）。
        cors_origins: 允许的跨域 origin。Tauri 桌面应用一般是 ``tauri://localhost`` /
            ``http://localhost:1420``，未传时使用一套安全的本地默认值。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # ---- startup ----
        if database_url:
            from matrix.db import session as session_mod

            engine = create_engine(database_url)
            session_mod.set_engine(engine)
            app.state.db_engine = engine
            app.state.db_session_factory = async_sessionmaker(
                engine, expire_on_commit=False, autoflush=False
            )
        else:
            from matrix.db.session import get_session_factory

            app.state.db_session_factory = get_session_factory()

        # ---- 装配 Agent 服务（LLM/KB 检索器/写库器）并启动 worker ----
        try:
            from matrix.kb.embedding import EmbeddingService
            from matrix.kb.retrieval import Retriever
            from matrix.kb.store import KbStore

            from matrix.agent._services import set_services as _set_services
            from matrix.api._agent_factory import build_runtime_services

            services = await build_runtime_services(
                app.state.db_session_factory,
                llm_factory=get_default_client,
                embedding_client_cls=EmbeddingClient,
            )
            _set_services(services)
            manager = build_run_manager(
                services=services, repository=DefaultAgentRepository()
            )
            init_manager(manager)

            # worker：每 1s 扫一次 running run，调 RunManager.start_run
            worker = AgentRunWorker(app.state.db_session_factory, poll_interval=1.0)
            worker.start()
            set_worker(worker)
            app.state.agent_worker = worker

            # v0.7 Phase 4：watchdog 兜底卡死 run（每 30s 扫一次）
            try:
                from matrix.agent.watcher import (
                    AgentRunScanner,
                    AgentRunWatchdog,
                    WatchdogConfig,
                )

                scanner = AgentRunScanner(app.state.db_session_factory)
                watchdog = AgentRunWatchdog(
                    scanner,
                    config=WatchdogConfig(
                        poll_interval_sec=30.0,
                        stuck_threshold_sec=600,
                        dry_run=True,  # 默认观察一周再切真实
                    ),
                )
                watchdog.start()
                app.state.agent_watchdog = watchdog
            except Exception:  # pragma: no cover
                logger.warning("agent_watchdog setup failed", exc_info=True)
        except Exception as e:  # pragma: no cover
            logger.warning(
                "agent services / worker setup failed; "
                "Agent runs created via API will NOT auto-execute",
                error=e,
                exc_info=True,
            )

        logger.info(
            "matrix.api starting", version=__version__, db=str(database_url or "default")
        )
        app.state.start_time = time.monotonic()
        yield
        # ---- shutdown ----
        # 先停 worker（避免在 DB 关闭后还查表）
        worker = getattr(app.state, "agent_worker", None)
        if worker is not None:
            try:
                await worker.stop()
            except Exception:  # pragma: no cover
                logger.warning("agent worker stop failed", exc_info=True)
        # v0.7 Phase 4: 停 watchdog
        watchdog = getattr(app.state, "agent_watchdog", None)
        if watchdog is not None:
            try:
                await watchdog.stop()
            except Exception:  # pragma: no cover
                logger.warning("agent watchdog stop failed", exc_info=True)
                logger.exception("agent worker stop failed")
        try:
            shutdown_tracing()
        except Exception:  # pragma: no cover
            logger.exception("shutdown_tracing failed")
        if app.state.db_engine is not None:
            try:
                await app.state.db_engine.dispose()
            except Exception:  # pragma: no cover
                logger.exception("engine.dispose failed")
        logger.info("matrix.api stopped")

    app = FastAPI(
        title="Matrix Master API",
        version=__version__,
        description="主控内部 REST API（Tauri shell 调用）",
        lifespan=lifespan,
    )
    app.state.db_engine = None
    app.state.start_time = time.monotonic()

    # ---- CORS（Tauri 桌面应用来源） ----
    origins = cors_origins or [
        "tauri://localhost",
        "http://localhost:1420",
        "http://localhost:8666",
        "http://127.0.0.1:8666",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- monitoring middleware ----
    if enable_monitoring_middleware:
        install_middleware(app)

    # ---- 全局异常处理 ----
    _install_exception_handlers(app)

    # ---- 路由 ----
    API_PREFIX = "/api/v1"
    app.include_router(health_routes.router, prefix=API_PREFIX)
    app.include_router(devices_routes.router, prefix=API_PREFIX)
    app.include_router(accounts_routes.router, prefix=API_PREFIX)
    app.include_router(personas_routes.router, prefix=API_PREFIX)
    app.include_router(notes_routes.router, prefix=API_PREFIX)
    app.include_router(goals_routes.router, prefix=API_PREFIX)
    app.include_router(settings_routes.router, prefix=API_PREFIX)
    app.include_router(agent_runs_routes.router, prefix=API_PREFIX)
    app.include_router(chat_routes.router, prefix=API_PREFIX)
    app.include_router(kb_routes.router, prefix=API_PREFIX)
    app.include_router(alerts_routes.router, prefix=API_PREFIX)
    app.include_router(analytics_routes.router, prefix=API_PREFIX)
    app.include_router(metrics_routes.router, prefix=API_PREFIX)
    app.include_router(interactions_routes.router, prefix=API_PREFIX)  # v0.6
    app.include_router(logs_routes.router, prefix=API_PREFIX)            # PR 6

    return app


# ---------------------------------------------------------------------------
# 异常处理
# ---------------------------------------------------------------------------


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # 保留 401 / 403 不做 envelope 包装（鉴权语义），其它业务异常走 error envelope
        if exc.status_code in (401, 403, 404):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        code = _code_for_status(exc.status_code)
        body = ErrorResponse(
            ok=False,
            error=ErrorDetail(
                code=code,
                message=str(exc.detail),
                retryable=exc.status_code >= 500,
            ),
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = ErrorResponse(
            ok=False,
            error=ErrorDetail(
                code="INVALID_PARAMS",
                message=str(exc.errors()),
                retryable=False,
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=body.model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exc_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", path=request.url.path)
        body = ErrorResponse(
            ok=False,
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message=str(exc),
                retryable=True,
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=body.model_dump(),
        )


def _code_for_status(status_code: int) -> str:
    if status_code == 422:
        return "INVALID_PARAMS"
    if status_code == 400:
        return "INVALID_PARAMS"
    if status_code == 409:
        return "INVALID_PARAMS"
    if status_code >= 500:
        return "INTERNAL_ERROR"
    return "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# 默认 app（供 uvicorn ``matrix.api.app:app`` 加载）
# ---------------------------------------------------------------------------

# 初始化 monitoring（tracing + logging），失败也不阻塞启动
try:
    setup_monitoring("matrix-api", otlp_endpoint=None)
except Exception:  # pragma: no cover
    logging.getLogger(__name__).warning("setup_monitoring failed", exc_info=True)

app = create_app()


def main() -> None:
    """CLI 入口。"""
    import uvicorn

    uvicorn.run(
        "matrix.api.app:app",
        host="127.0.0.1",
        port=8666,
        reload=False,
    )


if __name__ == "__main__":
    main()
