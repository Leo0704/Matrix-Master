"""独立的 ``/metrics`` 端点（Prometheus 拉取用）。

约束（来自任务）：
- 物理隔离：跑在 port 9091，主 API 在 8666，互不干扰
- 不依赖 ``matrix.api``，使用独立 FastAPI app
- 通过 ``start_metrics_server()`` 启动；可被测试或独立进程使用

用法::

    from matrix.monitoring.metrics_endpoint import start_metrics_server
    start_metrics_server(port=9091)
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

DEFAULT_METRICS_PORT = 9091


def create_metrics_app() -> FastAPI:
    """构造只暴露 ``/metrics`` 的独立 FastAPI app。"""
    app = FastAPI(title="Matrix Metrics", docs_url=None, redoc_url=None)

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok"}

    return app


def start_metrics_server(
    host: str = "127.0.0.1",
    port: int = DEFAULT_METRICS_PORT,
    log_level: str = "warning",
) -> None:
    """以阻塞方式启动 metrics server（独立进程 / 测试 fixture 用）。

    默认 ``127.0.0.1`` 监听，避免对外暴露。如需容器内被 scrape 切到 ``0.0.0.0``。
    """
    import uvicorn

    app = create_metrics_app()
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def start_metrics_server_background(
    host: str = "127.0.0.1",
    port: int = DEFAULT_METRICS_PORT,
) -> None:
    """以守护线程方式启动 metrics server（方便在主进程中并跑）。"""
    import threading

    import uvicorn

    app = create_metrics_app()
    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)

    def _run() -> None:
        server.run()

    thread = threading.Thread(target=_run, name="matrix-metrics", daemon=True)
    thread.start()
