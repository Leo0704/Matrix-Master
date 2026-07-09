"""账号-设备亲和 + 设备掉线任务暂停 / 恢复（SDD §3.5.3）。

- 账号 → 设备绑定存储在 ``accounts.device_id``
- 设备掉线时：所有 ``status = 'pending'`` 且 ``device_id = 离线设备`` 的 task 保持 pending 等待恢复
- 设备恢复时：pending task 按 ``scheduled_at`` 续跑

设计：``AccountBinding`` 不直接修改调度器状态，只在 DB 标记
``tasks.attempts`` / ``last_error``，由 scheduler 自然在 ``scheduled_at`` 到点时拉走。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Account, Device, Task

logger = get_logger(__name__)


@dataclass
class BindingResult:
    """绑定操作结果（用于 API 响应 / 日志）。"""

    account_id: UUID
    device_id: UUID
    bound: bool
    reason: Optional[str] = None


class AccountBindingError(RuntimeError):
    """账号-设备绑定错误。"""


class AccountBinding:
    """账号-设备亲和管理 + 设备掉线/恢复时的 task 调度协调。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # 绑定 / 解绑
    # ------------------------------------------------------------------

    async def bind(self, account_id: UUID, device_id: UUID) -> BindingResult:
        """绑定账号到设备。

        Raises:
            AccountBindingError: 设备不存在 / 账号不存在 / 设备已禁用
        """
        device = await self.session.get(Device, device_id)
        if device is None or device.deleted_at is not None:
            raise AccountBindingError(f"device {device_id} not found")
        if device.status == "disabled":
            raise AccountBindingError(f"device {device_id} is disabled")

        account = await self.session.get(Account, account_id)
        if account is None or account.deleted_at is not None:
            raise AccountBindingError(f"account {account_id} not found")

        account.device_id = device_id
        account.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        logger.info(
            "account.bound.to_device",
            account_id=str(account_id),
            device_id=str(device_id),
        )
        return BindingResult(account_id=account_id, device_id=device_id, bound=True)

    async def unbind(self, account_id: UUID) -> bool:
        """解绑账号（``accounts.device_id = NULL``）。"""
        account = await self.session.get(Account, account_id)
        if account is None:
            return False
        account.device_id = None
        account.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return True

    async def get_device_for_account(self, account_id: UUID) -> Optional[Device]:
        """获取账号绑定的设备（未删除、未禁用）。"""
        account = await self.session.get(Account, account_id)
        if account is None or account.device_id is None:
            return None
        device = await self.session.get(Device, account.device_id)
        if device is None or device.deleted_at is not None:
            return None
        return device

    async def list_accounts_for_device(
        self,
        device_id: UUID,
        *,
        include_disabled: bool = False,
    ) -> list[Account]:
        """列出绑定到该设备的所有账号。"""
        stmt = select(Account).where(
            Account.device_id == device_id,
            Account.deleted_at.is_(None),
        )
        if not include_disabled:
            stmt = stmt.where(Account.status != "disabled")
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # 设备掉线 / 恢复
    # ------------------------------------------------------------------

    async def pause_tasks_for_offline_device(self, device_id: UUID) -> int:
        """设备掉线时调用：标记该设备所有 pending task 为 paused（保持 pending + 写 last_error）。

        实际不修改 status（仍是 pending），由调度器继续尝试；尝试时若设备仍离线
        则 ``executor`` 报 DEVICE_OFFLINE，调度器会把 task 重新落 pending。
        这里只追加 last_error 让运营者可见原因。

        Returns:
            受影响的 task 数
        """
        result = await self.session.execute(
            update(Task)
            .where(
                Task.device_id == device_id,
                Task.status == "pending",
            )
            .values(
                last_error={
                    "code": "DEVICE_OFFLINE",
                    "message": "device offline; task waiting for recovery",
                },
                updated_at=datetime.now(timezone.utc),
            )
        )
        count = int(result.rowcount or 0)
        logger.info(
            "tasks.paused.offline_device",
            device_id=str(device_id),
            count=count,
        )
        return count

    async def resume_tasks_for_recovered_device(self, device_id: UUID) -> int:
        """设备恢复时调用：清掉 pending task 上的 DEVICE_OFFLINE 标记，让调度器按 scheduled_at 续跑。

        Returns:
            受影响的 task 数
        """
        # 用 JSONB 过滤避免更新无关行
        result = await self.session.execute(
            update(Task)
            .where(
                Task.device_id == device_id,
                Task.status == "pending",
                Task.last_error["code"].astext == "DEVICE_OFFLINE",
            )
            .values(
                last_error=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        count = int(result.rowcount or 0)
        logger.info(
            "tasks.resumed.recovered_device",
            device_id=str(device_id),
            count=count,
        )
        return count


__all__ = [
    "AccountBinding",
    "AccountBindingError",
    "BindingResult",
]
