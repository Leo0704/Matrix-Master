"""告警判定函数（基于 monitoring-runbook §3 的处理逻辑）。

约定：每个函数返回 ``list[Alert]``；``Alert`` 仅包含触发的告警。
调用方负责把 ``Alert`` 投递到通知渠道（邮件 / 飞书 / Webhook 等）。

设计要点：
- 纯函数：不直接读 Prometheus client state，参数化输入
- 易测试：每个判定都接收原始值
- 覆盖 runbook §3 中已接入扫描器的条目（DEVICE_OFFLINE / RISK_BLOCKED）
- v0.7+：message 必须是用户看得懂的人话，禁止把原始字段/代码怼脸。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Alert:
    """单条告警实例。"""

    code: str  # e.g. 'DEVICE_OFFLINE'
    severity: str  # 'critical' | 'warning' | 'info'
    message: str
    subject_id: str | None = None  # device_id / account_id / run_id 等


# ---------------------------------------------------------------------------
# §3.1 DEVICE_OFFLINE
# ---------------------------------------------------------------------------


def check_device_offline(
    devices: Iterable[dict],
    *,
    heartbeat_threshold_sec: int = 300,
) -> list[Alert]:
    """单设备心跳超时 > 阈值 → DEVICE_OFFLINE。

    Args:
        devices: 每项含 ``device_id`` / ``last_heartbeat_age_sec`` / ``offline``。
        heartbeat_threshold_sec: 心跳超时阈值，默认 300s（runbook §2.1）。
    """
    alerts: list[Alert] = []
    for d in devices:
        age = d.get("last_heartbeat_age_sec", 0) or 0
        if age > heartbeat_threshold_sec:
            minutes = round(age / 60)
            alerts.append(
                Alert(
                    code="DEVICE_OFFLINE",
                    severity="critical",
                    message=(
                        f"设备已经超过 {minutes} 分钟没上报心跳，"
                        f"请检查手机网络、电量和 Matrix 应用是否在后台运行。"
                    ),
                    subject_id=d.get("device_id"),
                )
            )
    return alerts


# ---------------------------------------------------------------------------
# §3.2 RISK_BLOCKED
# ---------------------------------------------------------------------------


def check_risk_blocked(
    accounts: Iterable[dict],
    *,
    risk_threshold: float = 0.7,
) -> list[Alert]:
    """账号 ``risk_score`` 超过阈值 → RISK_BLOCKED。

    按 runbook：触发后自动暂停该账号，等待人工确认。
    """
    alerts: list[Alert] = []
    for a in accounts:
        score = a.get("risk_score", 0.0) or 0.0
        if score > risk_threshold:
            alerts.append(
                Alert(
                    code="RISK_BLOCKED",
                    severity="critical",
                    message=(
                        f"账号风险评分 {score:.2f} 超过阈值，"
                        f"系统已自动暂停该账号，请人工确认后再恢复。"
                    ),
                    subject_id=a.get("account_id"),
                )
            )
    return alerts
