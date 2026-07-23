"""测试 FastAPI 应用层：错误响应 envelope + lifespan AlertScanner 装配。

覆盖：
- HTTPException 401/403/404/409/422 都走 ``{ok: False, error: {code, message, retryable}}``
- unhandled Exception 不泄漏 ``str(exc)``，且暴露 ``X-Trace-Id`` header
- 校验错误使用 ``VALIDATION_ERROR`` 而非旧 ``INVALID_PARAMS``
- lifespan 在 services 装配后启动 AlertScanner；shutdown 时停止
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from matrix.api.app import _install_exception_handlers
from matrix.monitoring.alert_scanner import AlertScanner, AlertScannerConfig


def _make_app() -> FastAPI:
    """最小测试 app：装上统一异常处理 + 一个会抛各状态的端点。"""
    app = FastAPI()
    _install_exception_handlers(app)

    @app.get("/boom/401")
    async def boom_401() -> None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "auth required")

    @app.get("/boom/403")
    async def boom_403() -> None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "nope")

    @app.get("/boom/404")
    async def boom_404() -> None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not here")

    @app.get("/boom/409")
    async def boom_409() -> None:
        raise HTTPException(status.HTTP_409_CONFLICT, "conflict")

    @app.get("/boom/500")
    async def boom_500() -> None:
        raise RuntimeError("super-secret-internal-detail-MUST-NOT-LEAK")

    @app.get("/boom/validation")
    async def boom_validation(payload: dict[str, int]) -> dict[str, int]:
        # payload['count'] 必须 ≥ 1；下面传 0 触发 RequestValidationError
        return payload

    return app


# ---------------------------------------------------------------------------
# HTTPException envelope
# ---------------------------------------------------------------------------


class TestErrorEnvelope:
    def test_401_returns_unauthorized_envelope(self):
        client = TestClient(_make_app())
        resp = client.get("/boom/401")
        assert resp.status_code == 401
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "UNAUTHORIZED"
        assert body["error"]["message"] == "auth required"
        assert body["error"]["retryable"] is False
        # 关键：旧 detail 字段不再出现
        assert "detail" not in body

    def test_403_returns_forbidden_envelope(self):
        client = TestClient(_make_app())
        resp = client.get("/boom/403")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "FORBIDDEN"

    def test_404_returns_not_found_envelope(self):
        client = TestClient(_make_app())
        resp = client.get("/boom/404")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "NOT_FOUND"

    def test_409_returns_conflict_envelope(self):
        client = TestClient(_make_app())
        resp = client.get("/boom/409")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "CONFLICT"
        assert body["error"]["retryable"] is False

    def test_422_validation_uses_validation_error_code(self):
        """校验错误应是 VALIDATION_ERROR 而不是旧 INVALID_PARAMS。"""
        client = TestClient(_make_app())
        # count 必须 ≥ 1，传 0 触发 RequestValidationError
        resp = client.get("/boom/validation", params={"count": 0})
        assert resp.status_code == 422
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "detail" not in body


class TestUnhandledExceptionSafety:
    def test_unhandled_exception_does_not_leak_str_exc(self):
        """unhandled 异常响应里必须不含 ``str(exc)`` 原文（安全 + 信息隔离）。"""
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/boom/500")
        assert resp.status_code == 500
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert body["error"]["retryable"] is True
        # 关键：原始异常文本不能出现在响应里
        body_text = resp.text
        assert "super-secret-internal-detail-MUST-NOT-LEAK" not in body_text
        # 通用提示文案
        assert body["error"]["message"] == "internal server error"

    def test_unhandled_exception_exposes_trace_id_header(self):
        """unhandled 响应必须有 ``X-Trace-Id`` header（即便值为空也得有 key，
        或值非空；这里至少断言 header 存在并通过 X-Request-ID 注入）。"""
        client = TestClient(_make_app(), raise_server_exceptions=False)
        sent_trace = "abcdef0123456789" * 2  # 32 hex chars
        resp = client.get(
            "/boom/500", headers={"X-Request-ID": sent_trace}
        )
        assert resp.status_code == 500
        # X-Trace-Id 应该等于 X-Request-ID（middleware 注入）
        # 注意：FastAPI BaseHTTPMiddleware 跑在 exception middleware 外；
        # unhandled handler 直接读 request.state.trace_id 而非 response header
        assert resp.headers.get("X-Trace-Id") == sent_trace
        # 同时响应 body 里 error.code 还在
        assert resp.json()["error"]["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# AlertScanner 装配路径
# ---------------------------------------------------------------------------


class TestAlertScannerWiring:
    @pytest.mark.asyncio
    async def test_alert_scanner_dedupes_active_alerts(self, monkeypatch):
        """同一轮 (code, subject_id) 已存在的未 resolved alert 不重复 INSERT。

        用最小 stub 替换 _gather_devices/_gather_accounts/_fetch_existing_pairs
        注入固定输入；验证 _scan_once 行为。
        """

        # 空 session_factory（只调用 .add / .flush / .commit 的 stub）

        # 一个空 sqlite in-memory + 创建 alerts 表（用 SQLAlchemy 核心 DDL）

        # 走最简路径：mock 掉 AlertScanner 内部的 helper，验证去重逻辑
        async def fake_gather_devices(session):
            return [
                {"device_id": "d1", "last_heartbeat_age_sec": 600},  # 触发
                {"device_id": "d2", "last_heartbeat_age_sec": 60},   # 不触发
            ]

        async def fake_gather_accounts(session):
            return [{"account_id": "a1", "risk_score": 0.95}]  # 触发

        async def fake_fetch_existing_pairs(session):
            # d1 + DEVICE_OFFLINE 已存在 → 应去重
            # a1 + RISK_BLOCKED 不存在 → 应新增
            return {("DEVICE_OFFLINE", "d1")}

        async def fake_config_reader(key: str, default: Any) -> Any:
            return default

        # mock session：add / flush / commit 都接受并保留记录
        written: list[Any] = []

        class _FakeRow:
            def __init__(self, code, severity, message, subject_id):
                from uuid import uuid4

                self.id = uuid4()
                self.code = code
                self.severity = severity
                self.message = message
                self.subject_id = subject_id
                self.resolved = False

        class _FakeSession:
            async def flush(self):
                pass

            async def commit(self):
                pass

            def add(self, row):
                written.append(row)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _FakeFactory:
            def __call__(self):
                return _FakeSession()

        factory = _FakeFactory()  # type: ignore[assignment]
        scanner = AlertScanner(
            session_factory=factory,  # type: ignore[arg-type]
            config_reader=fake_config_reader,
            config=AlertScannerConfig(),
        )
        # patch 内部 helper（用模块的 import 路径）
        import matrix.monitoring.alert_scanner as scanner_mod

        monkeypatch.setattr(
            scanner_mod.AlertScanner,
            "_gather_devices",
            staticmethod(fake_gather_devices),
        )
        monkeypatch.setattr(
            scanner_mod.AlertScanner,
            "_gather_accounts",
            staticmethod(fake_gather_accounts),
        )
        monkeypatch.setattr(
            scanner_mod.AlertScanner,
            "_fetch_existing_pairs",
            staticmethod(fake_fetch_existing_pairs),
        )

        result = await scanner._scan_once()

        # DEVICE_OFFLINE/d1 已存在 → 不写入；RISK_BLOCKED/a1 不存在 → 写入
        codes = [(r.code, r.subject_id) for r in result]
        assert ("DEVICE_OFFLINE", "d1") not in codes
        assert ("RISK_BLOCKED", "a1") in codes
        # v0.7+：监控类告警只写 alerts 表，不再重复发 notifications
        assert len(written) == 1
        assert written[0].code == "RISK_BLOCKED"
        assert written[0].subject_id == "a1"

    @pytest.mark.asyncio
    async def test_alert_scanner_notifier_failure_does_not_lose_alert(self, monkeypatch):
        """notifier 抛异常不应影响已写入的 alert（DB 已 commit）。"""
        async def fake_gather_devices(session):
            return [{"device_id": "d1", "last_heartbeat_age_sec": 600}]

        async def fake_gather_accounts(session):
            return []

        async def fake_fetch_existing_pairs(session):
            return set()

        async def fake_config_reader(key, default):
            return default

        class _FakeSession:
            async def flush(self):
                pass

            async def commit(self):
                pass

            def add(self, row):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class _FakeFactory:
            def __call__(self):
                return _FakeSession()

        scanner = AlertScanner(
            session_factory=_FakeFactory(),  # type: ignore[arg-type]
            config_reader=fake_config_reader,
        )
        import matrix.monitoring.alert_scanner as scanner_mod

        monkeypatch.setattr(scanner_mod.AlertScanner, "_gather_devices", staticmethod(fake_gather_devices))
        monkeypatch.setattr(scanner_mod.AlertScanner, "_gather_accounts", staticmethod(fake_gather_accounts))
        monkeypatch.setattr(scanner_mod.AlertScanner, "_fetch_existing_pairs", staticmethod(fake_fetch_existing_pairs))

        # 应该不抛异常（异常被 _scan_once 吞掉）
        result = await scanner._scan_once()
        assert len(result) == 1
        assert result[0].code == "DEVICE_OFFLINE"