"""matrix.db 层基础测试。

不连真实 DB：engine 用 mock factory 验证 URL 与配置；session context 用 mock；
模型用纯属性赋值验证 ORM 映射正确（不需要 DDL）。
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# engine 测试
# ---------------------------------------------------------------------------


def test_get_database_url_default_uses_asyncpg():
    """默认 URL 必须用 asyncpg 驱动。"""
    from matrix.db.engine import get_database_url

    url = get_database_url()
    assert url.startswith("postgresql+asyncpg://"), f"got {url!r}"


def test_get_database_url_override_wins():
    """显式传参应优先于环境变量。"""
    from matrix.db.engine import get_database_url

    url = get_database_url("postgresql+asyncpg://u:p@h:5432/d")
    assert "u:p@h:5432/d" in url


def test_get_database_url_reads_env(monkeypatch):
    """DATABASE_URL 环境变量被读取。"""
    from matrix.db import engine as engine_module

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://env:p@db/m")
    assert "env:p@db/m" in engine_module.get_database_url()


def test_create_engine_returns_async_engine():
    """create_engine 返回 AsyncEngine 实例。"""
    from sqlalchemy.ext.asyncio import AsyncEngine

    from matrix.db.engine import create_engine

    eng = create_engine("postgresql+asyncpg://u:p@h:5432/d")
    try:
        assert isinstance(eng, AsyncEngine)
        assert eng.url.drivername == "postgresql+asyncpg"
    finally:
        # 立即 dispose 避免 dangling 资源
        import asyncio

        asyncio.run(eng.dispose())


# ---------------------------------------------------------------------------
# session 测试
# ---------------------------------------------------------------------------


def test_get_session_factory_lazy_init():
    """sessionmaker 首次调用时创建。"""
    from matrix.db.session import (
        get_session_factory,
        set_engine,
    )

    # 用 mock engine 替换，避免真实连接
    mock_engine = MagicMock()
    set_engine(mock_engine)
    factory = get_session_factory()
    assert factory is not None
    # 二次调用应返回同一实例
    assert get_session_factory() is factory


@pytest.mark.asyncio
async def test_get_session_commits_on_success():
    """正常退出时 session.commit() 会被调用。"""
    from matrix.db.session import get_session, set_engine

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)
    set_engine(MagicMock())

    with patch("matrix.db.session.get_session_factory", return_value=mock_factory):
        async with get_session() as s:
            assert s is mock_session

    mock_session.commit.assert_awaited_once()
    mock_session.rollback.assert_not_called()
    mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_rolls_back_on_exception():
    """异常时 rollback 而非 commit。"""
    from matrix.db.session import get_session, set_engine

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)
    set_engine(MagicMock())

    with patch("matrix.db.session.get_session_factory", return_value=mock_factory):
        with pytest.raises(RuntimeError, match="commit failed"):
            async with get_session():
                pass

    mock_session.rollback.assert_awaited_once()
    mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_always_closes():
    """无论是否异常，session.close() 总会调用。"""
    from matrix.db.session import get_session, set_engine

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)
    set_engine(MagicMock())

    with patch("matrix.db.session.get_session_factory", return_value=mock_factory):
        async with get_session():
            pass

    mock_session.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# models 测试
# ---------------------------------------------------------------------------


# 全部 25 张表（外加 Base），按 schema.sql 顺序 + 004 migration 加的 alerts
# + 008 migration 加的 goal_rounds
# （schema.sql 尚未同步；migration 是事实源）
EXPECTED_TABLES = [
    "devices",
    "device_hmac_keys",
    "device_heartbeats",
    "accounts",
    "account_login_sessions",
    "risk_signals",
    "personas",
    "topics",
    "rules",
    "notes",
    "note_metrics",
    "kb_documents",
    "kb_chunks",
    "goals",
    "goal_rounds",
    "plans",
    "tasks",
    "agent_runs",
    "agent_checkpoints",
    "interactions",
    "comments",
    "audit_logs",
    "alerts",
    "daily_counters",
    "app_config",
]


def test_all_tableless_imported():
    """所有 22 个模型都能 import。"""
    from matrix.db import models

    for name in [
        "Device",
        "DeviceHmacKey",
        "DeviceHeartbeat",
        "Account",
        "AccountLoginSession",
        "RiskSignal",
        "Persona",
        "Topic",
        "Rule",
        "Note",
        "NoteMetric",
        "KbDocument",
        "KbChunk",
        "Goal",
        "Plan",
        "Task",
        "AgentRun",
        "AgentCheckpoint",
        "Interaction",
        "Comment",
        "AuditLog",
        "Alert",
        "DailyCounter",
        "AppConfig",
    ]:
        assert hasattr(models, name), f"missing model: {name}"


def test_tableless_match_schema():
    """__tablename__ 必须与 schema.sql 一致。"""
    from matrix.db import models

    table_to_class = {
        "devices": "Device",
        "device_hmac_keys": "DeviceHmacKey",
        "device_heartbeats": "DeviceHeartbeat",
        "accounts": "Account",
        "account_login_sessions": "AccountLoginSession",
        "risk_signals": "RiskSignal",
        "personas": "Persona",
        "topics": "Topic",
        "rules": "Rule",
        "notes": "Note",
        "note_metrics": "NoteMetric",
        "kb_documents": "KbDocument",
        "kb_chunks": "KbChunk",
        "goals": "Goal",
        "plans": "Plan",
        "tasks": "Task",
        "agent_runs": "AgentRun",
        "agent_checkpoints": "AgentCheckpoint",
        "interactions": "Interaction",
        "comments": "Comment",
        "audit_logs": "AuditLog",
        "alerts": "Alert",
        "daily_counters": "DailyCounter",
        "app_config": "AppConfig",
    }
    for table, classname in table_to_class.items():
        cls = getattr(models, classname)
        assert cls.__tablename__ == table, (
            f"{classname}.__tablename__ = {cls.__tablename__!r}, expected {table!r}"
        )


def test_tableless_count():
    """models 模块声明的 Base 子类数量应为 25（含 Alert + DailyCounter + GoalRound，去掉 LlmUsage）。"""
    from sqlalchemy.orm import DeclarativeBase

    from matrix.db import models

    subclasses = [
        v
        for v in vars(models).values()
        if isinstance(v, type) and issubclass(v, DeclarativeBase) and v is not DeclarativeBase
    ]
    # Base 自己也要排除
    subclasses = [s for s in subclasses if s is not models.Base]
    assert len(subclasses) == 25, f"got {len(subclasses)}: {[s.__name__ for s in subclasses]}"


def test_base_is_declarative():
    """Base 是 DeclarativeBase 的子类。"""
    from sqlalchemy.orm import DeclarativeBase

    from matrix.db.models import Base

    assert issubclass(Base, DeclarativeBase)


def test_partitioned_tables_have_partitionby():
    """三个时序表必须有 postgresql_partition_by 配置。"""
    from matrix.db.models import (
        AgentCheckpoint,
        DeviceHeartbeat,
        NoteMetric,
    )

    for cls in (DeviceHeartbeat, NoteMetric, AgentCheckpoint):
        table_args = cls.__table_args__
        # table_args 可能是 tuple 含 dict；检查 dict 形式
        flat = []
        for arg in table_args:
            if isinstance(arg, dict):
                flat.append(arg)
        assert any("postgresql_partition_by" in d for d in flat), (
            f"{cls.__name__} missing postgresql_partition_by, got {table_args!r}"
        )


def test_status_check_constraints():
    """含状态枚举字段的表必须有 CHECK constraint。"""
    from sqlalchemy import CheckConstraint

    # 表 -> 该表应受约束的字段名（schema.sql 中至少一个 CHECK）
    expected_enum_field = {
        "Account": "status",
        "AccountLoginSession": "result",
        "AgentRun": "status",
        "Goal": "status",
        "Interaction": "type",  # 也含 result check
        "Note": "status",
        "Plan": "status",
        "Rule": "severity",
        "Task": "status",
        "KbDocument": "type",
        "RiskSignal": "severity",
    }

    for cls_name, field in expected_enum_field.items():
        from matrix.db import models as m

        cls = getattr(m, cls_name)
        table_args = cls.__table_args__
        check_constraints = [
            arg for arg in table_args if isinstance(arg, CheckConstraint)
        ]
        assert check_constraints, f"{cls_name} missing CheckConstraint"
        sql_blob = " ".join(str(c.sqltext) for c in check_constraints).lower()
        assert field.lower() in sql_blob, (
            f"{cls_name} CHECK doesn't reference '{field}': {sql_blob}"
        )


def test_device_instantiation_simple_fields():
    """纯简单字段（无 pg 特化类型）的模型可实例化。"""
    from matrix.db.models import Device

    d = Device(
        id=uuid.uuid4(),
        nickname="dev1",
        model="Pixel 7",
        android_version="14",
        apk_version="1.0.0",
        status="active",
        tags=["brand-a", "product-b"],
    )
    assert d.nickname == "dev1"
    assert d.status == "active"
    assert d.tags == ["brand-a", "product-b"]


def test_account_instantiation():
    from matrix.db.models import Account

    a = Account(
        id=uuid.uuid4(),
        handle="xhs_user",
        status="active",
        risk_score=0.5,
    )
    assert a.handle == "xhs_user"
    assert a.risk_score == 0.5


def test_note_instantiation():
    from matrix.db.models import Note

    n = Note(
        id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        title="hello",
        content="world",
        tags=["a", "b"],
        images=["http://x"],
    )
    assert n.title == "hello"
    assert n.tags == ["a", "b"]


def test_app_config_instantiation():
    from matrix.db.models import AppConfig

    cfg = AppConfig(key="version", value="0.4", description="schema version")
    assert cfg.key == "version"


def test_audit_log_instantiation():
    from matrix.db.models import AuditLog

    log = AuditLog(action="device_register", user_id="op1")
    assert log.action == "device_register"


def test_kb_chunk_instantiation():
    from matrix.db.models import KbChunk

    c = KbChunk(
        id=uuid.uuid4(),
        doc_id=uuid.uuid4(),
        chunk_index=0,
        text="chunk body",
        token_count=100,
        embedding=[0.1] * 1536,
    )
    assert c.chunk_index == 0
    assert c.token_count == 100
    assert len(c.embedding) == 1536


def test_models_metadata_contains_all_tables():
    """Base.metadata 应包含全部 24 张表的 Table 对象（含 migration 004 加的 alerts）。"""
    from matrix.db.models import Base

    table_names = set(Base.metadata.tables.keys())
    expected = set(EXPECTED_TABLES)
    assert table_names == expected, (
        f"diff missing: {expected - table_names}, extra: {table_names - expected}"
    )