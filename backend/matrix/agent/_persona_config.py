"""persona_config 统一读取（E2E 实测补接线）。

背景：``schedule_node`` / ``orchestrator`` 都只读
``services.system_metadata["persona_config"]``，但生产路径从未往这个
dict 里写过值 —— 活跃窗永远落回硬编码默认（9-23），不可配置。

读取顺序：
1. ``services.config``（生产路径是 app_config 表的懒读取器）里的
   ``persona_config`` 键 —— 用户可通过 settings API / 直接写库调整；
2. 兜底 ``services.system_metadata["persona_config"]``（测试注入用）。
"""
from __future__ import annotations

from typing import Any


async def load_persona_config(services: Any) -> dict | None:
    """取 persona_config；拿不到返回 None（调用方用默认活跃窗）。"""
    try:
        cfg = getattr(services, "config", None)
        if cfg is not None:
            val = await cfg.get("persona_config", None)
            if isinstance(val, dict):
                return val
    except Exception:
        pass
    try:
        val = services.system_metadata.get("persona_config")
        return val if isinstance(val, dict) else None
    except Exception:
        return None


__all__ = ["load_persona_config"]
