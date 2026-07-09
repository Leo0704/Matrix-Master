"""自然语言指令 (chat) 端点。

真 LLM 多轮对话 + 主题识别：
- 接收前端传来的完整消息历史（localStorage 自管）
- 拼 prompt 调 LLM，让 LLM 输出 JSON {reply, theme_confirmed, theme}
- theme_confirmed=true 时建 Goal（target=ThemeTarget 结构化 dict） + AgentRun（payload 含 brief）
- theme_confirmed=false 时仅返回 reply
- 暂停关键词短路保留
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.agent.prompts import CHAT_SYSTEM, CHAT_USER
from matrix.api.deps import get_db
from matrix.api.schemas import (
    ChatAction,
    ChatHistoryMessage,
    ChatRequest,
    ChatResponse,
)
from matrix.db.models import AgentRun, Goal as GoalORM
from matrix.llm.errors import LLMError
from matrix.llm.retry import retry_with_backoff
from matrix.llm.router import get_default_client
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# 暂停关键词：与 LLM 短路，避免无意义 token 消耗
_PAUSE_PATTERNS = [r"暂停", r"停下", r"停止", r"pause", r"stop"]


def _format_history(history: list[ChatHistoryMessage]) -> str:
    """把 history 拼成可读文本给 LLM 上下文。"""
    if not history:
        return "（无历史，这是第一轮）"
    lines: list[str] = []
    for m in history:
        prefix = "运营者" if m.role == "user" else "你"
        lines.append(f"{prefix}：{m.content}")
    return "\n".join(lines)


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """从 LLM 文本中尽力解析 JSON。容错：剥 markdown 围栏、剥前后缀、找首个 {..}。"""
    text = raw.strip()
    # 剥 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # 取第一个 {...} 段
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in LLM output")
    return json.loads(text[start : end + 1])


@retry_with_backoff(max_attempts=3, backoff=(1.0, 3.0, 9.0))
async def _call_llm(prompt: str, system: str) -> str:
    client = get_default_client()
    result = await client.complete(
        prompt,
        model="sonnet",
        max_tokens=512,
        temperature=0.3,
        system=system,
        call_type="decision",
    )
    return result.text


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    session: AsyncSession = Depends(get_db),
) -> ChatResponse:
    message = body.message.strip()
    if not message:
        return ChatResponse(
            reply="收到空消息，请输入指令。",
            action=ChatAction(type="noop", payload={}),
        )

    # 暂停关键词短路
    lower = message.lower()
    if any(p in lower for p in _PAUSE_PATTERNS):
        stmt = select(AgentRun).where(AgentRun.status == "running")
        runs = (await session.execute(stmt)).scalars().all()
        cancelled = 0
        for r in runs:
            r.status = "cancelled"
            cancelled += 1
        await session.flush()
        return ChatResponse(
            reply=f"已请求暂停 {cancelled} 个运行中的 Agent run。",
            action=ChatAction(type="pause_all", payload={"cancelled": cancelled}),
        )

    # 真 LLM 多轮对话
    history_text = _format_history(body.history)
    prompt = CHAT_USER.format(history=history_text, message=message)

    try:
        raw = await _call_llm(prompt, CHAT_SYSTEM)
    except LLMError as e:
        logger.warning("chat.llm.call_failed", error=e)
        return ChatResponse(
            reply="主题识别暂不可用，请稍后重试。",
            action=ChatAction(type="llm_error", payload={"error": str(e)}),
        )

    # 解析 LLM JSON 输出，容错
    try:
        parsed = _parse_llm_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            "chat.llm.non_json_response",
            raw=raw[:200],
            error=e,
        )
        return ChatResponse(
            reply=raw[:500] or "请再说一次。",
            action=ChatAction(type="parse_error", payload={}),
        )

    reply_text = str(parsed.get("reply", "")).strip()
    theme_confirmed = bool(parsed.get("theme_confirmed"))
    theme_payload = parsed.get("theme") or {}
    if not isinstance(theme_payload, dict):
        theme_payload = {}

    if not theme_confirmed:
        return ChatResponse(
            reply=reply_text or "请补充一下主题细节。",
            theme_confirmed=False,
            theme_payload=theme_payload or None,
        )

    # 主题已明确：建 Goal + AgentRun
    goal_type = str(theme_payload.get("goal_type") or "generic")
    g = GoalORM(
        type=goal_type,
        target=theme_payload,  # JSONB 存 ThemeTarget 结构化对象
        status="active",
    )
    session.add(g)
    await session.flush()

    run = AgentRun(
        goal_id=g.id,
        current_state="IDLE",
        payload={
            "brief": theme_payload,
            "goal_text": message,
            "entry": "RESEARCH",
        },
        status="running",
    )
    session.add(run)
    await session.flush()

    logger.info(
        "chat -> goal created via LLM",
        goal_id=str(g.id),
        run_id=str(run.id),
        type=goal_type,
        theme=theme_payload.get("theme"),
    )

    return ChatResponse(
        reply=reply_text or f"主题已确定：{theme_payload.get('theme', '')}",
        theme_confirmed=True,
        theme_payload=theme_payload,
        action=ChatAction(
            type="create_goal",
            payload={
                "goal_id": str(g.id),
                "run_id": str(run.id),
                "goal_type": goal_type,
            },
        ),
    )
