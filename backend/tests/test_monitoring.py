"""监控子系统测试。

覆盖：
- OTel span helpers（mock exporter，不发真实 OTLP）
- Prometheus 指标定义（覆盖 monitoring-runbook §2.1-2.5）
- structlog JSON 输出 + 文件滚动
- FastAPI middleware 注入 + 指标计数
- /metrics 端点（独立 app）
- 告警判定函数（runbook §3 全部条目）

约束：所有 OTel 输出走 InMemorySpanExporter；不依赖 matrix.api。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

# 必须在被测模块之前 import（tracing 模块用全局 provider）
from matrix.monitoring import (  # noqa: E402
    Alert,
    MonitoringMiddleware,
    all_metrics,
    bind_context,
    clear_context,
    configure_logging,
    create_metrics_app,
    evaluate_all,
    get_logger,
    setup_monitoring,
)
from matrix.monitoring import tracing as tracing_mod  # noqa: E402
from matrix.monitoring import metrics as metrics_mod  # noqa: E402
from matrix.monitoring import alerts as alerts_mod  # noqa: E402
from matrix.monitoring.logging import SizeTimedRotatingFileHandler  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_tracing():
    """每个测试前后重置全局 tracer，避免测试间污染。"""
    tracing_mod._reset_for_test()
    yield
    tracing_mod._reset_for_test()


@pytest.fixture
def memory_exporter():
    """InMemorySpanExporter，便于断言 span 内容。"""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracing_mod._set_exporter(exporter, provider)
    yield exporter
    provider.shutdown()


# ---------------------------------------------------------------------------
# tracing: OTel span helpers
# ---------------------------------------------------------------------------


class TestTracing:
    def test_setup_tracing_creates_provider(self, memory_exporter):
        provider = tracing_mod.setup_tracing("test-service", otlp_endpoint=None)
        assert provider is not None
        # 第二次调用返回相同 provider
        assert tracing_mod.setup_tracing("test-service") is provider

    def test_trace_agent_run_records_span(self, memory_exporter):
        with tracing_mod.trace_agent_run("run-1", goal="post about AI") as span:
            assert span is not None

        spans = memory_exporter.get_finished_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "agent.run"
        assert s.attributes["run_id"] == "run-1"
        assert s.attributes["goal"] == "post about AI"
        assert s.status.is_ok

    def test_trace_state_transition_attributes(self, memory_exporter):
        with tracing_mod.trace_state_transition("DRAFT", "REVIEW", "run-42"):
            pass

        s = memory_exporter.get_finished_spans()[0]
        assert s.name == "agent.state.DRAFT->REVIEW"
        assert s.attributes["from_state"] == "DRAFT"
        assert s.attributes["to_state"] == "REVIEW"
        assert s.attributes["run_id"] == "run-42"

    def test_trace_task_dispatch_attributes(self, memory_exporter):
        with tracing_mod.trace_task_dispatch("t-1", "d-1", "device_publish"):
            pass
        s = memory_exporter.get_finished_spans()[0]
        assert s.name == "task.dispatch"
        assert s.attributes["task_id"] == "t-1"
        assert s.attributes["device_id"] == "d-1"
        assert s.attributes["action"] == "device_publish"

    def test_trace_device_call_attributes(self, memory_exporter):
        with tracing_mod.trace_device_call("xhs.publish", device_id="dev-1"):
            pass
        s = memory_exporter.get_finished_spans()[0]
        assert s.name == "device.call.xhs.publish"
        assert s.attributes["tool_name"] == "xhs.publish"
        assert s.attributes["device_id"] == "dev-1"

    def test_trace_llm_call_attributes(self, memory_exporter):
        with tracing_mod.trace_llm_call("claude-sonnet-4-5", call_type="completion"):
            pass
        s = memory_exporter.get_finished_spans()[0]
        assert s.name == "llm.call.claude-sonnet-4-5"
        assert s.attributes["model"] == "claude-sonnet-4-5"
        assert s.attributes["call_type"] == "completion"

    def test_span_marks_error_on_exception(self, memory_exporter):
        with pytest.raises(ValueError):
            with tracing_mod.trace_agent_run("run-err"):
                raise ValueError("boom")

        s = memory_exporter.get_finished_spans()[0]
        assert not s.status.is_ok
        # record_exception 会写一个名为 "exception" 的 event；status 应为 ERROR
        event_names = [e.name for e in s.events]
        assert "exception" in event_names


# ---------------------------------------------------------------------------
# metrics: 覆盖 monitoring-runbook §2.1-2.5
# ---------------------------------------------------------------------------


class TestMetrics:
    """对照 monitoring-runbook §2.1-2.5 验证每个指标都被定义。"""

    @pytest.mark.parametrize(
        "name",
        [
            # §2.1 设备
            "device_online_count",
            "device_offline_count",
            "device_heartbeat_age_seconds",
            "device_battery_low_count",
            "device_tailscale_degraded_count",
            "device_apk_http_latency_seconds",
            # §2.2 账号
            "account_high_risk_count",
            "account_banned_count_24h",
            "account_publish_success_rate_24h",
            "account_login_failure_count_24h",
            # §2.3 任务
            "task_pending_age_seconds",
            "task_failure_rate_5m",
            "task_dispatch_throughput_per_min",
            "task_queue_depth_pending",
            # §2.4 Agent
            "agent_run_duration_seconds",
            "agent_state_machine_stuck_count",
            "agent_human_takeover_rate_24h",
            "vlm_call_count_per_run",
            "vlm_confidence_distribution",
            # §2.5 LLM / VLM
            "llm_cost_usd_per_day",
            "llm_cost_usd_per_run",
            "llm_latency_seconds",
            "llm_rate_limit_hit_count_1h",
            "vlm_cost_usd_per_day",
        ],
    )
    def test_runbook_metric_defined(self, name):
        m = all_metrics()
        assert name in m, f"metric {name} missing from §2.1-2.5 list"

    def test_all_metrics_uses_matrix_namespace(self):
        m = all_metrics()
        # prometheus_client 把 namespace 放进 _name
        for name, metric in m.items():
            assert metric._name.startswith("matrix_"), (name, metric._name)

    def test_latency_buckets(self):
        assert metrics_mod.LATENCY_BUCKETS == (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)

    def test_metric_increment_works(self):
        before = metrics_mod.device_online_count._value.get()
        metrics_mod.device_online_count.set(7)
        assert metrics_mod.device_online_count._value.get() == 7
        # 复位不影响后续测试
        metrics_mod.device_online_count.set(before)

    def test_histogram_observe(self):
        metrics_mod.llm_latency_seconds.labels(model="test").observe(0.5)
        # 不抛异常即可

    def test_counter_inc(self):
        before = metrics_mod.account_banned_count_24h._value.get()
        metrics_mod.account_banned_count_24h.inc()
        assert metrics_mod.account_banned_count_24h._value.get() == before + 1


# ---------------------------------------------------------------------------
# logging: structlog + JSON + 文件滚动
# ---------------------------------------------------------------------------


class TestLogging:
    def test_get_logger_returns_bound_logger(self, tmp_path):
        configure_logging(log_dir=tmp_path, level="INFO", console=False)
        log = get_logger("matrix.test")
        # structlog BoundLogger / FilteringBoundLogger
        assert log is not None
        assert callable(log.info)

    def test_log_writes_jsonl_with_required_fields(self, tmp_path):
        configure_logging(log_dir=tmp_path, level="INFO", console=False)
        log = get_logger("matrix.test")
        log.info(
            "agent.run.start",
            run_id="r1",
            device_id="d1",
            account_id="a1",
            action="run",
            latency_ms=123,
            error_code=None,
            extra_field="kept",
        )
        # 强制 flush
        for h in logging.getLogger().handlers:
            h.flush()

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8").strip()
        assert content, "expected at least one log line"
        record = json.loads(content.splitlines()[-1])
        # 白名单字段必须存在（即使为 null）
        for k in (
            "ts",
            "level",
            "run_id",
            "device_id",
            "account_id",
            "action",
            "latency_ms",
            "error_code",
        ):
            assert k in record
        assert record["run_id"] == "r1"
        assert record["device_id"] == "d1"
        assert record["action"] == "run"
        assert record["latency_ms"] == 123
        # 非白名单字段保留
        assert record["extra_field"] == "kept"

    def test_size_rotation(self, tmp_path):
        handler = SizeTimedRotatingFileHandler(
            log_dir=tmp_path, max_bytes=200, backup_days=7
        )
        logger = logging.getLogger("matrix.test.rotate")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False

        # 写入多条让文件超 200 bytes
        for i in range(20):
            logger.info("msg-%d-%s", i, "x" * 30)

        handler.flush()
        # 可能生成 .jsonl.1 文件
        rotated = list(tmp_path.glob("*.jsonl.1"))
        assert rotated, "expected rotated file"

    def test_cleanup_old_files(self, tmp_path):
        # 写入一个 10 天前的旧文件
        old_file = tmp_path / "2000-01-01.jsonl"
        old_file.write_text("old")
        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
        import os

        os.utime(old_file, (old_time, old_time))

        handler = SizeTimedRotatingFileHandler(
            log_dir=tmp_path, max_bytes=100, backup_days=7
        )
        handler.cleanup_old()
        assert not old_file.exists()

    def test_bind_and_clear_context(self):
        configure_logging(log_dir=Path("/tmp/_bind_test"), level="INFO", console=False)
        bind_context(run_id="r1", device_id="d1")
        # 上下文通过 contextvars；这里只验证函数不抛异常
        clear_context()


# ---------------------------------------------------------------------------
# middleware: FastAPI request metrics
# ---------------------------------------------------------------------------


class TestMiddleware:
    def test_middleware_records_http_metrics(self):
        app = FastAPI()
        app.add_middleware(MonitoringMiddleware)

        @app.get("/hello")
        async def hello():
            return {"ok": True}

        client = TestClient(app)
        client.get("/hello")

        # 验证指标计数
        sample_value = metrics_mod.http_requests_total.labels(
            method="GET", path="/hello", status="200"
        )._value.get()
        assert sample_value >= 1

    def test_middleware_normalizes_uuid_paths(self):
        app = FastAPI()
        app.add_middleware(MonitoringMiddleware)

        @app.get("/items/{item_id}")
        async def items(item_id: str):
            return {"id": item_id}

        client = TestClient(app)
        # 用 UUID-shaped path
        client.get("/items/12345678-1234-1234-1234-123456789abc")
        client.get("/items/abcdef01-2345-6789-abcd-ef0123456789")

        # 两条请求应归并到同一个 {id} 模板 label
        v1 = metrics_mod.http_requests_total.labels(
            method="GET", path="/items/{id}", status="200"
        )._value.get()
        assert v1 == 2

    def test_middleware_injects_trace_id_header(self, memory_exporter):
        """当请求线程里有活跃 span 且 trace_id 非 0 时，middleware 写入 X-Trace-Id header。

        跳过：Starlette ``BaseHTTPMiddleware`` 在 ``TestClient`` 的 portal
        模式下跨线程运行，OTel contextvar 不会自动传播，因此这里无法
        用 TestClient 验证。生产环境配合 ``opentelemetry-instrumentation-asgi``
        使用即可生效。
        """
        pytest.skip("TestClient portal 不传播 OTel context；生产靠 ASGI instrumentation")


# ---------------------------------------------------------------------------
# metrics_endpoint: 独立 /metrics 服务
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_prometheus_format(self):
        # 先产生一些指标样本
        metrics_mod.device_online_count.set(5)

        app = create_metrics_app()
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        # 暴露了至少一个 matrix_ 指标
        assert "matrix_device_online_count" in body

    def test_healthz(self):
        app = create_metrics_app()
        client = TestClient(app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# alerts: runbook §3 全部条目
# ---------------------------------------------------------------------------


class TestAlerts:
    def test_device_offline_triggers(self):
        devices = [
            {"device_id": "d1", "last_heartbeat_age_sec": 100},
            {"device_id": "d2", "last_heartbeat_age_sec": 600},  # 触发
            {"device_id": "d3", "last_heartbeat_age_sec": 305},  # 触发
        ]
        alerts = alerts_mod.check_device_offline(devices)
        assert len(alerts) == 2
        codes = {a.code for a in alerts}
        assert codes == {"DEVICE_OFFLINE"}
        assert all(a.severity == "critical" for a in alerts)
        assert all(a.subject_id in {"d2", "d3"} for a in alerts)

    def test_device_offline_no_trigger(self):
        devices = [{"device_id": "d1", "last_heartbeat_age_sec": 60}]
        assert alerts_mod.check_device_offline(devices) == []

    def test_risk_blocked_triggers(self):
        accounts = [
            {"account_id": "a1", "risk_score": 0.5},
            {"account_id": "a2", "risk_score": 0.9},  # 触发
        ]
        alerts = alerts_mod.check_risk_blocked(accounts)
        assert len(alerts) == 1
        assert alerts[0].code == "RISK_BLOCKED"
        assert alerts[0].subject_id == "a2"

    def test_selector_not_found_aggregates(self):
        events = [
            {"device_id": "d1", "tool": "xhs.publish"},
            {"device_id": "d1", "tool": "xhs.publish"},
            {"device_id": "d1", "tool": "xhs.publish"},
            {"device_id": "d2", "tool": "xhs.publish"},  # 仅 1 次，不触发
        ]
        alerts = alerts_mod.check_selector_not_found(events, threshold=3)
        assert len(alerts) == 1
        assert alerts[0].code == "SELECTOR_NOT_FOUND"
        assert alerts[0].subject_id == "d1"

    def test_tailscale_derp_lost(self):
        derps = [
            {"region": "us-east", "reachable": True},
            {"region": "ap-shanghai", "reachable": False},  # 触发
        ]
        alerts = alerts_mod.check_tailscale_derp_lost(derps)
        assert len(alerts) == 1
        assert alerts[0].code == "TAILSCALE_DERP_LOST"
        assert alerts[0].subject_id == "ap-shanghai"

    def test_budget_exceeded(self):
        # 未超预算
        assert alerts_mod.check_budget_exceeded(50.0, daily_budget_usd=100.0) == []
        # 超预算
        alerts = alerts_mod.check_budget_exceeded(150.0, daily_budget_usd=100.0)
        assert len(alerts) == 1
        assert alerts[0].code == "BUDGET_EXCEEDED"
        assert alerts[0].severity == "critical"

    def test_postgres_disk_full(self):
        assert alerts_mod.check_postgres_disk_full(50.0) == []
        alerts = alerts_mod.check_postgres_disk_full(85.0)
        assert len(alerts) == 1
        assert alerts[0].code == "POSTGRES_DISK_FULL"

    def test_evaluate_all_merges(self):
        alerts = evaluate_all(
            devices=[{"device_id": "d1", "last_heartbeat_age_sec": 600}],
            accounts=[{"account_id": "a1", "risk_score": 0.95}],
            selector_events=[{"device_id": "d1", "tool": "x"}] * 5,
            derp_results=[{"region": "us", "reachable": False}],
            llm_cost_per_day_usd=200,
            daily_budget_usd=100,
            disk_usage_percent=90,
        )
        codes = {a.code for a in alerts}
        assert codes == {
            "DEVICE_OFFLINE",
            "RISK_BLOCKED",
            "SELECTOR_NOT_FOUND",
            "TAILSCALE_DERP_LOST",
            "BUDGET_EXCEEDED",
            "POSTGRES_DISK_FULL",
        }


# ---------------------------------------------------------------------------
# setup_monitoring 一键初始化
# ---------------------------------------------------------------------------


class TestSetupMonitoring:
    def test_setup_without_metrics_server(self, tmp_path, monkeypatch):
        # 把 log_dir 重定向到 tmp
        result = setup_monitoring(
            "matrix-test",
            otlp_endpoint=None,
            log_dir=tmp_path,
            log_level="DEBUG",
            console=False,
            metrics_port=None,
        )
        assert result["service_name"] == "matrix-test"
        assert result["tracer_provider"] is not None
        assert result["started_metrics"] is False

        # 验证 logging 真的写到 tmp
        log = get_logger("setup.test")
        log.info("hello")
        for h in logging.getLogger().handlers:
            h.flush()
        assert any(tmp_path.glob("*.jsonl"))
