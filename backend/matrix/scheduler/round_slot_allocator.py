"""Round-level slot allocator: 1 goal = N devices, staggered times, style variants.

设计要点：
- 复用 ``DefaultSlotPicker`` 的 active 过滤条件（active、未删、风险分 < 0.8、未自动暂停）；
- 同分随机（避免热点设备 / 账号）→ 返回长度 = min(找到的设备数, n) 的 :class:`ChosenSlot` 列表；
- 时间错开：``scheduled_at = base_time + i * stagger_minutes``，i 为设备下标；
- 风格轮换：``style_hint = STYLE_ROTATION[i % len(STYLE_ROTATION)]``，每台设备拿到不同风格；
- 活跃窗检查：任一 slot 落到窗外 → 整轮报 ``TimeOutOfWindowError``（不静默丢弃）；
- 数据源只读：每调用从 session_factory 拿新 session 拉一次候选集。

与 :class:`DefaultSlotPicker`（per-run 单选）的区别：allocator 一次返回 N 个，
且每个带 ``scheduled_at``（stagger 后）和 ``style_hint``，不调 LLM、不重选。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.agent.protocols import ChosenSlot
from matrix.db.models import Account, Device
from matrix.monitoring.logging import get_logger

from .active_window import is_in_active_window

logger = get_logger(__name__)


# 与 ``DefaultSlotPicker`` 保持一致
RISK_SCORE_MAX = 0.8
SLOT_REASON = "round_allocator.match"

# 8 种写作风格，按设备下标轮换。5 台设备 → 5 种不同风格；10 台设备 → 循环回头。
STYLE_ROTATION: tuple[str, ...] = (
    "专业严谨",   # 数据、参数、对比
    "轻松活泼",   # 口语、emoji、调侃
    "故事化",     # 第一人称叙事、场景代入
    "数据化",     # 数字、对比、量化结论
    "幽默",       # 段子、反转、夸张
    "情感共鸣",   # 痛点、共情、安慰
    "实用干货",   # 步骤、清单、tips
    "反差悬念",   # 反常识提问、悬念
)


class TimeOutOfWindowError(Exception):
    """任一 slot 落到活跃窗外时抛出（orchestrator 整轮失败）。"""


class DefaultRoundSlotAllocator:
    """Goal/round 级选槽器：一次返回 N 个 (device, account, scheduled_at, style_hint)。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def allocate(
        self,
        *,
        brief: dict,
        n: int,
        base_time: datetime | None = None,
        stagger_minutes: int = 15,
        persona_config: dict | None = None,
        business_id: UUID,
    ) -> list[ChosenSlot]:
        """挑 N 个可用 (device, account) 组合，按 i 索引错开时间和风格。

        :param brief: 主题摘要（保留供未来按 brief 过滤使用，当前未用）
        :param n: 期望设备数（soft cap 由调用方保证；本方法不强制 cap）
        :param base_time: 起始时间；默认 ``datetime.now(UTC)``
        :param stagger_minutes: 每台设备间隔分钟数
        :param persona_config: persona 活跃窗配置（透传给 ``is_in_active_window``）
        :param business_id: 业务归属（v0.7+ 必填：只挑本业务的账号/设备，
            否则多业务并存时 A 业务的稿会排到 B 业务的账号上发布——串号事故）
        :returns: 长度 = ``min(找到的设备数, n)`` 的 ``ChosenSlot`` 列表
        :raises TimeOutOfWindowError: 任一 slot 落在活跃窗外
        """
        if n <= 0:
            return []

        # 缺省时间用 UTC aware；is_in_active_window 对 aware 走 astimezone 转换
        if base_time is None:
            current = datetime.now(timezone.utc)
        elif base_time.tzinfo is None:
            current = base_time.replace(tzinfo=timezone.utc)
        else:
            current = base_time

        async with self._session_factory() as session:
            stmt = (
                select(Account.id, Device.id)
                .join(Device, Device.id == Account.device_id)
                .where(
                    Account.status == "active",
                    Account.deleted_at.is_(None),
                    Account.risk_score < RISK_SCORE_MAX,
                    (Account.auto_suspend_until.is_(None))
                    | (Account.auto_suspend_until < current),
                    Account.business_id == business_id,  # v0.7+ 业务隔离
                    Device.status == "active",
                    Device.deleted_at.is_(None),
                    Device.business_id == business_id,  # v0.7+ 业务隔离
                )
            )
            rows = (await session.execute(stmt)).all()

        if not rows:
            logger.info("round_allocator.no_candidates", n_requested=n)
            return []

        # 同分随机：避免热点设备
        random.shuffle(rows)
        rows = rows[:n]

        slots: list[ChosenSlot] = []
        for i, (account_id, device_id) in enumerate(rows):
            scheduled_at = current + timedelta(minutes=stagger_minutes * i)
            style_hint = STYLE_ROTATION[i % len(STYLE_ROTATION)]

            if not is_in_active_window(scheduled_at, persona_config):
                raise TimeOutOfWindowError(
                    f"slot {i} scheduled at {scheduled_at.isoformat()} "
                    f"is outside active window "
                    f"(base={current.isoformat()}, stagger={stagger_minutes}min, n={len(rows)})"
                )

            slots.append(
                ChosenSlot(
                    device_id=device_id,
                    account_id=account_id,
                    reason=SLOT_REASON,
                    scheduled_at=scheduled_at,
                    style_hint=style_hint,
                )
            )
        logger.info(
            "round_allocator.allocated",
            n=len(slots),
            n_requested=n,
            stagger_minutes=stagger_minutes,
            base=current.isoformat(),
        )
        return slots

    async def count_active_devices(self, *, business_id: UUID) -> int:
        """拉本业务当前 active device 计数（不分配，仅用于 orchestrator 决定 n）。"""
        async with self._session_factory() as session:
            stmt = (
                select(Account.id, Device.id)
                .join(Device, Device.id == Account.device_id)
                .where(
                    Account.status == "active",
                    Account.deleted_at.is_(None),
                    Account.risk_score < RISK_SCORE_MAX,
                    (Account.auto_suspend_until.is_(None))
                    | (Account.auto_suspend_until < datetime.now(timezone.utc)),
                    Account.business_id == business_id,  # v0.7+ 业务隔离
                    Device.status == "active",
                    Device.deleted_at.is_(None),
                    Device.business_id == business_id,  # v0.7+ 业务隔离
                )
            )
            rows = (await session.execute(stmt)).all()
        return len(rows)

    async def is_slot_valid(
        self,
        *,
        device_id: UUID,
        account_id: UUID,
        business_id: UUID,
        now: datetime | None = None,
    ) -> bool:
        """校验预分配 slot 的 device+account 是否仍 active（SCHEDULE 节点二次确认用）。

        任一字段不满足（设备 inactive / 账号 inactive / 风险分高 / 暂停中 / 软删除 /
        **不属于本业务**）→ 返回 False；调用方应报 NO_AVAILABLE_SLOT。
        """
        current = now or datetime.now(timezone.utc)
        async with self._session_factory() as session:
            stmt = (
                select(Account.id, Device.id)
                .join(Device, Device.id == Account.device_id)
                .where(
                    Account.id == account_id,
                    Account.status == "active",
                    Account.deleted_at.is_(None),
                    Account.risk_score < RISK_SCORE_MAX,
                    (Account.auto_suspend_until.is_(None))
                    | (Account.auto_suspend_until < current),
                    Account.business_id == business_id,  # v0.7+ 业务隔离
                    Device.id == device_id,
                    Device.status == "active",
                    Device.deleted_at.is_(None),
                    Device.business_id == business_id,  # v0.7+ 业务隔离
                )
            )
            row = (await session.execute(stmt)).first()
        return row is not None


__all__ = [
    "DefaultRoundSlotAllocator",
    "STYLE_ROTATION",
    "TimeOutOfWindowError",
    "RISK_SCORE_MAX",
    "SLOT_REASON",
]
