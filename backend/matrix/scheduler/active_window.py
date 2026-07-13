"""活跃窗检查（按 mcp-tools-notes.md §1）。

设备本地时区 09:00-23:00 才允许下发操作。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_START_HOUR = 9
DEFAULT_END_HOUR = 23


def is_in_active_window(
    now: datetime,
    persona_config: dict | None = None,
    device_tz: str | None = None,
) -> bool:
    """判断 ``now`` 是否在活跃窗内。

    :param now: 时间戳（naive 时视为设备本地时间；aware 时按其 tzinfo 转换后再判断）。
    :param persona_config: 可选 ``{"active_window": {"start": 9, "end": 23}}`` 覆盖默认窗口。
    :param device_tz: 设备本地时区名（如 ``"Asia/Shanghai"``、``"America/Los_Angeles"``）。
        仅当 ``now`` 为 naive 时使用。``None`` 时默认 ``"Asia/Shanghai"``（项目当前全在国内设备）。

    TODO：海外设备场景需让调用方从 device 表读 tz 字段传入；当前所有设备假定在东八区。
    """
    effective_tz = device_tz or "Asia/Shanghai"
    config = persona_config or {}
    window = config.get("active_window", {})
    start = int(window.get("start", DEFAULT_START_HOUR))
    end = int(window.get("end", DEFAULT_END_HOUR))

    local = _to_local(now, effective_tz)
    hour = local.hour
    # 起始包含，结束不包含（[start, end)）
    return start <= hour < end


def _to_local(now: datetime, device_tz: str) -> datetime:
    if now.tzinfo is not None:
        # aware datetime: 转到设备本地时区再判 hour
        try:
            return now.astimezone(ZoneInfo(device_tz))
        except ZoneInfoNotFoundError:
            return now
    # naive datetime: 视为设备本地时间，仅附加 tzinfo
    try:
        tz = ZoneInfo(device_tz)
    except ZoneInfoNotFoundError:
        return now
    return now.replace(tzinfo=tz)
