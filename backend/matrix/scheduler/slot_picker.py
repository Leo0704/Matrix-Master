"""调度选槽：给定草稿 + persona 配置，从 active device/account 池里挑一个可用组合。

设计要点：
- 数据源只读：从 accounts JOIN devices 拉一次候选集；
- 同分随机（避免热点设备 / 账号）→ 返回 :class:`ChosenSlot`；
- 无候选时返回 ``None``，由调用方决定如何处理（schedule_node 报 NO_AVAILABLE_SLOT）。
- 限速不在这里做（executor 阶段统一走 RateLimiter）。
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.agent.protocols import ChosenSlot
from matrix.db.models import Account, Device

# 风险分阈值（与 SDD §3.6 risk_score 维度一致）
RISK_SCORE_MAX = 0.8
SLOT_REASON = "slot_picker.match"


class DefaultSlotPicker:
    """默认槽位选择器：每调用从 session_factory 拿新 session 做一次只读查询。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def choose_slot(
        self,
        *,
        draft: dict,
        persona_config: dict | None = None,
        now: datetime | None = None,
    ) -> ChosenSlot | None:
        """挑一个可用的 (device, account) 组合；无候选返回 ``None``。

        :param draft: 当前草稿（保留供 persona 偏好扩展，当前未使用）
        :param persona_config: persona 配置（保留接口，persona 级别策略未来扩展）
        :param now: 当前时间（用于判断 ``auto_suspend_until``）
        """
        current = now or datetime.utcnow()
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
                    Device.status == "active",
                    Device.deleted_at.is_(None),
                )
            )
            rows = (await session.execute(stmt)).all()

        if not rows:
            return None
        # 同分随机：随机抽一个候选
        account_id, device_id = random.choice(rows)
        return ChosenSlot(
            device_id=device_id,
            account_id=account_id,
            reason=SLOT_REASON,
            scheduled_at=current,
        )


__all__ = ["DefaultSlotPicker", "SLOT_REASON", "RISK_SCORE_MAX"]
