"""INTERACT 节点策略层（Phase 2b #5 + A）。

两条老板场景：

1. **#5 平台去重** —— 同一个账号对同一篇笔记不能反复点赞/评论。
   之前只查 ``interactions`` 表，但 24h 跨 plan 也算"已经做过"。
   做法：进 device 之前先查 ``interactions`` 表，命中就直接跳过 + 标
   ``DEDUPED`` 返回（不算 failed 也不算 succeeded）。

2. **A 互动自适应** —— 自己账号风险高时收敛动作：
   - ``risk_score >= 0.85`` → 整条跳过（``RISK_TOO_HIGH``）
   - ``risk_score >= 0.7`` → 只 like 不 comment（``RISK_COMMENT_BLOCKED``）
   - ``status in {banned, suspended, disabled}`` → 整条跳过（``ACCOUNT_DISABLED``）

两个检查都通过 ``InteractPolicy.should_skip(...)`` 统一返回原因，
interact_node 拿到 reason 后写进 details。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from matrix.db.models import Interaction, Note
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PolicyDecision:
    """interact 节点去重 + 自适应决策。"""

    skip: bool
    reason: str = ""  # '' 表示不 skip
    message: str = ""


# 默认阈值（class-level，方便覆盖；后续可读 app_config）
RISK_COMMENT_BLOCKED = 0.7
RISK_SKIP_ALL = 0.85
DISABLED_STATUSES = frozenset({"banned", "suspended", "disabled"})


class InteractPolicy:
    """``session_factory`` + thresholds 一把搞定。"""

    def __init__(
        self,
        session_factory: Any,
        *,
        risk_comment_blocked: float = RISK_COMMENT_BLOCKED,
        risk_skip_all: float = RISK_SKIP_ALL,
        disabled_statuses: frozenset[str] = DISABLED_STATUSES,
    ) -> None:
        self._factory = session_factory
        self.risk_comment_blocked = risk_comment_blocked
        self.risk_skip_all = risk_skip_all
        self.disabled_statuses = disabled_statuses

    async def should_skip(
        self,
        *,
        account_id: Any,
        target_note_id: str,
        kind: str,
    ) -> PolicyDecision:
        """综合查重 + 自适应风险，返回是否跳过 + 原因。

        ``account_id`` 是我们这侧（操作者）的账号；``target_note_id`` 是平台
        笔记 id（XHS 那种字符串）。

        检查顺序：
        1) 账号状态 banned/suspended/disabled → 整条 skip
        2) risk_score 过高 → 整条 skip 或只禁 comment
        3) 同一 (account, target_note_id, type) 已写过 interactions → 整条 skip
        """
        if self._factory is None:
            return PolicyDecision(skip=False)

        try:
            async with self._factory() as session:
                # ---- 1) 账号状态检查 ----
                from matrix.db.models import Account

                acct = await session.get(Account, account_id)
                if acct is None:
                    return PolicyDecision(
                        skip=True,
                        reason="ACCOUNT_NOT_FOUND",
                        message=f"actor account {account_id} not in DB",
                    )
                if acct.status in self.disabled_statuses:
                    return PolicyDecision(
                        skip=True,
                        reason="ACCOUNT_DISABLED",
                        message=f"actor account {acct.handle} status={acct.status}",
                    )
                # ---- 2) 风险自适应 ----
                risk = float(getattr(acct, "risk_score", 0) or 0)
                if risk >= self.risk_skip_all:
                    return PolicyDecision(
                        skip=True,
                        reason="RISK_TOO_HIGH",
                        message=f"actor risk_score={risk} >= {self.risk_skip_all}",
                    )
                if (
                    kind == "comment"
                    and risk >= self.risk_comment_blocked
                ):
                    return PolicyDecision(
                        skip=True,
                        reason="RISK_COMMENT_BLOCKED",
                        message=(
                            f"actor risk_score={risk} >= "
                            f"{self.risk_comment_blocked}, comment blocked"
                        ),
                    )
                # ---- 3) 平台去重 ----
                # target_note_id 是平台 id（如 XHS note id），要先把本地 Note
                # 找到；找不到说明本机从没索引过这条 → 当作新笔记，不去重
                note_uuid = None
                if target_note_id:
                    # 平台 id 在 Note.platform_note_id 里
                    row = (
                        await session.execute(
                            select(Note.id).where(
                                Note.platform_note_id == target_note_id,
                                Note.deleted_at.is_(None),
                            )
                        )
                    ).scalars().first()
                    note_uuid = row
                if note_uuid is not None:
                    exists = (
                        await session.execute(
                            select(Interaction.id).where(
                                Interaction.account_id == account_id,
                                Interaction.target_note_id == note_uuid,
                                Interaction.type == kind,
                            )
                        )
                    ).scalars().first()
                    if exists is not None:
                        return PolicyDecision(
                            skip=True,
                            reason="DEDUPED",
                            message=(
                                f"already {kind} on target_note_id="
                                f"{target_note_id}"
                            ),
                        )
                return PolicyDecision(skip=False)
        except Exception:
            # 任何 DB 异常都不能挡主流程；记 warning 走原路
            logger.exception("interact_policy.check_failed")
            return PolicyDecision(skip=False)


__all__ = ["InteractPolicy", "PolicyDecision"]
