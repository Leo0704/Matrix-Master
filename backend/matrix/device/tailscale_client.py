"""Tailscale / Headscale 客户端（SDD §3.7.1 提到 Tailscale 隧道是控制通道）。

Headscale 控制面 API（v1）：

- ``POST /api/v1/node``       — 注册新节点
- ``DELETE /api/v1/node/{id}`` — 撤销节点
- ``GET /api/v1/node``         — 列出节点（ACL 调试用）

调用方可以传 ``api_url`` / ``api_key``（生产从 ``TS_API_URL`` / ``TS_API_KEY`` 读），
或者注入一个 ``TailscaleClient`` 的子类做测试。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = get_logger(__name__)


@dataclass
class TailscaleNode:
    """Headscale 节点最小字段。"""

    id: str
    name: str
    given_name: str
    ip_addresses: list[str]


class TailscaleError(RuntimeError):
    """Tailscale / Headscale API 调用失败。"""


class TailscaleClient:
    """Headscale 客户端（v1 API，Bearer 鉴权）。"""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        *,
        client: Optional[httpx.AsyncClient] = None,
        user: str = "tag:matrix-device",
    ) -> None:
        self.api_url = (api_url or os.environ.get("TS_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("TS_API_KEY", "")
        self.user = user
        # 允许测试注入 httpx client（respx 拦截）；否则创建默认
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "TailscaleClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    async def register_node(self, auth_key: str, name: str) -> str:
        """注册一个新节点；返回 Headscale 分配的 node_id。

        Args:
            auth_key: Headscale pre-auth key
            name: 节点名（主控侧可读标识）
        """
        if not self.api_url:
            raise TailscaleError("TS_API_URL not configured")

        payload = {
            "user": self.user,
            "key": auth_key,
            "hostname": name,
        }
        try:
            resp = await self._client.post("/api/v1/node", json=payload)
        except httpx.HTTPError as e:
            raise TailscaleError(f"register_node network error: {e}") from e
        if resp.status_code >= 400:
            raise TailscaleError(
                f"register_node failed: status={resp.status_code} body={resp.text[:200]}"
            )
        data = resp.json()
        node = data.get("node") or data
        return str(node.get("id"))

    async def revoke_node(self, node_id: str) -> None:
        """撤销 / 删除节点。"""
        if not self.api_url:
            raise TailscaleError("TS_API_URL not configured")

        try:
            resp = await self._client.delete(f"/api/v1/node/{node_id}")
        except httpx.HTTPError as e:
            raise TailscaleError(f"revoke_node network error: {e}") from e
        if resp.status_code >= 400:
            raise TailscaleError(
                f"revoke_node failed: status={resp.status_code} body={resp.text[:200]}"
            )

    async def list_nodes(self, user: Optional[str] = None) -> list[TailscaleNode]:
        """列出指定用户下的所有节点（ACL 调试用）。"""
        if not self.api_url:
            raise TailscaleError("TS_API_URL not configured")

        target_user = user or self.user
        try:
            resp = await self._client.get("/api/v1/node", params={"user": target_user})
        except httpx.HTTPError as e:
            raise TailscaleError(f"list_nodes network error: {e}") from e
        if resp.status_code >= 400:
            raise TailscaleError(
                f"list_nodes failed: status={resp.status_code} body={resp.text[:200]}"
            )
        data = resp.json()
        nodes_raw = data.get("nodes") or []
        return [
            TailscaleNode(
                id=str(n.get("id", "")),
                name=str(n.get("name", "")),
                given_name=str(n.get("givenName", "")),
                ip_addresses=list(n.get("ipAddresses") or []),
            )
            for n in nodes_raw
        ]


__all__ = ["TailscaleClient", "TailscaleError", "TailscaleNode"]
