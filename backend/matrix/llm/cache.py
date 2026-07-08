"""LLM 响应缓存：按 prompt hash 存 CompletionResult，1h TTL。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .clients import CompletionResult


def _make_key(
    *,
    prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
    system: str | None,
) -> str:
    """缓存 key 包含影响输出的全部参数。"""
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(max_tokens).encode("utf-8"))
    h.update(b"\x00")
    h.update(repr(float(temperature)).encode("utf-8"))
    h.update(b"\x00")
    if system:
        h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    return h.hexdigest()


@dataclass
class _Entry:
    result: CompletionResult
    expires_at: float


class CompletionCache:
    """进程内 LRU 缓存，TTL 1 小时。"""

    def __init__(self, *, max_size: int = 512, ttl_seconds: float = 3600.0) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._data: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    def make_key(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 1.0,
        system: str | None = None,
    ) -> str:
        return _make_key(
            prompt=prompt,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )

    async def get(self, key: str) -> CompletionResult | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._data[key]
                return None
            # LRU bump
            self._data.move_to_end(key)
            return entry.result

    async def set(self, key: str, result: CompletionResult) -> None:
        async with self._lock:
            self._data[key] = _Entry(result=result, expires_at=time.monotonic() + self._ttl)
            self._data.move_to_end(key)
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, Any]:
        return {"size": len(self._data), "max_size": self._max_size, "ttl_seconds": self._ttl}
