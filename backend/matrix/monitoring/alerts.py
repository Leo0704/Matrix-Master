"""告警判定函数（基于 monitoring-runbook §3 的处理逻辑）。

约定：每个函数返回 ``list[Alert]``；``Alert`` 仅包含触发的告警。
调用方负责把 ``Alert`` 投递给通知渠道（邮件 / 飞书 / Webhook 等）。

设计要点：
- 纯函数：不直接读 Prometheus client state，参数化输入
- 易测试：每个判定都接收原始值
- 覆盖 runbook §3 全部条目（DEVICE_OFFLINE / RISK_BLOCKED /
  SELECTOR_NOT_FOUND / TAILSCALE_DERP_LOST / POSTGRES_DISK_FULL）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


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
            alerts.append(
                Alert(
                    code="DEVICE_OFFLINE",
                    severity="critical",
                    message=(
                        f"Device {d.get('device_id')} heartbeat age "
                        f"{age}s > {heartbeat_threshold_sec}s"
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
                        f"Account {a.get('account_id')} risk_score={score:.2f} "
                        f"> {risk_threshold}; auto-paused"
                    ),
                    subject_id=a.get("account_id"),
                )
            )
    return alerts


# ---------------------------------------------------------------------------
# §3.3 SELECTOR_NOT_FOUND
# ---------------------------------------------------------------------------


def check_selector_not_found(
    events: Iterable[dict],
    *,
    window_min: int = 5,
    threshold: int = 3,
) -> list[Alert]:
    """窗口内 selector 失败次数 > 阈值 → SELECTOR_NOT_FOUND。

    Args:
        events: 每项含 ``device_id`` / ``tool`` / ``ts``。
        window_min: 滚动窗口（分钟）。
        threshold: 触发告警的次数阈值。
    """
    counts: dict[tuple[str, str], int] = {}
    for ev in events:
        key = (ev.get("device_id", ""), ev.get("tool", ""))
        counts[key] = counts.get(key, 0) + 1

    alerts: list[Alert] = []
    for (device_id, tool), n in counts.items():
        if n >= threshold:
            alerts.append(
                Alert(
                    code="SELECTOR_NOT_FOUND",
                    severity="warning",
                    message=(
                        f"Selector failed {n}x in {window_min}min "
                        f"on device={device_id} tool={tool}; triggering VLM fallback"
                    ),
                    subject_id=device_id,
                )
            )
    return alerts


# ---------------------------------------------------------------------------
# §3.4 TAILSCALE_DERP_LOST
# ---------------------------------------------------------------------------


def check_tailscale_derp_lost(
    derp_results: Iterable[dict],
) -> list[Alert]:
    """DERP 区域不可达 → TAILSCALE_DERP_LOST。

    Args:
        derp_results: 每项含 ``region`` / ``reachable``。
    """
    alerts: list[Alert] = []
    for r in derp_results:
        if not r.get("reachable", False):
            alerts.append(
                Alert(
                    code="TAILSCALE_DERP_LOST",
                    severity="critical",
                    message=(
                        f"DERP region {r.get('region')} unreachable; "
                        "check Headscale / DERP container"
                    ),
                    subject_id=r.get("region"),
                )
            )
    return alerts


# ---------------------------------------------------------------------------
# §3.5 POSTGRES_DISK_FULL
# ---------------------------------------------------------------------------


def check_postgres_disk_full(
    disk_usage_percent: float,
    *,
    threshold: float = 80.0,
) -> list[Alert]:
    """DB 磁盘使用率超阈值 → POSTGRES_DISK_FULL。"""
    if disk_usage_percent > threshold:
        return [
            Alert(
                code="POSTGRES_DISK_FULL",
                severity="warning",
                message=(
                    f"Postgres disk usage {disk_usage_percent:.1f}% > "
                    f"{threshold:.1f}%; cleanup old checkpoints/heartbeats"
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# 聚合入口：跑全部检查并合并
# ---------------------------------------------------------------------------


def evaluate_all(
    *,
    devices: Sequence[dict] | None = None,
    accounts: Sequence[dict] | None = None,
    selector_events: Sequence[dict] | None = None,
    derp_results: Sequence[dict] | None = None,
    disk_usage_percent: float = 0.0,
) -> list[Alert]:
    """跑全部告警检查，返回合并后的列表。

    任一参数缺省即跳过对应检查。"""
    alerts: list[Alert] = []
    if devices is not None:
        alerts.extend(check_device_offline(devices))
    if accounts is not None:
        alerts.extend(check_risk_blocked(accounts))
    if selector_events is not None:
        alerts.extend(check_selector_not_found(selector_events))
    if derp_results is not None:
        alerts.extend(check_tailscale_derp_lost(derp_results))
    alerts.extend(check_postgres_disk_full(disk_usage_percent))
    return alerts
