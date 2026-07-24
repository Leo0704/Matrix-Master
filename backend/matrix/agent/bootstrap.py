"""Agent 服务装配：把具体依赖（LLM / KB / 设备适配器 / 仓库）装进 AgentServices，并产出可执行的 RunManager。

生产路径把真实 ``LLMClient`` / ``Retriever`` / ``ApkHttpClient`` / ``DefaultAgentRepository``
传进来即可，节点代码与状态机无需改动。

v0.6.1：``device_adapter`` 改为必传。生产代码不再有 mock 兜底——忘记传会
立刻 ``RuntimeError``，避免悄悄走到假实现上。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any

from matrix.agent._services import AgentServices
from matrix.agent.protocols import DeviceInteractor
from matrix.agent.repository import AgentRepository
from matrix.agent.run_manager import RunManager
from matrix.agent.state_machine import build_state_machine

logger = get_logger(__name__)


async def _noop_notifier(name: str, payload: dict[str, Any]) -> None:
    return None


def build_agent_services(
    *,
    llm: Any,
    kb_retriever: Any,
    kb_writer: Any,
    device_adapter: Any,
    notifier: Any | None = None,
    scheduler: Any | None = None,
    task_writer: Any | None = None,
    note_writer: Any | None = None,
    checkpoint_writer: Any | None = None,
    interaction_writer: Any | None = None,
    rate_limiter: Any | None = None,
    config: Any | None = None,
    model: str = "sonnet",
    round_allocator: Any | None = None,
    llm_rate_limiter: Any | None = None,
    image_generator: Any | None = None,
    session_factory: Any | None = None,
) -> AgentServices:
    """组装 AgentServices。``device_adapter`` 必传（生产路径 = ``ApkHttpClient``）。

    v0.6：若 ``device_adapter`` 实现了 ``DeviceInteractor`` Protocol，自动作为
    ``device_interactor`` 注入。``interaction_writer`` / ``rate_limiter`` 可选注入。

    v0.7 Phase 5：``note_writer`` 默认 None → DRAFT 节点不落库；
    生产路径会传一个 ``_db_note_writer`` 把草稿写进 ``notes`` 表（status='draft'）。

    v0.7+ round-level：``round_allocator`` 默认 None → orchestrator 走降级路径；
    生产路径会传 ``DefaultRoundSlotAllocator(session_factory)`` 注入。

    v0.7+ 第 2 期：``llm_rate_limiter`` 默认 None → 跳过 LLM 限速（dev/test）；
    生产路径传 ``LLMRateLimiter(semaphore=...)``（在 ``_agent_factory.build_runtime_services``）。

    测试场景下用 ``tests._fake_adapters.MockDeviceAdapter`` 注入。
    """
    if device_adapter is None:
        raise RuntimeError(
            "device_adapter is required; production path must inject ApkHttpClient, "
            "tests must inject tests._fake_adapters.MockDeviceAdapter"
        )
    if notifier is None:
        notifier = _noop_notifier
    # v0.6: 自动探测 interactor（同 adapter 实现了 DeviceInteractor 就复用）
    device_interactor: DeviceInteractor | None = None
    if isinstance(device_adapter, DeviceInteractor):
        device_interactor = device_adapter
    return AgentServices(
        llm=llm,
        kb_retriever=kb_retriever,
        kb_writer=kb_writer,
        device_publisher=device_adapter,
        device_collector=device_adapter,
        device_interactor=device_interactor,
        notifier=notifier,
        config=config,
        model=model,
        scheduler=scheduler,
        round_allocator=round_allocator,
        task_writer=task_writer,
        note_writer=note_writer,
        checkpoint_writer=checkpoint_writer,
        interaction_writer=interaction_writer,
        rate_limiter=rate_limiter,
        llm_rate_limiter=llm_rate_limiter,
        image_generator=image_generator,
        session_factory=session_factory,
    )


# ---------------------------------------------------------------------------
# notes writer（v0.7 Phase 5：DRAFT 阶段落库）
# ---------------------------------------------------------------------------


async def db_note_writer(record: dict[str, Any]) -> Any:
    """生产 note_writer：把 DRAFT 草稿或 PUBLISH 结果 upsert 到 ``notes`` 表。

    record schema：
        - ``id`` (UUID) — 已生成的 note_id（DRAFT 节点生成的 uuid4）
        - ``account_id`` (UUID | None) — DISPATCH 时绑定；DRAFT 阶段为 None
        - ``title``, ``content``, ``images``, ``tags``
        - ``status`` — 'draft' / 'scheduled' / 'published' / 'failed'
        - ``platform_note_id``, ``platform_url`` (可选，PUBLISH 时填)
        - ``published_at`` (datetime | None，PUBLISH 时填)
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from matrix.db.models import Note
    from matrix.db.session import get_session

    payload = dict(record)
    # 可更新的白名单列（account_id/goal_id/run_id/title/content/images/tags/status/
    # platform_note_id/platform_url/published_at/scheduled_collect_at）。
    _UPSERT_COLS = (
        "account_id",
        # v0.7+ 第 2 期：goal_id/run_id 加入 upsert 白名单，让
        # DRAFT→PUBLISH 多次写时 goal/run 关联保持一致
        "goal_id",
        "run_id",
        "title",
        "content",
        "images",
        "tags",
        "status",
        "platform_note_id",
        "platform_url",
        "published_at",
        # Phase 1：让 publish_node 排 24h 延时采集时写入 scheduled_collect_at
        "scheduled_collect_at",
    )
    async with get_session() as session:
        # mark_failed 等部分写只传 id/goal_id/run_id/business_id/status，
        # 缺 title/content（NOT NULL）。若走 INSERT...ON CONFLICT，
        # asyncpg 在 bind 阶段就报 NotNullViolation（即使行存在该走 UPDATE
        # 分支）。故缺 title 时走纯 UPDATE（不 INSERT），其余走幂等 upsert。
        if "title" not in payload:
            from sqlalchemy import update as sa_update
            up_cols = {c: payload[c] for c in _UPSERT_COLS if c in payload}
            await session.execute(
                sa_update(Note).where(Note.id == payload["id"]).values(**up_cols)
            )
        else:
            stmt = pg_insert(Note).values(**payload)
            update_cols = {
                c: stmt.excluded[c] for c in _UPSERT_COLS if c in payload
            }
            stmt = stmt.on_conflict_do_update(index_elements=[Note.id], set_=update_cols)
            await session.execute(stmt)
        # 读回拿 server-default id（caller 传了 id 就直接用）
        row = (
            await session.execute(select(Note).where(Note.id == payload["id"]))
        ).scalar_one()
        return row.id


# ---------------------------------------------------------------------------
# interactions writer（v0.6 互动成功记录落库）
# ---------------------------------------------------------------------------


async def db_interaction_writer(record: dict[str, Any]) -> Any:
    """生产 interaction_writer：把 INTERACT 成功记录写进 ``interactions`` 表。

    record schema：
        - ``account_id`` (UUID) — 操作者账号
        - ``target_note_id`` (UUID | None) — 本地 notes.id（interact 节点已把
          平台 id 解析成本地 UUID；解析不到时节点不会调到这）
        - ``type`` — 'like' / 'comment'
        - ``content`` (str | None)
        - ``result`` — 缺省 'success'
        - ``request_id`` (str | None，unique)
    """
    from matrix.db.models import Interaction
    from matrix.db.session import get_session

    async with get_session() as session:
        row = Interaction(
            account_id=record["account_id"],
            target_note_id=record.get("target_note_id"),
            type=record["type"],
            content=record.get("content"),
            result=record.get("result", "success"),
            request_id=record.get("request_id"),
        )
        session.add(row)
        await session.flush()
        return row.id


def build_run_manager(
    *,
    services: AgentServices,
    repository: AgentRepository,
    state_machine: Any | None = None,
) -> RunManager:
    """产出可直接 ``create_run`` / ``start_run`` 的 RunManager。

    ``services`` 经此构造后即被注入全局（RunManager 内部调 ``set_services``），
    节点里的 ``get_services()`` 即可取到。
    """
    return RunManager(
        services=services,
        repository=repository,
        state_machine=state_machine or build_state_machine(),
    )


__all__ = ["build_agent_services", "build_run_manager", "db_note_writer", "db_interaction_writer"]
