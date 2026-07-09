"""测试用假适配器集合。

只允许在 tests/ 目录及其子目录下 import。生产代码绝对禁止引用。
（v0.6.1：从 matrix.device.adapters 搬出，遵守"非测试代码不允许 mock"原则）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from matrix.agent.protocols import (
    DeviceCollector,
    DevicePublisher,
    PublishResult,
)


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


__all__ = ["MockDeviceAdapter"]
