"""FastAPI 应用入口 — 主控内部 REST API（Web frontend 调用）。

仅本地监听（默认 ``127.0.0.1:8666``），不直接对外暴露。

完整接口见 ``docs/api/master-rest.openapi.yaml``。
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from matrix.monitoring.logging import get_logger

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
    learning as learning_routes,
    health as health_routes,
    interactions as interactions_routes,
    kb as kb_routes,
    logs as logs_routes,
    metrics as metrics_routes,
    notes as notes_routes,
    notifications as notifications_routes,
    personas as personas_routes,
    settings as settings_routes,
)
from matrix.agent._default_repository import DefaultAgentRepository
from matrix.agent.bootstrap import build_run_manager
from matrix.agent.run_manager import init_manager
from matrix.agent.orchestrator_runner import (
    GoalOrchestratorWorker,
    set_orchestrator_worker,
)
from matrix.agent.runner import AgentRunWorker, set_worker
from matrix.agent.goal_stuck_watchdog import (  # noqa: E402  P2-2
    GoalStuckScanner,
    GoalStuckWatchdog,
    GoalStuckWatchdogConfig,
)
from matrix.api._agent_factory import _LazyConfigReader  # noqa: E402  P2-2
from matrix.api.schemas import ErrorDetail, ErrorResponse
from matrix.db import create_engine
from matrix.llm.embeddings import EmbeddingClient
from matrix.llm.router import get_default_client
from matrix.monitoring import setup_monitoring, shutdown_tracing
from matrix.monitoring.alert_scanner import AlertScanner
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
        cors_origins: 允许的跨域 origin。Web frontend 开发时一般是 ``http://localhost:1420``
            （vite dev server），生产同源访问 ``http://localhost:8666``。
            未传时使用一套安全的本地默认值。
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
        services: Any = None
        try:

            from matrix.agent._services import set_services as _set_services
            from matrix.api._agent_factory import build_runtime_services
            from matrix.scheduler.db import DbTaskLoader, DbTaskStatusWriter, DbTaskWriter
            from matrix.scheduler.db_task_executor import DeviceTaskExecutor
            from matrix.scheduler.rate_limiter import RateLimiter
            from matrix.scheduler.scheduler import Scheduler

            # ---- v0.7 P0-1：调度器接入 dispatch_node 的 task_writer ----
            task_writer = DbTaskWriter(app.state.db_session_factory)

            services = await build_runtime_services(
                app.state.db_session_factory,
                llm_factory=get_default_client,
                embedding_client_cls=EmbeddingClient,
                task_writer=task_writer,
            )
            _set_services(services)
            # Phase 1：保留 notifier 引用，lifespan 收尾需 aclose 关闭长生命周期 httpx 客户端
            app.state.notifier = services.notifier
            manager = build_run_manager(
                services=services, repository=DefaultAgentRepository()
            )
            init_manager(manager)

            # worker：每 1s 扫一次 running run，调 RunManager.start_run
            worker = AgentRunWorker(app.state.db_session_factory, poll_interval=1.0)
            worker.start()
            set_worker(worker)
            app.state.agent_worker = worker

            # goal orchestrator：每 5s 扫一次 phase≠DONE 的 goal，调 advance_goal
            orchestrator = GoalOrchestratorWorker(
                app.state.db_session_factory, poll_interval=5.0
            )
            orchestrator.start()
            set_orchestrator_worker(orchestrator)
            app.state.goal_orchestrator = orchestrator

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
                        dry_run=False,  # worker 失败重试耗尽后由 watchdog 兜底标 timeout
                    ),
                )
                # v0.7 P2-1：从 app_config 表覆盖 watchdog 配置（运维可在线调阈值）
                try:
                    from matrix.api._agent_factory import _LazyConfigReader

                    await watchdog.configure_from_config_reader(
                        _LazyConfigReader(app.state.db_session_factory)
                    )
                except Exception:  # pragma: no cover
                    logger.warning(
                        "watchdog configure_from_config_reader failed", exc_info=True
                    )
                watchdog.start()
                app.state.agent_watchdog = watchdog
            except Exception:  # pragma: no cover
                logger.warning("agent_watchdog setup failed", exc_info=True)

            # v0.7 P2-2：goal_stuck_watchdog 兜底卡在 PENDING 的 goal
            # （仅当协调员静默死/没启动时需要这条救援路径）
            try:
                import os

                _g_default_interval = float(
                    os.environ.get("MATRIX_GOAL_STUCK_WATCHDOG_INTERVAL_SEC", "60")
                )
                _g_default_threshold = int(
                    os.environ.get("MATRIX_GOAL_STUCK_WATCHDOG_THRESHOLD_SEC", "120")
                )
                _g_dry_run_raw = os.environ.get(
                    "MATRIX_GOAL_STUCK_WATCHDOG_DRY_RUN", "0"
                )
                _g_dry_run = _g_dry_run_raw.strip().lower() in {"1", "true", "yes", "on"}

                g_scanner = GoalStuckScanner(app.state.db_session_factory)
                g_watchdog = GoalStuckWatchdog(
                    g_scanner,
                    config=GoalStuckWatchdogConfig(
                        poll_interval_sec=_g_default_interval,
                        stuck_threshold_sec=_g_default_threshold,
                        dry_run=_g_dry_run,
                    ),
                )
                try:
                    await g_watchdog.configure_from_config_reader(
                        _LazyConfigReader(app.state.db_session_factory)
                    )
                except Exception:  # pragma: no cover
                    logger.warning(
                        "goal_stuck_watchdog configure_from_config_reader failed",
                        exc_info=True,
                    )
                g_watchdog.start()
                app.state.goal_stuck_watchdog = g_watchdog
            except Exception:  # pragma: no cover
                logger.warning("goal_stuck_watchdog setup failed", exc_info=True)

            # v0.7 Phase 4：AlertScanner 后台定期跑 monitoring/alerts 全部 check
            # 必须 services 装配好之后启动（依赖 notifier / config_reader）
            try:
                scanner = AlertScanner(
                    session_factory=app.state.db_session_factory,
                    config_reader=services.config,  # type: ignore[union-attr]
                    notifier=services.notifier,  # type: ignore[union-attr]
                )
                scanner.start()
                app.state.alert_scanner = scanner
            except Exception:  # pragma: no cover
                logger.warning("alert_scanner setup failed", exc_info=True)
        except Exception as e:  # pragma: no cover
            logger.warning(
                "agent services / worker setup failed; "
                "Agent runs created via API will NOT auto-execute",
                error=e,
                exc_info=True,
            )

        # ---- v0.7 P0-1：调度器主循环（拉 pending → execute → 写回 status） ----
        # scheduler 失败不能阻塞 lifespan（dev 环境没 DB / 没 APK 也能起 API）
        try:
            from matrix.device.adapters import ApkHttpClient
            from matrix.device.endpoints import DeviceEndpointResolver

            apk_client = ApkHttpClient(
                resolver=DeviceEndpointResolver(app.state.db_session_factory)
            )
            app.state.apk_client = apk_client
            from matrix.scheduler.rate_limiter import DbDailyCounter

            rate_limiter = RateLimiter(
                jitter_base=0.0,
                jitter_sigma=0.0,
                daily_counter=DbDailyCounter(app.state.db_session_factory),
            )
            executor = DeviceTaskExecutor(
                device_publisher=apk_client,
                device_collector=apk_client,
                device_interactor=apk_client,
            )
            scheduler = Scheduler(
                loader=DbTaskLoader(app.state.db_session_factory),
                writer=DbTaskStatusWriter(app.state.db_session_factory),
                executor=executor,
                rate_limiter=rate_limiter,
                poll_interval=1.0,
            )
            app.state.scheduler_task = asyncio.create_task(
                scheduler.run(), name="matrix-scheduler"
            )
            app.state.scheduler = scheduler
            logger.info("matrix.scheduler started")
        except Exception:  # pragma: no cover
            logger.warning("matrix.scheduler setup failed; tasks will not auto-execute", exc_info=True)

        logger.info(
            "matrix.api starting", version=__version__, db=str(database_url or "default")
        )
        app.state.start_time = time.monotonic()
        yield
        # ---- shutdown ----
        # 先停后台扫描器（避免 DB 关了还查表）
        alert_scanner = getattr(app.state, "alert_scanner", None)
        if alert_scanner is not None:
            try:
                await alert_scanner.stop()
            except Exception:  # pragma: no cover
                logger.warning("alert_scanner stop failed", exc_info=True)
        # 先停 worker（避免在 DB 关闭后还查表）
        worker = getattr(app.state, "agent_worker", None)
        if worker is not None:
            try:
                await worker.stop()
            except Exception:  # pragma: no cover
                logger.warning("agent worker stop failed", exc_info=True)
        # 停 goal orchestrator
        orchestrator = getattr(app.state, "goal_orchestrator", None)
        if orchestrator is not None:
            try:
                await orchestrator.stop()
            except Exception:  # pragma: no cover
                logger.warning("goal orchestrator stop failed", exc_info=True)
        # v0.7 Phase 4: 停 watchdog
        watchdog = getattr(app.state, "agent_watchdog", None)
        if watchdog is not None:
            try:
                await watchdog.stop()
            except Exception:  # pragma: no cover
                logger.warning("agent watchdog stop failed", exc_info=True)
                logger.exception("agent worker stop failed")
        # v0.7 P2-2: 停 goal_stuck_watchdog
        g_watchdog = getattr(app.state, "goal_stuck_watchdog", None)
        if g_watchdog is not None:
            try:
                await g_watchdog.stop()
            except Exception:  # pragma: no cover
                logger.warning("goal_stuck_watchdog stop failed", exc_info=True)
        # v0.7 P0-1: 停调度器
        # Scheduler.stop() 是同步方法（只 set 事件），不要 await
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is not None:
            try:
                scheduler.stop()
            except Exception:  # pragma: no cover
                logger.warning("scheduler stop failed", exc_info=True)
        scheduler_task = getattr(app.state, "scheduler_task", None)
        if scheduler_task is not None and not scheduler_task.done():
            try:
                await asyncio.wait_for(scheduler_task, timeout=5.0)
            except asyncio.TimeoutError:  # pragma: no cover
                scheduler_task.cancel()
                logger.warning("scheduler task cancel after timeout")
        # 关 APK HTTP 客户端
        apk_client = getattr(app.state, "apk_client", None)
        if apk_client is not None:
            try:
                await apk_client.aclose()
            except Exception:  # pragma: no cover
                logger.warning("apk_client aclose failed", exc_info=True)
        # Phase 1：关闭 WebhookNotifier 内部 httpx 客户端
        notifier = getattr(app.state, "notifier", None)
        if notifier is not None and hasattr(notifier, "aclose"):
            try:
                await notifier.aclose()
            except Exception:  # pragma: no cover
                logger.warning("notifier aclose failed", exc_info=True)
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
        description="主控内部 REST API（Web frontend 调用）",
        lifespan=lifespan,
    )
    app.state.db_engine = None
    app.state.start_time = time.monotonic()

    # ---- CORS（Web frontend 来源，浏览器开发时访问 vite dev server） ----
    origins = cors_origins or [
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
    app.include_router(learning_routes.router, prefix=API_PREFIX)
    app.include_router(settings_routes.router, prefix=API_PREFIX)
    app.include_router(agent_runs_routes.router, prefix=API_PREFIX)
    app.include_router(chat_routes.router, prefix=API_PREFIX)
    app.include_router(kb_routes.router, prefix=API_PREFIX)
    app.include_router(alerts_routes.router, prefix=API_PREFIX)
    app.include_router(notifications_routes.router, prefix=API_PREFIX)  # Phase 1
    app.include_router(analytics_routes.router, prefix=API_PREFIX)
    app.include_router(metrics_routes.router, prefix=API_PREFIX)
    app.include_router(interactions_routes.router, prefix=API_PREFIX)  # v0.6
    app.include_router(logs_routes.router, prefix=API_PREFIX)            # PR 6

    return app


# ---------------------------------------------------------------------------
# 异常处理
# ---------------------------------------------------------------------------

# HTTP status → envelope code 映射（按 plan / 错误码语义）
_STATUS_CODE_MAP: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "INVALID_PARAMS",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
}


def _install_exception_handlers(app: FastAPI) -> None:
    """统一错误响应 envelope：所有异常（HTTP / 校验 / unhandled）都走
    ``{ok: False, error: {code, message, retryable}}``。

    客户端字段从 ``detail`` 切到 ``error.code``；unhandled 不再泄漏 ``str(exc)``，
    改为暴露 ``X-Trace-Id`` header 让客户端回报问题时能关联日志。
    """

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code = _STATUS_CODE_MAP.get(
            exc.status_code,
            "INTERNAL_ERROR" if exc.status_code >= 500 else "UNKNOWN",
        )
        body = ErrorResponse(
            ok=False,
            error=ErrorDetail(
                code=code,  # type: ignore[arg-type]
                message=str(exc.detail) if exc.detail is not None else code,
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
                code="VALIDATION_ERROR",
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
        # 安全：不要把 str(exc) 漏给客户端（可能含 SQL / 内部路径 / 堆栈）；
        # 仅服务端 logger.exception 留 trace_id 关联
        trace_id = _extract_trace_id(request)
        logger.exception(
            "unhandled exception",
            path=request.url.path,
            trace_id=trace_id,
            error=str(exc),
        )
        body = ErrorResponse(
            ok=False,
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="internal server error",
                retryable=True,
            ),
        )
        headers = {"X-Trace-Id": trace_id} if trace_id else {}
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=body.model_dump(),
            headers=headers,
        )


def _extract_trace_id(request: Request) -> str:
    """从 request.state（middleware 注入）/ X-Request-ID header / OTel context 取 trace_id。

    不依赖 ``X-Trace-Id`` response header（那是给客户端读的，handler 在它之前）。
    """
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id:
        return trace_id
    raw = request.headers.get("x-request-id")
    if raw:
        candidate = raw.strip().lower()
        if len(candidate) == 32 and all(c in "0123456789abcdef" for c in candidate):
            return candidate
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:  # pragma: no cover
        pass
    return ""


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
