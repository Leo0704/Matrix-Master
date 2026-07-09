"""节点通用 util：LLM JSON 解析、上下文注入。"""

from __future__ import annotations

import json
from matrix.monitoring.logging import get_logger
import re
from typing import Any

logger = get_logger(__name__)


_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_response(text: str) -> dict[str, Any]:
    """从 LLM 文本中提取 JSON。宽容处理：

    - 直接是 JSON
    - 包在 ```json ... ``` / ``` ... ``` 代码块
    - 前后夹带其他字符
    """
    if not text:
        return {}
    text = text.strip()

    # 代码块
    m = _JSON_RE.search(text)
    if m:
        text = m.group(1).strip()

    # 直接尝试
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取首个 {...}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            logger.warning("agent.llm.json_parse_fail", text=text[:200])

    return {}


def join_chunks(chunks: list, *, limit: int = 5) -> str:
    """把 KB chunks 拼成 prompt 可读片段。"""
    lines = []
    for chunk in chunks[:limit]:
        text = getattr(chunk, "text", "")
        lines.append(f"- ({chunk.doc_type}) {text}")
    return "\n".join(lines) or "(none)"


__all__ = ["parse_json_response", "join_chunks", "format_brief"]


def format_brief(brief: dict[str, Any] | None) -> str:
    """把 brief 主题对象转成可读摘要，给 prompt 注入主题上下文。

    若 brief 缺失或全字段为空，返回空串（调用方按需跳过）。
    """
    if not isinstance(brief, dict) or not brief:
        return ""
    lines: list[str] = []
    theme = brief.get("theme")
    audience = brief.get("audience")
    product_category = brief.get("product_category")
    goal_type = brief.get("goal_type")
    if theme:
        lines.append(f"主题：{theme}")
    if audience:
        lines.append(f"目标人群：{audience}")
    if product_category:
        lines.append(f"商品/内容类目：{product_category}")
    if goal_type:
        lines.append(f"动作类型：{goal_type}")
    return "\n".join(lines)
