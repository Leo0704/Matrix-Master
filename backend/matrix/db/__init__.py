"""数据库连接 / 迁移。

- `Base` — SQLAlchemy 2.0 DeclarativeBase，所有 ORM 模型的基类
- `create_engine` / `get_database_url` — async engine 工厂
- `get_session` — async context manager / FastAPI dependency
- 各 ORM 模型（23 张表）
"""
from matrix.db.engine import create_engine, get_database_url
from matrix.db.models import (
    Account,
    AccountLoginSession,
    AgentCheckpoint,
    AgentRun,
    AppConfig,
    AuditLog,
    Base,
    Comment,
    Device,
    DeviceHeartbeat,
    DeviceHmacKey,
    Goal,
    Interaction,
    KbChunk,
    KbDocument,
    LlmUsage,
    Note,
    NoteMetric,
    Persona,
    Plan,
    Rule,
    RiskSignal,
    Task,
    Topic,
)
from matrix.db.session import get_session

__all__ = [
    "Base",
    "create_engine",
    "get_database_url",
    "get_session",
    # 业务实体
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
    # 任务 / Agent
    "Goal",
    "Plan",
    "Task",
    "AgentRun",
    "AgentCheckpoint",
    # 交互
    "Interaction",
    "Comment",
    # 统计 / 审计 / 配置
    "LlmUsage",
    "AuditLog",
    "AppConfig",
]