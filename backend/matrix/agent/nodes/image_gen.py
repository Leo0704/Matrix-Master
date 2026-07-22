"""IMAGE_GEN 节点（v0.7 Phase 3）。

业务约束：小红书图文必发。流程：DRAFT → IMAGE_GEN → REVIEW。

行为：
- 读 ``state["draft"]`` 构造 image prompt
- 优先查 KB 缓存（``doc_type=image_asset``），命中则复用 URLs
- 未命中调 ``services.image_generator.generate``，成功后把 URLs 写回
  ``draft.images``
- 失败兜底（D6）：默认 ``fallback=no_image``：返回 ``last_error=IMAGE_GEN_*``
  但 ``draft.images=[]``，下游可继续走纯文；切 ``fallback=idle`` 强制 ALERT
- 测试可通过 ``state["image_generator"]`` 或 ``services.image_generator``
  注入客户端；``image_gen_fallback=idle`` 强制失败路径
"""
from __future__ import annotations

import hashlib
from typing import Any

from matrix.llm.image_gen import ImageGenError
from matrix.monitoring.logging import get_logger

from .._services import get_services
from ..types import AgentState

logger = get_logger(__name__)


FALLBACK_NO_IMAGE = "no_image"  # 默认：纯文发布
FALLBACK_IDLE = "idle"  # 强制 ALERT


def _topic_hash(account_id: Any, title: str, style_version: int = 1) -> str:
    """缓存键：(account, title, style_version)。"""
    raw = f"{str(account_id or '')}|{title.strip()}|{style_version}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _build_image_prompt(draft: dict, topic: dict | None) -> str:
    """从 draft 构造 image prompt。"""
    title = str(draft.get("title", "")).strip()
    tags = draft.get("tags") or []
    tags_part = ", ".join(str(t) for t in tags[:6]) if tags else ""
    if topic and isinstance(topic, dict) and not title:
        title = str(topic.get("title", "")).strip()
    parts = ["小红书图文笔记封面", title]
    if tags_part:
        parts.append(f"主题风格: {tags_part}")
    parts.append("竖版构图 1024x1024 高清")
    return " | ".join(p for p in parts if p)


async def image_gen_node(state: AgentState) -> dict[str, Any]:
    """生图节点（D6 默认 fallback=no_image）。"""
    services = get_services()
    draft = state.get("draft") or {}

    if not draft.get("title"):
        return _fallback_result(
            draft,
            code="IMAGE_GEN_NO_DRAFT_TITLE",
            message="draft missing title; skip image gen",
        )

    fallback = _read_fallback(state)
    topic = (
        state.get("selected_topic")
        if isinstance(state.get("selected_topic"), dict)
        else None
    )
    topic_hash = _topic_hash(state.get("account_id"), str(draft.get("title", "")))

    # 1) KB 缓存查找
    cache_hit = await _lookup_cached_image(services, topic_hash)
    if cache_hit:
        new_draft = {**draft, "images": cache_hit}
        return {"draft": new_draft, "last_error": None, "image_cache_hit": True}

    # 2) 调生图 client
    image_gen = getattr(services, "image_generator", None)
    if image_gen is None:
        return _fallback_result(
            draft,
            code="IMAGE_GEN_NO_CLIENT",
            message="AgentServices.image_generator not configured",
            fallback=fallback,
        )

    prompt = _build_image_prompt(draft, topic)
    try:
        result = await image_gen.generate(
            prompt=prompt,
            n=1,
            size="1024*1024",
            timeout=60.0,
        )
    except ImageGenError as exc:
        logger.warning("image_gen.provider_failed err=%s", exc)
        return _fallback_result(
            draft,
            code="IMAGE_GEN_FAILED",
            message=str(exc),
            fallback=fallback,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("image_gen.unexpected")
        return _fallback_result(
            draft,
            code="IMAGE_GEN_EXCEPTION",
            message=str(exc),
            fallback=fallback,
        )

    urls = list(result.urls or [])
    if not urls:
        return _fallback_result(
            draft,
            code="IMAGE_GEN_EMPTY",
            message="provider returned no urls",
            fallback=fallback,
        )

    # 3) 写 KB 缓存（best-effort）
    await _save_image_to_kb(
        services, topic_hash, prompt, result, business_id=state.get("business_id")
    )

    new_draft = {**draft, "images": urls}
    return {"draft": new_draft, "last_error": None, "image_cache_hit": False}


def _read_fallback(state: AgentState) -> str:
    fb = state.get("image_gen_fallback")
    return fb or FALLBACK_NO_IMAGE


def _fallback_result(
    draft: dict,
    *,
    code: str,
    message: str,
    fallback: str | None = None,
) -> dict[str, Any]:
    fallback = fallback or FALLBACK_NO_IMAGE
    base_err = {"code": code, "message": message}
    if fallback == FALLBACK_IDLE:
        # draft=None 触发下游 REVIEW guard 失败 → ALERT
        return {"draft": None, "last_error": base_err}
    return {"draft": {**draft, "images": []}, "last_error": base_err}


async def _lookup_cached_image(services: Any, topic_hash: str) -> list[str] | None:
    retriever = getattr(services, "kb_retriever", None)
    if retriever is None:
        return None
    try:
        from ..protocols import RetrieveQuery

        chunks = await retriever.retrieve(
            RetrieveQuery(query=topic_hash, doc_types=("image_asset",), top_k=1)
        )
    except Exception:
        logger.exception("image_gen.cache_lookup_failed")
        return None
    if not chunks:
        return None
    first = chunks[0]
    meta = getattr(first, "metadata", {}) or {}
    cached_urls = meta.get("urls") or []
    return [str(u) for u in cached_urls if u]


async def _save_image_to_kb(
    services: Any,
    topic_hash: str,
    prompt: str,
    result: Any,
    *,
    business_id: Any | None = None,
) -> None:
    writer = getattr(services, "kb_writer", None)
    if writer is None or business_id is None:
        return
    try:
        await writer.upsert_document(
            type="image_asset",
            ref_id=None,
            title=f"image:{topic_hash}",
            content=prompt,
            metadata={
                "topic_hash": topic_hash,
                "urls": list(result.urls or []),
                "provider": result.provider,
                "model": result.model,
                "revised_prompt": result.revised_prompt or "",
            },
            business_id=business_id,
        )
    except Exception:
        logger.exception("image_gen.kb_write_failed")


__all__ = ["image_gen_node", "_topic_hash", "_build_image_prompt"]
