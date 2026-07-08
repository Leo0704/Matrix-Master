"""文档 chunker：按 token 数切分文本。

策略（与 SDD §3.2.2 / kb-writing-guide §3.2 一致）：
- 每个 chunk 目标 500 token，overlap 50 token
- 用 ``tiktoken`` 的 ``cl100k_base`` 编码（与 OpenAI text-embedding-3-* 一致）
- 短文本（<= 500 token）不切，整段返回

切分算法：
- 把全文编码成 token 序列
- 按 ``chunk_size`` 切片；相邻两片之间保留 ``overlap`` token
- 每个切片用 ``decode`` 还原为文本（cl100k_base decode 单 token 不会跨多 token 失真）
"""
from __future__ import annotations

from dataclasses import dataclass

import tiktoken


DEFAULT_CHUNK_SIZE: int = 500
DEFAULT_OVERLAP: int = 50
DEFAULT_ENCODING: str = "cl100k_base"


@dataclass(frozen=True)
class Chunk:
    """单个 chunk。

    Attributes:
        text: chunk 文本
        token_count: chunk 的实际 token 数
        index: 在原文档中的顺序（0-based）
    """

    text: str
    token_count: int
    index: int


class Chunker:
    """按 token 数切分文本的 chunker。"""

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
        encoding_name: str = DEFAULT_ENCODING,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError(
                f"overlap must satisfy 0 <= overlap < chunk_size, "
                f"got chunk_size={chunk_size} overlap={overlap}"
            )
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._enc = tiktoken.get_encoding(encoding_name)

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    @property
    def overlap(self) -> int:
        return self._overlap

    def split(self, text: str) -> list[Chunk]:
        """把 ``text`` 切分成 ``list[Chunk]``。

        短文本（编码后 token 数 <= chunk_size）整段返回为单 chunk。
        长文本按 ``chunk_size`` 切片，chunk 间保留 ``overlap`` token。
        """
        if not text:
            return []

        tokens = self._enc.encode(text)
        if len(tokens) <= self._chunk_size:
            return [Chunk(text=text, token_count=len(tokens), index=0)]

        step = self._chunk_size - self._overlap
        chunks: list[Chunk] = []
        idx = 0
        start = 0
        while start < len(tokens):
            end = min(start + self._chunk_size, len(tokens))
            sub_tokens = tokens[start:end]
            chunk_text = self._enc.decode(sub_tokens)
            chunks.append(Chunk(text=chunk_text, token_count=len(sub_tokens), index=idx))
            idx += 1
            # 最后一片已读完
            if end == len(tokens):
                break
            start += step
        return chunks
