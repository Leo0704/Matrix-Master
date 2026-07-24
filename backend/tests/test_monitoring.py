"""监控子系统测试。

覆盖：
- structlog JSON 输出 + 文件滚动
- FastAPI middleware 日志上下文注入（trace_id 串联）
- 告警判定函数（runbook §3 全部条目）

约束：不依赖 matrix.api。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI

from matrix.monitoring import (
    MonitoringMiddleware,
    bind_context,
    clear_context,
    configure_logging,
    get_logger,
)
from matrix.monitoring import alerts as alerts_mod
from matrix.monitoring.logging import SizeTimedRotatingFileHandler


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
# middleware: 请求日志上下文注入（trace_id 串联）
# ---------------------------------------------------------------------------


class TestMiddleware:
    def test_middleware_prefers_x_request_id_header(self, tmp_path):
        """X-Request-ID header（32 hex）作为 trace_id。

        客户端 / 前端可在调用前发此 header，
        让一次调用的日志能串联同一个 trace_id。
        """
        import asyncio

        from httpx import ASGITransport, AsyncClient

        configure_logging(log_dir=tmp_path, level="INFO", console=False)

        sent_trace_id = "deadbeef" * 4  # 32 hex chars (lowercase)

        app = FastAPI()
        app.add_middleware(MonitoringMiddleware)

        @app.get("/probe")
        async def probe():
            log = get_logger(__name__)
            log.info("trace.probe.received", path="/probe")
            return {"ok": True}

        async def run_request() -> dict:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/probe", headers={"X-Request-ID": sent_trace_id}
                )
                return {
                    "header": resp.headers.get("X-Trace-Id", ""),
                    "status": resp.status_code,
                }

        result = asyncio.run(run_request())
        for h in logging.getLogger().handlers:
            h.flush()

        assert result["status"] == 200
        assert result["header"] == sent_trace_id, (
            f"X-Trace-Id 应等于 X-Request-ID header，实际: {result['header']!r}"
        )

        # 同时验证 jsonl 里有该 trace_id
        files = list(tmp_path.glob("*.jsonl"))
        assert files
        content = files[0].read_text(encoding="utf-8").strip()
        found = False
        for line in content.splitlines():
            record = json.loads(line)
            if record.get("trace_id") == sent_trace_id:
                found = True
                assert record["event"] == "trace.probe.received"
                break
        assert found, f"trace_id {sent_trace_id} 不在 jsonl 里"

    def test_middleware_rejects_malformed_x_request_id_header(self):
        """非法 X-Request-ID（不是 32 hex）应该被忽略。"""
        from matrix.monitoring.middleware import _normalize_trace_id

        assert _normalize_trace_id("abcdef") == ""           # 太短
        assert _normalize_trace_id("g" * 32) == ""           # 非 hex
        assert _normalize_trace_id("") == ""                 # 空
        assert _normalize_trace_id(None) == ""               # None
        assert _normalize_trace_id("ABCDEF" * 4 + "AB" + "CD") == ""  # 大写长度不对
        valid = "abcdef0123456789" * 2                        # 32 字符合法小写
        assert _normalize_trace_id(valid) == valid
        assert _normalize_trace_id(valid.upper()) == valid    # 大写小写化
        assert _normalize_trace_id(f"  {valid}  ") == valid   # 空白 strip


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
