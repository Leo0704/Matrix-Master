"""设备执行适配器：把 Agent 的 DevicePublisher / DeviceCollector / DeviceInteractor 协议接到真实 APK。

- :class:`ApkHttpClient` —— 生产实现，通过 HTTP 调手机端 companion APK 的 REST 接口
  （接口契约见 ``docs/api/apk-http.openapi.yaml``）。APK 地址由 ``device_id`` 经
  Tailscale tailnet IP 解析得到。

v0.6.1：移除原 ``MockDeviceAdapter``（搬到 ``tests/_fake_adapters.py``），遵守
"非测试代码不允许 mock" 原则。
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import httpx

from matrix.agent.protocols import (
    DeviceCollector,
    DeviceInteractor,
    DevicePublisher,
    InteractResult,
    PublishResult,
)
from matrix.device.endpoints import ApkEndpoint
from matrix.device.hmac import compute_signature
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


class ApkHttpClient(DevicePublisher, DeviceCollector, DeviceInteractor):
    """真实 APK HTTP 客户端，实现 DevicePublisher + DeviceCollector + DeviceInteractor。

    生产路径：主控经 Tailscale 连到手机的 APK（``http://<tailnet_ip>:<port>``），
    调用其 ``POST /xhs/publish`` / ``POST /xhs/collect_metrics`` / ``POST /xhs/interact``。
    APK 地址通过 ``resolver(device_id)`` 解析（实现方负责从设备表 / Tailscale 取 tailnet IP）。
    """

    def __init__(
        self,
        *,
        resolver: Callable[[UUID], Awaitable[ApkEndpoint]] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._resolver = resolver
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._timeout = timeout

    async def __aenter__(self) -> ApkHttpClient:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    async def _endpoint(self, device_id: UUID) -> ApkEndpoint:
        if self._resolver is None:
            raise RuntimeError(
                f"ApkHttpClient 未配置 resolver，无法定位 device={device_id} 的 APK"
            )
        return await self._resolver(device_id)

    @staticmethod
    def _body(payload: dict) -> bytes:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _headers(endpoint: ApkEndpoint, request_id: str, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        return {
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Request-Id": request_id,
            "X-Signature": compute_signature(endpoint.hmac_key, timestamp, request_id, body),
        }

    async def _post(
        self,
        endpoint: ApkEndpoint,
        path: str,
        payload: dict,
        request_id: str,
        timeout: float,
    ) -> dict:
        body = self._body(payload)
        response = await self._client.post(
            f"{endpoint.base_url}{path}",
            content=body,
            headers=self._headers(endpoint, request_id, body),
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise RuntimeError("APK returned an invalid success response")
        return data

    async def publish(
        self,
        *,
        device_id: UUID,
        account_id: UUID,
        title: str,
        content: str,
        images: list[str],
        tags: list[str],
        request_id: str,
        timeout: float = 120.0,
    ) -> PublishResult:
        ep = await self._endpoint(device_id)
        try:
            response = await self._post(
                ep,
                "/xhs/publish",
                {
                    "account_id": str(account_id),
                    "title": title,
                    "content": content,
                    "images": images,
                    "tags": tags,
                    "request_id": request_id,
                },
                request_id,
                min(timeout, self._timeout) or self._timeout,
            )
            data = response.get("data")
            if not isinstance(data, dict) or not data.get("platform_note_id"):
                raise RuntimeError("APK publish response is missing platform_note_id")
            return PublishResult(
                ok=True,
                note_id=uuid4(),
                platform_note_id=str(data.get("platform_note_id") or ""),
                platform_url=data.get("url"),
            )
        except httpx.TimeoutException as exc:
            return PublishResult(ok=False, note_id=uuid4(), error_code="TIMEOUT", error_message=str(exc))
        except httpx.HTTPStatusError as exc:
            return PublishResult(
                ok=False,
                note_id=uuid4(),
                error_code=f"HTTP_{exc.response.status_code}",
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("apk.publish.failed", device_id=device_id)
            return PublishResult(ok=False, note_id=uuid4(), error_code="APK_ERROR", error_message=str(exc))

    async def collect(
        self,
        *,
        device_id: UUID,
        account_id: UUID,
        platform_note_id: str,
        scope: str = "recent_24h",
    ) -> dict[str, int]:
        ep = await self._endpoint(device_id)
        request_id = str(uuid4())
        response = await self._post(
            ep,
            "/xhs/collect_metrics",
            {
                "account_id": str(account_id),
                "platform_note_id": platform_note_id,
                "scope": scope,
                "request_id": request_id,
            },
            request_id,
            self._timeout,
        )
        rows = response.get("data")
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("APK collect response has no metrics")
        data = next(
            (row for row in rows if isinstance(row, dict) and row.get("note_id") == platform_note_id),
            rows[0],
        )
        if not isinstance(data, dict):
            raise RuntimeError("APK collect response has invalid metrics")
        # APK 可能对未采集字段返回 null（见 NoteMetric.views），不假装成 0；
        # 直接丢弃 None 键，下游 collect_node 会跳过，ANALYZE 节点就能识别"未采集"。
        return {
            k: int(v)
            for k, v in (
                (k, data.get(k)) for k in ("views", "likes", "collects", "comments", "follows_gained")
            )
            if v is not None
        }

    async def interact(
        self,
        *,
        device_id: UUID,
        account_id: UUID,
        action: str,
        target_note_id: str,
        content: str | None = None,
        request_id: str,
        timeout: float = 60.0,
    ) -> InteractResult:
        """调 APK ``POST /xhs/interact``（v0.6 MVP：action ∈ {like, comment}）。

        协议：见 ``docs/api/apk-http.openapi.yaml`` 的 ``/xhs/interact`` 节。
        """
        ep = await self._endpoint(device_id)
        try:
            await self._post(
                ep,
                "/xhs/interact",
                {
                    "account_id": str(account_id),
                    "action": action,
                    "target": {"note_id": target_note_id},
                    "content": content,
                    "request_id": request_id,
                },
                request_id,
                min(timeout, self._timeout) or self._timeout,
            )
            return InteractResult(
                ok=True,
                interaction_id=uuid4(),
                error_code=None,
                error_message=None,
            )
        except httpx.TimeoutException as exc:
            return InteractResult(
                ok=False, interaction_id=uuid4(),
                error_code="TIMEOUT", error_message=str(exc),
            )
        except httpx.HTTPStatusError as exc:
            return InteractResult(
                ok=False, interaction_id=uuid4(),
                error_code=f"HTTP_{exc.response.status_code}",
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("apk.interact.failed", device_id=device_id)
            return InteractResult(
                ok=False, interaction_id=uuid4(),
                error_code="APK_ERROR", error_message=str(exc),
            )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["ApkEndpoint", "ApkHttpClient"]
