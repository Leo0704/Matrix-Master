"""SQLAlchemy 2.0 ORM 模型。

字段名、类型、约束与 docs/database/schema.sql 严格对齐。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Boolean,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import (
    ARRAY,
    INET,
    JSONB,
    UUID,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


# =============================================================================
# 设备
# =============================================================================


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("uuid_generate_v4()"),
    )
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    android_version: Mapped[str] = mapped_column(String(32), nullable=False)
    adb_serial: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    apk_version: Mapped[str] = mapped_column(String(32), nullable=False)
    tailnet_ip: Mapped[Optional[Any]] = mapped_column(INET)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sa_text("'{}'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'pending'")
    )
    hmac_key_id: Mapped[Optional[str]] = mapped_column(String(64))
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'offline', 'tailscale_degraded', 'disabled')",
            name="devices_status_check",
        ),
    )


class DeviceHmacKey(Base):
    __tablename__ = "device_hmac_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    key_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    rotated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class DeviceHeartbeat(Base):
    __tablename__ = "device_heartbeats"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    battery: Mapped[Optional[int]] = mapped_column(SmallInteger)
    network: Mapped[Optional[str]] = mapped_column(String(16))
    signal_dbm: Mapped[Optional[int]] = mapped_column(SmallInteger)
    foreground_app: Mapped[Optional[str]] = mapped_column(String(128))
    errors: Mapped[Optional[dict]] = mapped_column(JSONB)
    tailscale_state: Mapped[Optional[str]] = mapped_column(String(32))

    __table_args__ = (
        {"postgresql_partition_by": "RANGE (ts)"},
    )


# =============================================================================
# 账号
# =============================================================================


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("uuid_generate_v4()"),
    )
    handle: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    persona_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("personas.id")
    )
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'pending'")
    )
    last_active: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    risk_score: Mapped[float] = mapped_column(
        Numeric, nullable=False, server_default=sa_text("0")
    )
    auto_suspend_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'banned', 'disabled')",
            name="accounts_status_check",
        ),
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 1",
            name="accounts_risk_score_check",
        ),
    )


class AccountLoginSession(Base):
    __tablename__ = "account_login_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("uuid_generate_v4()"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id"), nullable=False
    )
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_signal: Mapped[Optional[str]] = mapped_column(String(32))
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "result IN ('success', 'failed', 'captcha', 'logout', 'expired')",
            name="account_login_sessions_result_check",
        ),
    )


class RiskSignal(Base):
    __tablename__ = "risk_signals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("uuid_generate_v4()"),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id")
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )

    __table_args__ = (
        CheckConstraint(
            "severity BETWEEN 1 AND 5",
            name="risk_signals_severity_check",
        ),
    )


# =============================================================================
# 人设 / 规则 / 选题
# =============================================================================


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("uuid_generate_v4()"),
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    tone: Mapped[str] = mapped_column(String(256), nullable=False)
    style_guide: Mapped[str] = mapped_column(Text, nullable=False)
    forbidden_words: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sa_text("'{}'")
    )
    sample_note_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=sa_text("'{}'")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("uuid_generate_v4()"),
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    heat_score: Mapped[float] = mapped_column(
        Numeric, nullable=False, server_default=sa_text("0")
    )
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("NOW()")
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Rule(Base):
    __tablename__ = 'rules'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            'severity BETWEEN 1 AND 5',
            name='rules_severity_check',
        ),
    )


# =============================================================================
# 笔记
# =============================================================================


class Note(Base):
    __tablename__ = 'notes'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey('accounts.id'), nullable=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    images: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sa_text("'{}'")
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sa_text("'{}'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'draft'")
    )
    platform_note_id: Mapped[Optional[str]] = mapped_column(String(64))
    platform_url: Mapped[Optional[str]] = mapped_column(Text)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'reviewing', 'scheduled', 'publishing', 'published', 'failed', 'deleted')",
            name='notes_status_check',
        ),
    )


class NoteMetric(Base):
    __tablename__ = 'note_metrics'

    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey('notes.id', ondelete='CASCADE'),
        primary_key=True,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    views: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text('0'))
    likes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text('0'))
    collects: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text('0'))
    comments: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text('0'))
    follows_gained: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('0')
    )

    __table_args__ = (
        {"postgresql_partition_by": "RANGE (ts)"},
    )


# =============================================================================
# 知识库
# =============================================================================


class KbDocument(Base):
    __tablename__ = 'kb_documents'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    title: Mapped[Optional[str]] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 'metadata' 字段名与 SQLAlchemy 的 Base.metadata 保留属性冲突，用 name= 保持列名为 metadata
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=sa_text("'{}'::jsonb"),
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('1')
    )
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536))
    is_published: Mapped[bool] = mapped_column(
        # 是否通过 review（kb-writing-guide §4.5）：未发布的文档对 Agent 不可见
        nullable=False,
        server_default=sa_text('FALSE'),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "type IN ('brand', 'persona', 'rule', 'topic', 'history', 'template', 'product')",
            name='kb_documents_type_check',
        ),
    )


class KbChunk(Base):
    __tablename__ = 'kb_chunks'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey('kb_documents.id', ondelete='CASCADE'),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )

    __table_args__ = (
        UniqueConstraint('doc_id', 'chunk_index', name='kb_chunks_doc_id_chunk_index_key'),
    )


# =============================================================================
# 任务 / Agent
# =============================================================================


class Goal(Base):
    __tablename__ = 'goals'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[dict] = mapped_column(JSONB, nullable=False)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'active'")
    )
    # v0.7：goal-level orchestrator 状态机（5 阶段 + DONE）
    phase: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=sa_text("'PENDING'")
    )
    current_round: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('1')
    )
    max_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('3')
    )
    # v0.7 第 1 期优化：可调字段（老板创建 goal 时可指定）
    target_likes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('500')
    )
    notes_per_round: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('3')
    )
    learning_summary: Mapped[Optional[str]] = mapped_column(Text)
    phase_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'achieved', 'failed', 'cancelled')",
            name='goals_status_check',
        ),
        CheckConstraint(
            "phase IN ('PENDING','PREPARING','EXECUTING','MONITORING',"
            "'SUMMARIZING','DECIDING','DONE')",
            name='goals_phase_check',
        ),
        CheckConstraint(
            "notes_per_round BETWEEN 1 AND 20",
            name='goals_notes_per_round_range_check',
        ),
    )


class GoalRound(Base):
    """每轮运营记录：goal_id + round_number + KPI 汇总 + 起止时间。"""

    __tablename__ = 'goal_rounds'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey('goals.id', ondelete='CASCADE'), nullable=False
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    kpi_summary: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    notes_created: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('0')
    )
    total_views: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('0')
    )
    total_likes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text('0')
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )

    __table_args__ = (
        UniqueConstraint('goal_id', 'round_number', name='goal_rounds_goal_round_uniq'),
    )


class Plan(Base):
    __tablename__ = 'plans'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey('goals.id', ondelete='CASCADE'),
        nullable=False,
    )
    steps: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
            name='plans_status_check',
        ),
    )


class Task(Base):
    __tablename__ = 'tasks'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey('plans.id', ondelete='CASCADE'),
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey('devices.id'), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey('accounts.id'), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text('0'))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text('3'))
    last_error: Mapped[Optional[dict]] = mapped_column(JSONB)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
            name='tasks_status_check',
        ),
    )


class AgentRun(Base):
    __tablename__ = 'agent_runs'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    goal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey('goals.id')
    )
    current_state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=sa_text("'IDLE'")
    )
    checkpoint: Mapped[Optional[dict]] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa_text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'running'")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'cancelled', 'timeout')",
            name='agent_runs_status_check',
        ),
    )


class AgentCheckpoint(Base):
    __tablename__ = 'agent_checkpoints'

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey('agent_runs.id', ondelete='CASCADE'),
        primary_key=True,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (
        {"postgresql_partition_by": "RANGE (ts)"},
    )


# =============================================================================
# 交互
# =============================================================================


class Interaction(Base):
    __tablename__ = 'interactions'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey('accounts.id'), nullable=False
    )
    target_note_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey('notes.id')
    )
    target_user: Mapped[Optional[str]] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    result: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sa_text("'pending'")
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ('like', 'comment', 'follow', 'share', 'collect')",
            name='interactions_type_check',
        ),
        CheckConstraint(
            "result IN ('pending', 'success', 'failed')",
            name='interactions_result_check',
        ),
    )


class Comment(Base):
    __tablename__ = 'comments'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey('notes.id', ondelete='CASCADE'),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey('accounts.id'), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    replied_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey('comments.id')
    )


# =============================================================================
# 统计 / 审计 / 配置
# =============================================================================


class AuditLog(Base):
    __tablename__ = 'audit_logs'

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    user_id: Mapped[Optional[str]] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(32))
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    before_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    after_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    ip_address: Mapped[Optional[Any]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)


class Alert(Base):
    """告警：来自 monitoring/alerts.py 9 条 check 规则。

    字段：
    - code: 告警类型（DEVICE_OFFLINE / RISK_BLOCKED / ...）
    - severity: critical / warning / info
    - subject_id: 关联 device_id / account_id / run_id / region 等
    - resolved: 是否已处理
    """
    __tablename__ = 'alerts'

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text('uuid_generate_v4()'),
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[Optional[str]] = mapped_column(String(128))
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_text('FALSE')
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "severity IN ('critical', 'warning', 'info')",
            name='alerts_severity_check',
        ),
    )


class AppConfig(Base):
    __tablename__ = 'app_config'

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )


class DailyCounter(Base):
    """按 (scope, key, kind, day) 原子自增的日计数器。

    取代 ``RateLimiter`` 内的进程内 ``_DailyCounter``（uvicorn workers>1 会被绕过）。
    通过 ``INSERT ... ON CONFLICT DO UPDATE SET count = daily_counters.count + 1``
    实现跨进程 / 跨节点的严格日上限。
    """
    __tablename__ = 'daily_counters'

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[Any] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text('0'))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text('NOW()')
    )


__all__ = [
    'Base',
    'Device',
    'DeviceHmacKey',
    'DeviceHeartbeat',
    'Account',
    'AccountLoginSession',
    'RiskSignal',
    'Persona',
    'Topic',
    'Rule',
    'Note',
    'NoteMetric',
    'KbDocument',
    'KbChunk',
    'Goal',
    'Plan',
    'Task',
    'AgentRun',
    'AgentCheckpoint',
    'Interaction',
    'Comment',
    'DailyCounter',
    'AuditLog',
    'Alert',
    'AppConfig',
]
