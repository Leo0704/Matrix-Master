"""Anthropic / OpenAI prompt caching 封装。

Anthropic 通过消息 content 中的 ``cache_control`` 块启用 prompt cache。
OpenAI 自动对长 prompt 启用 cache（无需显式配置；本模块提供是否启用开关）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CachedBlock:
    """Anthropic cache_control 包装的文本块。"""

    text: str
    cache_type: str = "ephemeral"  # 'ephemeral' | 'long'

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "type": "text",
            "text": self.text,
            "cache_control": {"type": self.cache_type},
        }


@dataclass
class CachedMessages:
    """构造带 cache_control 的 messages 列表。

    用法::

        msgs = CachedMessages(system="persona 描述 ...")
        msgs.add_user("今天写什么？", cache=True)
        client.messages.create(model=..., messages=msgs.build(), ...)
    """

    system: str | None = None
    system_cache: bool = True
    _messages: list[dict[str, Any]] = field(default_factory=list)
    _last_user_text: str | None = None
    _last_user_cache: bool = False

    def add_user(self, text: str, *, cache: bool = False) -> None:
        block: dict[str, Any] = {"type": "text", "text": text}
        if cache:
            block["cache_control"] = {"type": "ephemeral"}
        self._messages.append({"role": "user", "content": [block]})

    def add_assistant(self, text: str) -> None:
        self._messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": text}]}
        )

    def build(self) -> tuple[Any, list[dict[str, Any]]]:
        """返回 (system, messages)。"""
        if self.system and self.system_cache:
            sys_payload: Any = [
                {
                    "type": "text",
                    "text": self.system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            sys_payload = self.system
        return sys_payload, self._messages
