"""设备执行适配器：把 Agent 的 DevicePublisher / DeviceCollector 协议接到真实 APK 或开发期 Mock。

- :class:`ApkHttpClient` —— 生产实现，通过 HTTP 调手机端 companion APK 的 REST 接口
  （接口契约见 ``docs/api/apk-http.openapi.yaml``）。APK 地址由 ``device_id`` 经
  Tailscale tailnet IP 解析得到。
- :class:`MockDeviceAdapter` —— 开发 / 测试用，纯内存模拟，无需真实手机即可跑通整条闭环。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import httpx

from matrix.agent.protocols import (
    DeviceCollector,
    DevicePublisher,
    PublishResult,
)

logger = logging.getLogger(__name__)


@dataclass
class ApkEndpoint:
    """某设备在 tailnet 上的 APK 访问信息。"""

    base_url: str
    hmac_key: str | None = None


class ApkHttpClient(DevicePublisher, DeviceCollector):
    """真实 APK HTTP 客户端，实现 DevicePublisher + DeviceCollector。

    生产路径：主控经 Tailscale 连到手机的 APK（``http://<tailnet_ip>:<port>``），
    调用其 ``POST /xhs/publish`` 与 ``POST /xhs/collect_metrics``。APK 地址通过
    ``resolver(device_id)`` 解析（实现方负责从设备表 / Tailscale 取 tailnet IP）。
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

    async def _endpoint(self, device_id: UUID) -> ApkEndpoint:
        if self._resolver is None:
            raise RuntimeError(
                f"ApkHttpClient 未配置 resolver，无法定位 device={device_id} 的 APK"
            )
        return await self._resolver(device_id)

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
            resp = await self._client.post(
                f"{ep.base_url}/xhs/publish",
                json={
                    "account_id": str(account_id),
                    "title": title,
                    "content": content,
                    "images": images,
                    "tags": tags,
                    "request_id": request_id,
                },
                timeout=min(timeout, self._timeout) or self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return PublishResult(
                ok=True,
                note_id=uuid4(),
                platform_note_id=str(data.get("platform_note_id") or ""),
                platform_url=data.get("platform_url"),
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
            logger.exception("apk.publish failed device=%s", device_id)
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
        resp = await self._client.post(
            f"{ep.base_url}/xhs/collect_metrics",
            json={
                "account_id": str(account_id),
                "platform_note_id": platform_note_id,
                "scope": scope,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            k: int(data.get(k, 0))
            for k in ("views", "likes", "collects", "comments", "follows_gained")
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass
class MockDeviceAdapter(DevicePublisher, DeviceCollector):
    """开发 / 测试用内存模拟 APK。无需真实手机即可闭环。"""

    publish_ok: bool = True
    collect_metrics: dict[str, int] = field(
        default_factory=lambda: {
            "views": 120,
            "likes": 9,
            "collects": 4,
            "comments": 3,
            "follows_gained": 2,
        }
    )
    publish_error_code: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = field(default=0, init=False, repr=False)

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
        self._seq += 1
        self.calls.append(
            {
                "action": "publish",
                "device_id": device_id,
                "account_id": account_id,
                "title": title,
                "content": content,
                "tags": tags,
                "request_id": request_id,
            }
        )
        if not self.publish_ok:
            return PublishResult(
                ok=False,
                note_id=uuid4(),
                error_code=self.publish_error_code or "MOCK_FAIL",
                error_message="mock publish failure",
            )
        return PublishResult(
            ok=True,
            note_id=uuid4(),
            platform_note_id=f"mock-{self._seq}",
            platform_url=f"https://www.xiaohongshu.com/explore/mock-{self._seq}",
        )

    async def collect(
        self,
        *,
        device_id: UUID,
        account_id: UUID,
        platform_note_id: str,
        scope: str = "recent_24h",
    ) -> dict[str, int]:
        self.calls.append(
            {
                "action": "collect",
                "device_id": device_id,
                "account_id": account_id,
                "platform_note_id": platform_note_id,
                "scope": scope,
            }
        )
        return dict(self.collect_metrics)

    @property
    def publish_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["action"] == "publish"]

    @property
    def collect_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["action"] == "collect"]


__all__ = ["ApkEndpoint", "ApkHttpClient", "MockDeviceAdapter"]
