"""OpenTelemetry tracing 初始化与常用 span helper。

约定 span 命名（与 docs/architecture/SDD.md §3.6.3 一致）：

- ``agent.run.start`` / ``agent.run.end``
- ``agent.state.{from}->{to}``
- ``task.dispatch``
- ``device.call.{tool_name}``
- ``llm.call.{model}``

导出器：
- ``otlp_endpoint`` 非空 → 走 OTel Collector（gRPC 4317 或 HTTP 4318）
- ``otlp_endpoint`` 为空 / ``None`` → 用 ConsoleSpanExporter 兜底
- 测试可通过 ``tracing._reset_for_test()`` + ``tracer_provider`` 注入 mock exporter
"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
import threading
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = get_logger(__name__)

_LOCK = threading.Lock()
_PROVIDER: TracerProvider | None = None
_TRACER: trace.Tracer | None = None
_EXPORTER: SpanExporter | None = None


def setup_tracing(service_name: str, otlp_endpoint: str | None = None) -> TracerProvider:
    """初始化全局 TracerProvider。重复调用安全。"""
    global _PROVIDER, _TRACER, _EXPORTER

    with _LOCK:
        if _PROVIDER is not None:
            return _PROVIDER

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        exporter = _build_exporter(otlp_endpoint)
        if isinstance(exporter, ConsoleSpanExporter) or otlp_endpoint is None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        else:
            provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        _PROVIDER = provider
        _EXPORTER = exporter
        _TRACER = provider.get_tracer(service_name)
        return provider


def _build_exporter(otlp_endpoint: str | None) -> SpanExporter:
    if not otlp_endpoint:
        logger.info("OTLP endpoint not set, falling back to ConsoleSpanExporter")
        return ConsoleSpanExporter()

    try:
        if otlp_endpoint.endswith(":4318") or "/v1/traces" in otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HTTPSpanExporter,
            )

            logger.info("Using OTLP/HTTP exporter", endpoint=otlp_endpoint)
            return HTTPSpanExporter(endpoint=otlp_endpoint)
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GRPCSpanExporter,
        )

        logger.info("Using OTLP/gRPC exporter", endpoint=otlp_endpoint)
        return GRPCSpanExporter(endpoint=otlp_endpoint, insecure=True)
    except Exception as e:  # pragma: no cover - 防御性兜底
        logger.warning(
            "Failed to init OTLP exporter; using ConsoleSpanExporter", error=e
        )
        return ConsoleSpanExporter()


def get_tracer() -> trace.Tracer:
    global _TRACER
    if _TRACER is None:
        with _LOCK:
            if _TRACER is None:
                _TRACER = trace.get_tracer("matrix.monitoring")
    return _TRACER


def _set_exporter(exporter: SpanExporter, provider: TracerProvider | None = None) -> None:
    """测试钩子：替换全局 exporter / provider。"""
    global _EXPORTER, _PROVIDER, _TRACER
    _EXPORTER = exporter
    if provider is not None:
        _PROVIDER = provider
        _TRACER = provider.get_tracer("matrix.monitoring")


def _reset_for_test() -> None:
    """测试钩子：清空全局状态。"""
    global _PROVIDER, _TRACER, _EXPORTER
    with _LOCK:
        if _PROVIDER is not None:
            try:
                _PROVIDER.shutdown()
            except Exception:  # pragma: no cover
                pass
        _PROVIDER = None
        _TRACER = None
        _EXPORTER = None
    trace.set_tracer_provider(trace.NoOpTracerProvider())


def shutdown_tracing() -> None:
    global _PROVIDER
    with _LOCK:
        if _PROVIDER is not None:
            try:
                _PROVIDER.force_flush()
                _PROVIDER.shutdown()
            except Exception:  # pragma: no cover
                pass
            _PROVIDER = None
            _TRACER = None
            _EXPORTER = None


# ---------------------------------------------------------------------------
# Span helpers - 每个 helper 自带 try/except + status 设置
# ---------------------------------------------------------------------------


@contextmanager
def trace_agent_run(run_id: str, goal: str | None = None) -> Iterator[Span]:
    """包裹一次 Agent run。span 名 ``agent.run``。"""
    tracer = get_tracer()
    with tracer.start_as_current_span("agent.run", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("run_id", run_id)
        if goal is not None:
            span.set_attribute("goal", goal)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))


@contextmanager
def trace_state_transition(from_state: str, to_state: str, run_id: str) -> Iterator[Span]:
    """包裹一次状态机转移。span 名 ``agent.state.{from}->{to}``。"""
    tracer = get_tracer()
    name = f"agent.state.{from_state}->{to_state}"
    with tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
        span.set_attribute("from_state", from_state)
        span.set_attribute("to_state", to_state)
        span.set_attribute("run_id", run_id)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))


@contextmanager
def trace_task_dispatch(task_id: str, device_id: str, action: str) -> Iterator[Span]:
    """包裹一次任务下发。span 名 ``task.dispatch``。"""
    tracer = get_tracer()
    with tracer.start_as_current_span("task.dispatch", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("task_id", task_id)
        span.set_attribute("device_id", device_id)
        span.set_attribute("action", action)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))


@contextmanager
def trace_device_call(tool_name: str, device_id: str | None = None) -> Iterator[Span]:
    """包裹一次 APK device tool 调用。span 名 ``device.call.{tool_name}``。"""
    tracer = get_tracer()
    name = f"device.call.{tool_name}"
    with tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
        span.set_attribute("tool_name", tool_name)
        if device_id is not None:
            span.set_attribute("device_id", device_id)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))


@contextmanager
def trace_llm_call(model: str, call_type: str = "completion") -> Iterator[Span]:
    """包裹一次 LLM 调用。span 名 ``llm.call.{model}``。"""
    tracer = get_tracer()
    name = f"llm.call.{model}"
    with tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
        span.set_attribute("model", model)
        span.set_attribute("call_type", call_type)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        else:
            span.set_status(Status(StatusCode.OK))
