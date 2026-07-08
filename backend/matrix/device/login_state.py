"""登录态监控（SDD §3.5.4）。

APK 在心跳中上报 XHS 登录状态；本服务负责：
- 记录 ``account_login_sessions`` 表
- 掉线时通过监控子系统告警
- 恢复时清掉告警

设计：纯被动接收（APK 推），不主动探活（探活在 APK 端做）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Account, AccountLoginSession

logger = logging.getLogger(__name__)


# 与 AccountLoginSession.result CHECK constraint 对齐
VALID_RESULTS = ("success", "failed", "captcha", "logout", "expired")


@dataclass
class LoginStateReport:
    """APK 上报的单次登录态变更。"""

    account_id: UUID
    device_id: UUID
    result: str  # "success" / "failed" / "captcha" / "logout" / "expired"
    risk_signal: Optional[str] = None
    error_message: Optional[str] = None


class LoginStateError(ValueError):
    """登录态数据非法。"""


class LoginStateMonitor:
    """登录态变化记录 + 告警联动。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        alerter: Optional[object] = None,
    ) -> None:
        """``alerter`` 是可选的 ``matrix.monitoring.alerts.Alert`` sink（注入以便测试 mock）。"""
        self.session = session
        self.alerter = alerter

    # ------------------------------------------------------------------
    # 上报
    # ------------------------------------------------------------------

    async def report(self, report: LoginStateReport) -> AccountLoginSession:
        """记录一次登录态变化。"""
        if report.result not in VALID_RESULTS:
            raise LoginStateError(
                f"invalid result {report.result!r}; must be one of {VALID_RESULTS}"
            )

        record = AccountLoginSession(
            account_id=report.account_id,
            device_id=report.device_id,
            ts=datetime.now(timezone.utc),
            result=report.result,
            risk_signal=report.risk_signal,
            error_message=report.error_message,
        )
        self.session.add(record)
        await self.session.flush()

        # 同步 account.last_active（仅 success）
        if report.result == "success":
            account = await self.session.get(Account, report.account_id)
            if account is not None:
                account.last_active = record.ts
                account.status = "active"
                account.updated_at = record.ts
                await self.session.flush()

        # 失败 / 风控 / 登出时告警
        if report.result in ("failed", "captcha", "logout", "expired"):
            await self._alert(report)

        return record

    async def get_recent(self, account_id: UUID, limit: int = 10) -> list[AccountLoginSession]:
        """获取账号最近的登录态记录。"""
        result = await self.session.execute(
            select(AccountLoginSession)
            .where(AccountLoginSession.account_id == account_id)
            .order_by(AccountLoginSession.ts.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def is_logged_in(self, account_id: UUID, window_minutes: int = 30) -> bool:
        """近 ``window_minutes`` 分钟内是否有过 success 记录。"""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        result = await self.session.execute(
            select(AccountLoginSession)
            .where(
                AccountLoginSession.account_id == account_id,
                AccountLoginSession.ts >= cutoff,
                AccountLoginSession.result == "success",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _alert(self, report: LoginStateReport) -> None:
        """通过监控子系统告警。alerter 未注入时仅打 log。"""
        if self.alerter is None:
            logger.warning(
                "login state degraded",
                extra={
                    "account_id": str(report.account_id),
                    "device_id": str(report.device_id),
                    "result": report.result,
                    "risk_signal": report.risk_signal,
                },
            )
            return
        try:
            alert = {
                "type": "login_state",
                "result": report.result,
                "account_id": str(report.account_id),
                "device_id": str(report.device_id),
                "risk_signal": report.risk_signal,
                "error_message": report.error_message,
            }
            fire = getattr(self.alerter, "fire", None)
            if callable(fire):
                fire(alert)
        except Exception:  # pragma: no cover - 告警失败不影响主流程
            logger.exception("failed to fire login state alert")


__all__ = [
    "LoginStateMonitor",
    "LoginStateReport",
    "LoginStateError",
    "VALID_RESULTS",
]
