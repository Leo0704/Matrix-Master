"""运营小助手 chat 路由（v0.7+ 重定位）。

替代 v0.7 之前的"主题识别 + 建目标"路径；现在 chat 只做运维/查询/调参辅助。
建目标走 POST /goals 手动表单。

支持 5 类 intent（dispatch 到 matrix.agent.chat_tools）：
  - ask_data / browse_kb / chitchat：只读，无需确认
  - diagnose：只读（含二次 LLM 归因，第 3 期）
  - preview_change / apply_change：写操作，走 confirmation_token 两阶段

确认机制：confirmation_token 落 ``chat_confirmation_tokens`` 表（10 分钟 TTL，
消费即删）——多 worker / 多实例部署不再丢令牌。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.agent.chat_tools import (
    CHAT_BATCH_LIMIT,
    CHAT_TOOL_DISPATCH,
    TOOL_REQUIRED_ARGS,
    _resolve_goal_filter,
)
from matrix.agent.prompts import CHAT_SYSTEM, CHAT_USER
from matrix.api.deps import get_db
from matrix.api.schemas import ChatAction, ChatHistoryMessage, ChatRequest, ChatResponse
from matrix.db.models import ChatConfirmationToken
from matrix.llm.errors import LLMError
from matrix.llm.retry import retry_with_backoff
from matrix.llm.router import get_default_client
from matrix.config import get_settings
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Confirmation token 存储（DB 表，10 分钟 TTL，消费即删）
# ---------------------------------------------------------------------------

_CONFIRMATION_TTL_SEC = 600  # 10 分钟


def _make_token() -> str:
    """生成 UUID token。"""
    return str(uuid.uuid4())


async def _store_token(
    session: AsyncSession, token: str, args: dict[str, Any], business_id: uuid.UUID
) -> None:
    """落库：token → (args, business_id, expires_at)。v0.7+：绑定创建时的 business。"""
    session.add(
        ChatConfirmationToken(
            token=token,
            args=args,
            business_id=business_id,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=_CONFIRMATION_TTL_SEC),
        )
    )
    await session.flush()


async def _consume_token(
    session: AsyncSession, token: str
) -> tuple[Optional[dict[str, Any]], Optional[uuid.UUID]]:
    """校验 + 取出（消费即删除，避免重复执行）。

    返回 (args, business_id) — 任一为 None 表示令牌无效/已过期。
    过期行不主动清理：量小无害，consume 时判定即可。
    """
    row = await session.get(ChatConfirmationToken, token)
    if row is None:
        return None, None
    await session.delete(row)
    expires_at = row.expires_at
    # 兼容 naive（SQLite / 测试 fake）：一律按 UTC 对齐再比较
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None, None
    return dict(row.args or {}), row.business_id


# ---------------------------------------------------------------------------
# LLM 调用 + JSON 解析（保留 v0.7 之前的容错逻辑）
# ---------------------------------------------------------------------------


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
    """从 LLM 文本中尽力解析 JSON。容错：剥 markdown 围栏、找首个 {...}。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in LLM output")
    return json.loads(text[start : end + 1])


@retry_with_backoff(max_attempts=3, backoff=(1.0, 3.0, 9.0))
async def _call_llm(prompt: str, system: str) -> str:
    client = get_default_client()
    settings = get_settings()
    result = await client.complete(
        prompt,
        model=settings.matrix_llm_model or "MiniMax-M3",
        max_tokens=800,
        temperature=0.3,
        system=system,
        call_type="decision",
    )
    return result.text


# ---------------------------------------------------------------------------
# Chat 路由主函数
# ---------------------------------------------------------------------------


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    session: AsyncSession = Depends(get_db),
) -> ChatResponse:
    message = body.message.strip()

    # 1) /confirm <token> 短路 —— 用户在前端确认 preview_change 后调用
    if message.startswith("/confirm "):
        token = message[len("/confirm ") :].strip()
        cached_args, cached_business_id = await _consume_token(session, token)
        if cached_args is None:
            return ChatResponse(
                reply="确认令牌无效或已过期，请重新发起指令。",
                action=ChatAction(
                    type="parse_error", payload={"reason": "token_invalid"}
                ),
                error_hint="令牌 10 分钟内有效；重新发起预览即可拿到新令牌。",
            )
        # v0.7+ 跨业务拒绝：token 创建时的 business 与当前请求 business 必须一致
        if cached_business_id != body.business_id:
            return ChatResponse(
                reply="确认令牌的所属业务与当前请求不一致，已拒绝执行。",
                action=ChatAction(
                    type="parse_error", payload={"reason": "business_mismatch"}
                ),
                error_hint="跨业务确认被拦截，请重新切到令牌对应的业务再确认。",
            )
        tool = CHAT_TOOL_DISPATCH["apply_change"]
        try:
            # apply_change 工具接收 operator_business_id 鉴权
            result = await tool(
                session,
                {**cached_args, "confirmation_token": token},
                business_id=body.business_id,
                operator_business_id=body.business_id,
            )
        except Exception as e:
            logger.exception("chat.apply_change.error", token=token)
            return ChatResponse(
                reply=f"执行失败：{type(e).__name__}: {e}",
                action=ChatAction(type="llm_error", payload={"intent": "apply_change"}),
                error_hint="工具执行异常，可重试。",
            )
        return ChatResponse(
            reply=result.get("reply") or "已执行。",
            action=ChatAction(
                type="apply_change",
                payload=result.get("payload", {}),
            ),
        )

    # 2) /cancel <token> 短路 —— 用户取消预览
    if message.startswith("/cancel "):
        token = message[len("/cancel ") :].strip()
        await _consume_token(session, token)  # 不关心返回值，丢弃即可
        return ChatResponse(
            reply="已取消，未执行任何操作。",
            action=ChatAction(type="noop", payload={"cancelled_token": token}),
        )

    # 3) 空消息
    if not message:
        return ChatResponse(
            reply="收到空消息，请输入指令。",
            action=ChatAction(type="noop", payload={}),
        )

    # 4) 调 LLM
    history_text = _format_history(body.history)
    prompt = CHAT_USER.format(
        history=history_text,
        message=message,
        today_date=date.today().isoformat(),
    )
    try:
        raw = await _call_llm(prompt, CHAT_SYSTEM)
    except LLMError as e:
        logger.warning("chat.llm.call_failed", error=e)
        return ChatResponse(
            reply="运营小助手暂不可用，请稍后重试。",
            action=ChatAction(type="llm_error", payload={"error": str(e)}),
            error_hint="LLM 服务异常，可能是额度或网络问题。",
        )

    # 5) 解析 JSON
    try:
        parsed = _parse_llm_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("chat.llm.parse_error", raw=raw[:200], error=e)
        return ChatResponse(
            reply=raw[:500] or "请再说一次。",
            action=ChatAction(type="parse_error", payload={}),
            error_hint="模型返回的不是 JSON，试试换种说法。",
        )

    reply_text = str(parsed.get("reply", "")).strip()
    intent = str(parsed.get("intent", "unknown")).strip().lower()
    args = parsed.get("args") or {}
    if not isinstance(args, dict):
        args = {}

    # 6) chitchat 短路
    if intent == "chitchat":
        return ChatResponse(
            reply=reply_text,
            action=ChatAction(type="chitchat", payload={}),
        )

    # 7) unknown_intent 兜底
    if intent not in CHAT_TOOL_DISPATCH:
        return ChatResponse(
            reply=reply_text or "我不确定你要做什么。",
            action=ChatAction(type="unknown_intent", payload={"raw_intent": intent}),
            error_hint=(
                f"未知意图 '{intent}'。可问：「现在有几个 goal 在跑？」"
                "「把 max_rounds=3 的 goal 改成 5」「看看这周 KB 新写了哪些 strategy_card」"
            ),
        )

    # 8) 必填参数检查
    tool = CHAT_TOOL_DISPATCH[intent]
    required = TOOL_REQUIRED_ARGS.get(intent, [])
    missing = [k for k in required if not args.get(k)]
    if missing:
        return ChatResponse(
            reply=f"需要补充：{', '.join(missing)}。",
            action=ChatAction(
                type="missing_args",
                payload={"missing": missing, "intent": intent},
            ),
            error_hint="参数不全，没法执行。",
        )

    # 9) 批量上限检查（仅对 preview_change：先 dry-run 匹配数量）
    if intent == "preview_change":
        filter_args = args.get("filter") or {}
        if isinstance(filter_args, dict):
            matched = await _resolve_goal_filter(
                session, filter_args, business_id=body.business_id
            )
            if len(matched) > CHAT_BATCH_LIMIT:
                return ChatResponse(
                    reply=(
                        f"匹配到 {len(matched)} 个 goal，"
                        f"超过单次上限 {CHAT_BATCH_LIMIT}。请缩小范围（如指定 theme_keyword）。"
                    ),
                    action=ChatAction(
                        type="batch_too_large",
                        payload={
                            "matched": len(matched),
                            "limit": CHAT_BATCH_LIMIT,
                        },
                    ),
                    error_hint="批量操作有上限，避免误伤。",
                )

    # 10) dispatch 到工具（v0.7+ 全部透传 business_id）
    try:
        # preview_change / apply_change 多传 operator_business_id（默认用 body.business_id）
        if intent in ("preview_change", "apply_change"):
            result = await tool(
                session, args, business_id=body.business_id,
                operator_business_id=body.business_id,
            )
        else:
            result = await tool(session, args, business_id=body.business_id)
    except Exception as e:
        logger.exception("chat.tool.error", intent=intent)
        return ChatResponse(
            reply=f"执行 {intent} 失败：{type(e).__name__}: {e}",
            action=ChatAction(type="llm_error", payload={"intent": intent, "error": str(e)}),
            error_hint="工具执行异常，可重试或换种问法。",
        )

    payload = result.get("payload", {})
    requires_confirmation = bool(result.get("requires_confirmation"))

    # 11) 写操作需要 confirmation（v0.7+ 绑定 business_id）
    if requires_confirmation:
        token = _make_token()
        await _store_token(session, token, args, body.business_id)
        return ChatResponse(
            reply=reply_text or f"准备好执行，请确认。",
            action=ChatAction(
                type="preview_change",
                payload=payload,
                needs_confirmation=True,
                confirmation_token=token,
            ),
            confirmation_token=token,
        )

    # 12) 只读 / 已确认
    return ChatResponse(
        reply=reply_text,
        action=ChatAction(type=intent, payload=payload),
    )