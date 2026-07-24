"""后台告警扫描器：定期跑 ``monitoring.alerts.evaluate_all`` → 写库。

设计要点
--------
- 纯后台任务，不阻塞请求路径
- 复用现有 ``_LazyConfigReader`` 读阈值（``monitoring.heartbeat_threshold_sec`` /
  ``monitoring.risk_score_threshold``），缺省走 ``monitoring/alerts.py`` 默认值
- 复用现有 ``Alert`` ORM（迁移 004 已建）；同 ``(code, subject_id)`` 未 resolved
  的不重复写（去重）
- DB 失败 logger.exception，不让后台循环崩
- ``stop()`` 等待当前一轮跑完才返回，避免半截 INSERT

调用方
------
``matrix.api.app:lifespan`` 在 services 装配后 ``scanner.start()``，
shutdown 阶段 ``await scanner.stop()``。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.db.models import Account, Alert, Device
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class AlertScannerConfig:
    """扫描器运行时参数（也可通过 app_config 表在运行时覆盖）。"""

    poll_interval_sec: float = 60.0
    enable_auto_scan: bool = True
    heartbeat_threshold_sec: int = 300
    risk_score_threshold: float = 0.7


# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

# 与 ``_LazyConfigReader.get`` 同型：async (key, default) -> Any
ConfigReader = Callable[[str, Any], Awaitable[Any]]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class AlertScanner:
    """后台扫描器：周期拉取监测数据 → 生成 alerts → 写库。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        config_reader: ConfigReader,
        config: AlertScannerConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config_reader = config_reader
        self._config = config or AlertScannerConfig()
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()

    # -- 公开 API ----------------------------------------------------------

    def start(self) -> asyncio.Task[None]:
        """启动后台循环；返回 asyncio.Task 便于测试 await。"""
        if self._task is not None and not self._task.done():
            return self._task
        self._stop_event.clear()
        self._task = asyncio.create_task(self.loop(), name="alert-scanner")
        logger.info(
            "alert_scanner.started",
            poll_interval_sec=self._config.poll_interval_sec,
        )
        return self._task

    async def stop(self) -> None:
        """请求停止；等待当前一轮（最多一周期）跑完。"""
        self._stop_event.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=self._config.poll_interval_sec + 5.0)
        except asyncio.TimeoutError:  # pragma: no cover - 极端情况兜底
            logger.warning("alert_scanner.stop timeout; cancelling")
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass
        except Exception:  # pragma: no cover - 后台任务异常不应影响 shutdown
            logger.exception("alert_scanner.stop swallowed exception")
        finally:
            self._task = None
        logger.info("alert_scanner.stopped")

    async def loop(self) -> None:
        """后台主循环：每 poll_interval_sec 跑一次 _scan_once。"""
        # 启动后立刻跑一轮（避免冷启动等 60s 才出第一条）
        try:
            await self._scan_once()
        except Exception:  # pragma: no cover
            logger.exception("alert_scanner initial scan failed")

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.poll_interval_sec,
                )
            except asyncio.TimeoutError:
                # 正常 timeout = 到点跑下一轮
                if not self._config.enable_auto_scan:
                    continue
                try:
                    await self._scan_once()
                except Exception:  # pragma: no cover - 单轮失败不应崩循环
                    logger.exception("alert_scanner tick failed")

    async def _scan_once(self) -> list[Alert]:
        """执行一轮扫描；公开给测试用。返回本轮新写入的 Alert ORM 行。"""
        from matrix.monitoring.alerts import (
            check_device_offline,
            check_risk_blocked,
        )

        thresholds = await self._load_thresholds()
        async with self._session_factory() as session:
            device_payloads = await self._gather_devices(session)
            account_payloads = await self._gather_accounts(session)
            existing_pairs = await self._fetch_existing_pairs(session)

            # 直接调各 check_* 以便传阈值；evaluate_all 不接这些参数
            alerts: list[Alert] = []
            alerts.extend(
                check_device_offline(
                    device_payloads,
                    heartbeat_threshold_sec=thresholds["heartbeat_threshold_sec"],
                )
            )
            alerts.extend(
                check_risk_blocked(
                    account_payloads,
                    risk_threshold=thresholds["risk_score_threshold"],
                )
            )
            written: list[Alert] = []
            # 设备/账号 → 业务 映射（payloads 里带了 business_id），
            # 写告警时按 subject_id（device_id / account_id）回填 alerts.business_id
            subject_business: dict[str, Any] = {
                str(p.get("device_id") or p.get("account_id")): p.get("business_id")
                for p in (*device_payloads, *account_payloads)
            }
            for a in alerts:
                if (a.code, a.subject_id) in existing_pairs:
                    continue
                row = Alert(
                    code=a.code,
                    severity=a.severity,
                    message=a.message,
                    subject_id=a.subject_id,
                    resolved=False,
                    business_id=subject_business.get(a.subject_id or ""),
                )
                session.add(row)
                await session.flush()
                written.append(row)
                existing_pairs.add((a.code, a.subject_id))
                logger.info(
                    "alert_scanner.wrote",
                    code=a.code,
                    severity=a.severity,
                    subject_id=a.subject_id,
                )

            await session.commit()

        # v0.7+：监控类告警只写入 alerts 表，不再重复发到 notifications。
        # 消息页只保留 AI 运营反馈（goal round / note published / collect / agent.alert
        # 运行期异常等），避免告警页和消息页内容重叠。
        return written

    # -- 内部 helpers ------------------------------------------------------

    async def _load_thresholds(self) -> dict[str, Any]:
        """从 app_config 读阈值（缺省走 AlertScannerConfig 默认值）。"""
        cfg = self._config
        return {
            "heartbeat_threshold_sec": await self._config_reader(
                "monitoring.heartbeat_threshold_sec",
                cfg.heartbeat_threshold_sec,
            ),
            "risk_score_threshold": float(
                await self._config_reader(
                    "monitoring.risk_score_threshold",
                    cfg.risk_score_threshold,
                )
            ),
        }

    @staticmethod
    async def _gather_devices(session: AsyncSession) -> list[dict[str, Any]]:
        """把 devices 表行转成 check_device_offline 期望的 payload 形状。"""
        stmt = select(Device).where(
            Device.deleted_at.is_(None),
            Device.status == "active",
        )
        rows = (await session.execute(stmt)).scalars().all()
        now = datetime.now(timezone.utc)
        payloads: list[dict[str, Any]] = []
        for d in rows:
            age = 0.0
            if d.last_heartbeat is not None:
                try:
                    age = max(0.0, (now - d.last_heartbeat).total_seconds())
                except Exception:  # pragma: no cover
                    age = 0.0
            payloads.append(
                {
                    "device_id": str(d.id),
                    "last_heartbeat_age_sec": age,
                    "business_id": d.business_id,
                }
            )
        return payloads

    @staticmethod
    async def _gather_accounts(session: AsyncSession) -> list[dict[str, Any]]:
        """把 accounts 表行转成 check_risk_blocked 期望的 payload 形状。"""
        stmt = select(Account).where(Account.deleted_at.is_(None))
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "account_id": str(a.id),
                "risk_score": float(a.risk_score or 0),
                "business_id": a.business_id,
            }
            for a in rows
        ]

    @staticmethod
    async def _fetch_existing_pairs(session: AsyncSession) -> set[tuple[str, str | None]]:
        """取所有 unresolved 的 (code, subject_id) — 用于本轮去重。"""
        stmt = select(Alert.code, Alert.subject_id).where(
            Alert.resolved == False  # noqa: E712
        )
        return {(r[0], r[1]) for r in (await session.execute(stmt)).all()}


__all__ = ["AlertScanner", "AlertScannerConfig"]