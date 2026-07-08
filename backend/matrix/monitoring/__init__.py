"""监控子系统：OTel tracing + Prometheus metrics + structlog logging。

一键初始化：``setup_monitoring(service_name, otlp_endpoint)``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from matrix.monitoring.alerts import Alert, evaluate_all
from matrix.monitoring.logging import (
    bind_context,
    clear_context,
    configure_logging,
    get_logger,
)
from matrix.monitoring.metrics import (
    LATENCY_BUCKETS,
    all_metrics,
)
from matrix.monitoring.metrics_endpoint import (
    DEFAULT_METRICS_PORT,
    create_metrics_app,
    start_metrics_server_background,
)
from matrix.monitoring.middleware import MonitoringMiddleware, install_middleware
from matrix.monitoring.tracing import (
    get_tracer,
    setup_tracing,
    shutdown_tracing,
    trace_agent_run,
    trace_device_call,
    trace_llm_call,
    trace_state_transition,
    trace_task_dispatch,
)


def setup_monitoring(
    service_name: str,
    otlp_endpoint: str | None = None,
    *,
    log_dir: Path | None = None,
    log_level: str = "INFO",
    console: bool = True,
    metrics_port: int | None = None,
) -> dict[str, Any]:
    """一键初始化 tracing + logging +（可选）metrics HTTP server。

    Args:
        service_name: 服务名，会作为 OTel ``service.name``。
        otlp_endpoint: OTel Collector 地址（如 ``http://localhost:4317``）；
            传 ``None`` 时退化为 ConsoleSpanExporter（开发/测试）。
        log_dir: 日志目录；默认 ``~/.matrix/logs``。
        log_level: 根日志级别。
        console: 是否同时输出到 stdout。
        metrics_port: 非 None 时在后台线程启动 metrics HTTP server
            （默认端口见 ``DEFAULT_METRICS_PORT``，与主 API 物理隔离）。

    Returns:
        dict 含初始化结果（``tracer_provider`` / ``started_metrics``），便于测试断言。
    """
    provider = setup_tracing(service_name, otlp_endpoint)
    configure_logging(log_dir=log_dir, level=log_level, console=console)

    started_metrics = False
    if metrics_port is not None:
        start_metrics_server_background(port=metrics_port)
        started_metrics = True

    return {
        "service_name": service_name,
        "tracer_provider": provider,
        "started_metrics": started_metrics,
        "metrics_port": metrics_port,
    }


__all__ = [
    "Alert",
    "LATENCY_BUCKETS",
    "MonitoringMiddleware",
    "DEFAULT_METRICS_PORT",
    "all_metrics",
    "bind_context",
    "clear_context",
    "configure_logging",
    "create_metrics_app",
    "evaluate_all",
    "get_logger",
    "get_tracer",
    "install_middleware",
    "setup_monitoring",
    "setup_tracing",
    "shutdown_tracing",
    "start_metrics_server_background",
    "trace_agent_run",
    "trace_device_call",
    "trace_llm_call",
    "trace_state_transition",
    "trace_task_dispatch",
]
