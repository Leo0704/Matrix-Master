"""RoundSlotAllocator 单元测试。

覆盖：
- allocate() 的 stagger 时间数学
- style_hint 轮换（按设备下标）
- 活跃窗外 → TimeOutOfWindowError
- 找不到设备 → []
- 设备数 < n → 返回部分
- count_active_devices() 计数
- is_slot_valid() 二次校验（active/inactive）
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from matrix.agent.protocols import ChosenSlot
from matrix.scheduler.round_slot_allocator import (
    DefaultRoundSlotAllocator,
    STYLE_ROTATION,
    TimeOutOfWindowError,
)


# 全天活跃窗，避开 OUT_OF_ACTIVE_WINDOW 干扰
PERSONA_CFG_ALL_DAY = {"active_window": {"start": 0, "end": 24}}
# 默认窗 09:00-23:00
PERSONA_CFG_DEFAULT = {"active_window": {"start": 9, "end": 23}}


def _make_session_factory(rows: list[tuple] | None = None):
    """构造 fake session_factory：session.execute(stmt) → async 返回 result。

    result.all() → rows 副本；result.first() → rows[0] or None。
    """
    rows = rows or []

    @asynccontextmanager
    async def _factory():
        session = MagicMock()
        result = MagicMock()
        result.all = MagicMock(return_value=list(rows))
        result.first = MagicMock(return_value=rows[0] if rows else None)
        session.execute = AsyncMock(return_value=result)
        yield session

    return _factory


# ---------------------------------------------------------------------------
# allocate: stagger 时间 + style_hint
# ---------------------------------------------------------------------------


class TestAllocateStagger:
    @pytest.mark.asyncio
    async def test_stagger_minutes(self):
        """base=10:00, n=3, stagger=15min → 10:00 / 10:15 / 10:30。"""
        base = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
        rows = [(uuid4(), uuid4()) for _ in range(3)]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))

        slots = await alloc.allocate(
            brief={}, n=3, base_time=base, stagger_minutes=15,
            persona_config=PERSONA_CFG_ALL_DAY,
        )
        assert len(slots) == 3
        assert slots[0].scheduled_at == base
        assert slots[1].scheduled_at == base + timedelta(minutes=15)
        assert slots[2].scheduled_at == base + timedelta(minutes=30)

    @pytest.mark.asyncio
    async def test_style_hint_cycles(self):
        """5 台设备 → 5 个不同 style（轮换 8 种列表的前 5）。"""
        base = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
        rows = [(uuid4(), uuid4()) for _ in range(5)]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))

        slots = await alloc.allocate(
            brief={}, n=5, base_time=base, persona_config=PERSONA_CFG_ALL_DAY,
        )
        hints = [s.style_hint for s in slots]
        assert hints == list(STYLE_ROTATION[:5])
        # 5 个都不重样
        assert len(set(hints)) == 5

    @pytest.mark.asyncio
    async def test_style_hint_cycles_around(self):
        """10 台设备 → 8 种风格循环 1 次 + 前 2 种。"""
        base = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
        rows = [(uuid4(), uuid4()) for _ in range(10)]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))

        slots = await alloc.allocate(
            brief={}, n=10, base_time=base, persona_config=PERSONA_CFG_ALL_DAY,
        )
        hints = [s.style_hint for s in slots]
        # 10 个 hint：前 8 + 头 2
        assert hints == list(STYLE_ROTATION) + list(STYLE_ROTATION[:2])

    @pytest.mark.asyncio
    async def test_default_stagger_is_15_minutes(self):
        """不传 stagger_minutes → 默认 15min 间隔。"""
        base = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
        rows = [(uuid4(), uuid4()) for _ in range(2)]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))

        slots = await alloc.allocate(
            brief={}, n=2, base_time=base, persona_config=PERSONA_CFG_ALL_DAY,
        )
        assert slots[1].scheduled_at - slots[0].scheduled_at == timedelta(minutes=15)

    @pytest.mark.asyncio
    async def test_returns_chosen_slots(self):
        """返回 ChosenSlot 实例，带 reason / style_hint。"""
        base = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
        rows = [(uuid4(), uuid4())]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))

        slots = await alloc.allocate(
            brief={}, n=1, base_time=base, persona_config=PERSONA_CFG_ALL_DAY,
        )
        assert len(slots) == 1
        assert isinstance(slots[0], ChosenSlot)
        assert slots[0].reason == "round_allocator.match"
        assert slots[0].style_hint == STYLE_ROTATION[0]


# ---------------------------------------------------------------------------
# allocate: 边界
# ---------------------------------------------------------------------------


class TestAllocateEdgeCases:
    @pytest.mark.asyncio
    async def test_no_devices_returns_empty(self):
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows=[]))
        slots = await alloc.allocate(brief={}, n=5, persona_config=PERSONA_CFG_ALL_DAY)
        assert slots == []

    @pytest.mark.asyncio
    async def test_fewer_devices_than_n_returns_partial(self):
        """要 5 台但只找到 3 台 → 返 3 个 slot。"""
        rows = [(uuid4(), uuid4()) for _ in range(3)]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))
        slots = await alloc.allocate(
            brief={}, n=5, persona_config=PERSONA_CFG_ALL_DAY,
        )
        assert len(slots) == 3

    @pytest.mark.asyncio
    async def test_n_zero_returns_empty(self):
        alloc = DefaultRoundSlotAllocator(_make_session_factory([(uuid4(), uuid4())]))
        slots = await alloc.allocate(brief={}, n=0, persona_config=PERSONA_CFG_ALL_DAY)
        assert slots == []

    @pytest.mark.asyncio
    async def test_negative_n_returns_empty(self):
        alloc = DefaultRoundSlotAllocator(_make_session_factory([(uuid4(), uuid4())]))
        slots = await alloc.allocate(brief={}, n=-1, persona_config=PERSONA_CFG_ALL_DAY)
        assert slots == []


# ---------------------------------------------------------------------------
# allocate: 活跃窗失败
# ---------------------------------------------------------------------------


class TestAllocateActiveWindow:
    @pytest.mark.asyncio
    async def test_raises_when_stagger_pushes_out_of_window(self):
        """base=14:50 UTC = 22:50 Shanghai（hour 22 在窗内 9-23），n=3, stagger=15min
        → slot 0=22:50（窗内）/ slot 1=23:05 Shanghai（hour 23，窗外）/ slot 2=23:20（窗外）。

        第一个落在窗外的 slot 是 index 1，allocate 在循环中按顺序检测，
        报 slot 1 错。
        """
        base = datetime(2026, 7, 13, 14, 50, tzinfo=timezone.utc)
        rows = [(uuid4(), uuid4()) for _ in range(3)]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))

        with pytest.raises(TimeOutOfWindowError) as excinfo:
            await alloc.allocate(
                brief={}, n=3, base_time=base, stagger_minutes=15,
                persona_config=PERSONA_CFG_DEFAULT,
            )
        assert "slot 1" in str(excinfo.value)
        assert "outside active window" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_all_day_window_accepts_any_time(self):
        """active_window 0-24 → 任何时间都接受。"""
        base = datetime(2026, 7, 13, 22, 50, tzinfo=timezone.utc)
        rows = [(uuid4(), uuid4()) for _ in range(3)]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))

        slots = await alloc.allocate(
            brief={}, n=3, base_time=base, stagger_minutes=15,
            persona_config=PERSONA_CFG_ALL_DAY,
        )
        assert len(slots) == 3

    @pytest.mark.asyncio
    async def test_naive_datetime_treated_as_utc(self):
        """naive datetime → 视为 UTC（缺省时区）。"""
        base_naive = datetime(2026, 7, 13, 10, 0)  # no tzinfo
        rows = [(uuid4(), uuid4())]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))

        slots = await alloc.allocate(
            brief={}, n=1, base_time=base_naive, persona_config=PERSONA_CFG_ALL_DAY,
        )
        assert len(slots) == 1
        # scheduled_at 应该是 UTC aware
        assert slots[0].scheduled_at.tzinfo is not None


# ---------------------------------------------------------------------------
# count_active_devices
# ---------------------------------------------------------------------------


class TestCountActiveDevices:
    @pytest.mark.asyncio
    async def test_counts_rows(self):
        rows = [(uuid4(), uuid4()) for _ in range(7)]
        alloc = DefaultRoundSlotAllocator(_make_session_factory(rows))
        assert await alloc.count_active_devices() == 7

    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        alloc = DefaultRoundSlotAllocator(_make_session_factory([]))
        assert await alloc.count_active_devices() == 0


# ---------------------------------------------------------------------------
# is_slot_valid
# ---------------------------------------------------------------------------


class TestIsSlotValid:
    @pytest.mark.asyncio
    async def test_valid_when_row_found(self):
        device_id = uuid4()
        account_id = uuid4()
        # session.execute(stmt).first() → (account_id, device_id) 元组 = 找到
        factory = _make_session_factory(rows=[(account_id, device_id)])
        alloc = DefaultRoundSlotAllocator(factory)
        ok = await alloc.is_slot_valid(
            device_id=device_id, account_id=account_id
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_invalid_when_no_row(self):
        factory = _make_session_factory(rows=[])  # .first() 返 None
        alloc = DefaultRoundSlotAllocator(factory)
        ok = await alloc.is_slot_valid(
            device_id=uuid4(), account_id=uuid4()
        )
        assert ok is False


# ---------------------------------------------------------------------------
# STYLE_ROTATION 自身一致性
# ---------------------------------------------------------------------------


def test_style_rotation_has_8_unique_entries():
    assert len(STYLE_ROTATION) == 8
    assert len(set(STYLE_ROTATION)) == 8  # 8 个都不一样
    for s in STYLE_ROTATION:
        assert isinstance(s, str) and s  # 非空字符串
