"""测试 /api/v1/logs ingest endpoint。

APK 日志经此端点进入 master，统一写进 ~/.matrix/logs/*.jsonl。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from matrix.api.routes.logs import router as logs_router
from matrix.monitoring.logging import configure_logging


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(logs_router, prefix="/api/v1")
    return app


def test_logs_route_accepts_batch(tmp_path: Path):
    """APK 上行一批日志，202 accepted，逐条写入 jsonl。"""
    configure_logging(log_dir=tmp_path, level="INFO", console=False)

    client = TestClient(_make_app())
    resp = client.post(
        "/api/v1/logs",
        json={
            "device_id": "device-abc",
            "app_version": "0.6.1",
            "lines": [
                {
                    "level": "info",
                    "event": "apk.boot",
                    "message": "App initialized",
                    "attrs": {"uptime_sec": 12},
                },
                {
                    "level": "error",
                    "event": "apk.pair.failed",
                    "message": "code mismatch",
                    "attrs": {"device_id": "device-abc"},
                    "throwable": "IllegalArgumentException: bad code",
                },
            ],
        },
    )
    assert resp.status_code == 202
    assert resp.json() == {"received": 2}

    for h in logging.getLogger().handlers:
        h.flush()

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8").strip()
    records_by_event: dict[str, dict] = {}
    for line in content.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # uvicorn access log 等非 JSON 行混入，跳过
            continue
        ev = rec.get("event")
        # 只关心我们喂的 events；其他（如 uvicorn access log）忽略
        if ev in ("apk.boot", "apk.pair.failed"):
            records_by_event[ev] = rec
    assert set(records_by_event) == {"apk.boot", "apk.pair.failed"}

    r0 = records_by_event["apk.boot"]
    assert r0["level"] == "info"
    assert r0["message"] == "App initialized"
    assert r0["uptime_sec"] == 12  # structlog 把 kwargs 展开到顶层
    assert r0["source"] == "matrix-apk"
    r1 = records_by_event["apk.pair.failed"]
    assert r1["level"] == "error"
    assert r1["throwable"].startswith("IllegalArgumentException")


def test_logs_route_empty_batch(tmp_path: Path):
    """空批量不报错，返回 received=0。"""
    configure_logging(log_dir=tmp_path, level="INFO", console=False)
    client = TestClient(_make_app())
    resp = client.post("/api/v1/logs", json={"lines": []})
    assert resp.status_code == 202
    assert resp.json() == {"received": 0}


def test_logs_route_truncates_oversize_batch(tmp_path: Path):
    """超过 _MAX_BATCH 的批量被截断，但返回 202 + 截断后实际接收数。"""
    configure_logging(log_dir=tmp_path, level="INFO", console=False)
    client = TestClient(_make_app())

    oversized = {
        "lines": [
            {"level": "info", "event": "test.line", "message": f"line {i}"}
            for i in range(500)
        ]
    }
    resp = client.post("/api/v1/logs", json=oversized)
    assert resp.status_code == 202
    assert resp.json()["received"] == 200  # _MAX_BATCH = 200


def test_logs_route_mounted_without_console_auth(tmp_path: Path, monkeypatch):
    """logs 路由按设计意图不挂控制台 Bearer 鉴权（APK 无 token，
    靠 Tailscale 网络层隔离）——真实 app 里不带 Authorization 也得 202。

    显式设 MATRIX_API_SECRET 让控制台鉴权真正生效，否则鉴权是放行态、
    测不出 logs 是否挂在鉴权组里；用 GET /devices 401 做对照。
    """
    configure_logging(log_dir=tmp_path, level="INFO", console=False)
    from matrix.api.app import create_app

    monkeypatch.setenv("MATRIX_API_SECRET", "test-secret")
    app = create_app(
        database_url="sqlite+aiosqlite:///:memory:",
        enable_monitoring_middleware=False,
    )
    # 不用 `with TestClient(...)`：context manager 会跑 lifespan startup，
    # ensure_api_secret 会把生成的 secret 写进进程级 os.environ，
    # 污染后面所有 create_app 集成测试（notifications 等会集体 401）。
    client = TestClient(app)
    # 对照：控制台路由无 token 必须 401（证明鉴权确实生效中）
    assert client.get("/api/v1/devices").status_code == 401
    resp = client.post("/api/v1/logs", json={"lines": []})
    assert resp.status_code == 202
