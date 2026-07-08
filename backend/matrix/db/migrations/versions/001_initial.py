"""initial schema

完整嵌入 docs/database/schema.sql 的全部 DDL（扩展、函数、表、索引、触发器、视图、初始数据）。

Revision ID: 001_initial
Revises:
Create Date: 2026-07-08 00:00:00
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""-- =============================================================================
-- AI-Native 自媒体矩阵主控系统 - 数据库 Schema
-- 版本：v0.1
-- 数据库：PostgreSQL 16+ (需 pgvector 扩展)
-- 字符集：UTF-8
-- 时区：UTC 存储，本地时区按 devices.timezone 转换
-- =============================================================================

-- 扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- 通用：updated_at 触发器函数
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- 设备
-- =============================================================================

CREATE TABLE devices (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nickname        VARCHAR(64) NOT NULL,
    model           VARCHAR(64) NOT NULL,
    android_version VARCHAR(32) NOT NULL,
    adb_serial      VARCHAR(64) UNIQUE,
    apk_version     VARCHAR(32) NOT NULL,
    tailnet_ip      INET,
    tags            TEXT[] NOT NULL DEFAULT '{}',
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'active', 'offline', 'tailscale_degraded', 'disabled')),
    hmac_key_id     VARCHAR(64),
    last_heartbeat  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_devices_status ON devices(status) WHERE deleted_at IS NULL;
CREATE INDEX idx_devices_tags ON devices USING GIN(tags) WHERE deleted_at IS NULL;
CREATE INDEX idx_devices_last_heartbeat ON devices(last_heartbeat) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_devices_updated_at BEFORE UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE device_hmac_keys (
    id              VARCHAR(64) PRIMARY KEY,
    device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    key_hash        BYTEA NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rotated_at      TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX idx_device_hmac_keys_device ON device_hmac_keys(device_id);

CREATE TABLE device_heartbeats (
    device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts              TIMESTAMPTZ NOT NULL,
    battery         SMALLINT,
    network         VARCHAR(16),
    signal_dbm      SMALLINT,
    foreground_app  VARCHAR(128),
    errors          JSONB,
    tailscale_state VARCHAR(32),
    PRIMARY KEY (device_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE device_heartbeats_default PARTITION OF device_heartbeats DEFAULT;

-- =============================================================================
-- 账号
-- =============================================================================

CREATE TABLE accounts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    handle          VARCHAR(64) NOT NULL UNIQUE,
    persona_id      UUID REFERENCES personas(id),
    device_id       UUID REFERENCES devices(id),
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'active', 'suspended', 'banned', 'disabled')),
    last_active     TIMESTAMPTZ,
    risk_score      REAL NOT NULL DEFAULT 0
                       CHECK (risk_score >= 0 AND risk_score <= 1),
    auto_suspend_until TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_accounts_device ON accounts(device_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_accounts_status ON accounts(status) WHERE deleted_at IS NULL;
CREATE INDEX idx_accounts_persona ON accounts(persona_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_accounts_risk ON accounts(risk_score DESC) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_accounts_updated_at BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE account_login_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    device_id       UUID NOT NULL REFERENCES devices(id),
    result          VARCHAR(16) NOT NULL
                       CHECK (result IN ('success', 'failed', 'captcha', 'logout', 'expired')),
    risk_signal     VARCHAR(32),
    error_message   TEXT
);

CREATE INDEX idx_account_login_sessions_account_ts ON account_login_sessions(account_id, ts DESC);

CREATE TABLE risk_signals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    device_id       UUID REFERENCES devices(id),
    type            VARCHAR(32) NOT NULL,
    severity        SMALLINT NOT NULL CHECK (severity BETWEEN 1 AND 5),
    source          VARCHAR(32) NOT NULL,
    payload         JSONB,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_risk_signals_account_ts ON risk_signals(account_id, ts DESC);
CREATE INDEX idx_risk_signals_type_ts ON risk_signals(type, ts DESC);

-- =============================================================================
-- 人设 / 规则 / 选题
-- =============================================================================

CREATE TABLE personas (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(64) NOT NULL UNIQUE,
    tone            VARCHAR(256) NOT NULL,
    style_guide     TEXT NOT NULL,
    forbidden_words TEXT[] NOT NULL DEFAULT '{}',
    sample_note_ids UUID[] NOT NULL DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE TRIGGER trg_personas_updated_at BEFORE UPDATE ON personas
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE topics (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           VARCHAR(256) NOT NULL,
    category        VARCHAR(32) NOT NULL,
    source          VARCHAR(32) NOT NULL,
    heat_score      REAL NOT NULL DEFAULT 0,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_topics_heat ON topics(heat_score DESC) WHERE deleted_at IS NULL;
CREATE INDEX idx_topics_category ON topics(category) WHERE deleted_at IS NULL;
CREATE INDEX idx_topics_last_used ON topics(last_used DESC) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_topics_updated_at BEFORE UPDATE ON topics
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE rules (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    category        VARCHAR(32) NOT NULL,
    text            TEXT NOT NULL,
    severity        SMALLINT NOT NULL CHECK (severity BETWEEN 1 AND 5),
    source          VARCHAR(64),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_rules_category ON rules(category) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_rules_updated_at BEFORE UPDATE ON rules
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =============================================================================
-- 笔记
-- =============================================================================

CREATE TABLE notes (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id          UUID NOT NULL REFERENCES accounts(id),
    title               VARCHAR(256) NOT NULL,
    content             TEXT NOT NULL,
    images              TEXT[] NOT NULL DEFAULT '{}',
    tags                TEXT[] NOT NULL DEFAULT '{}',
    status              VARCHAR(16) NOT NULL DEFAULT 'draft'
                           CHECK (status IN ('draft', 'reviewing', 'scheduled', 'publishing', 'published', 'failed', 'deleted')),
    platform_note_id    VARCHAR(64),
    platform_url        TEXT,
    request_id          VARCHAR(64) UNIQUE,
    scheduled_at        TIMESTAMPTZ,
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX idx_notes_account ON notes(account_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_notes_status ON notes(status) WHERE deleted_at IS NULL;
CREATE INDEX idx_notes_scheduled ON notes(scheduled_at) WHERE status = 'scheduled';
CREATE INDEX idx_notes_published ON notes(published_at DESC) WHERE status = 'published';
CREATE INDEX idx_notes_tags ON notes USING GIN(tags) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_notes_updated_at BEFORE UPDATE ON notes
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE note_metrics (
    note_id         UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    ts              TIMESTAMPTZ NOT NULL,
    views           INTEGER NOT NULL DEFAULT 0,
    likes           INTEGER NOT NULL DEFAULT 0,
    collects        INTEGER NOT NULL DEFAULT 0,
    comments        INTEGER NOT NULL DEFAULT 0,
    follows_gained  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (note_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE note_metrics_default PARTITION OF note_metrics DEFAULT;

-- =============================================================================
-- 知识库
-- =============================================================================

CREATE TABLE kb_documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type            VARCHAR(32) NOT NULL
                       CHECK (type IN ('brand', 'persona', 'rule', 'topic', 'history', 'template')),
    ref_id          UUID,
    title           VARCHAR(256),
    content         TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    embedding       vector(1536),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_kb_documents_type ON kb_documents(type) WHERE deleted_at IS NULL;
CREATE INDEX idx_kb_documents_ref ON kb_documents(ref_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_kb_documents_embedding ON kb_documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TRIGGER trg_kb_documents_updated_at BEFORE UPDATE ON kb_documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE kb_chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id          UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    token_count     INTEGER NOT NULL,
    embedding       vector(1536) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (doc_id, chunk_index)
);

CREATE INDEX idx_kb_chunks_doc ON kb_chunks(doc_id);
CREATE INDEX idx_kb_chunks_embedding ON kb_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- =============================================================================
-- 任务 / Agent
-- =============================================================================

CREATE TABLE goals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type            VARCHAR(32) NOT NULL,
    target          JSONB NOT NULL,
    deadline        TIMESTAMPTZ,
    status          VARCHAR(16) NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'achieved', 'failed', 'cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE TRIGGER trg_goals_updated_at BEFORE UPDATE ON goals
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE plans (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    goal_id         UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    steps           JSONB NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_plans_updated_at BEFORE UPDATE ON plans
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plan_id         UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    device_id       UUID NOT NULL REFERENCES devices(id),
    account_id      UUID NOT NULL REFERENCES accounts(id),
    action          VARCHAR(32) NOT NULL,
    payload         JSONB NOT NULL,
    request_id      VARCHAR(64) NOT NULL UNIQUE,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    last_error      JSONB,
    scheduled_at    TIMESTAMPTZ NOT NULL,
    executed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tasks_status_scheduled ON tasks(status, scheduled_at) WHERE status = 'pending';
CREATE INDEX idx_tasks_device_status ON tasks(device_id, status);
CREATE INDEX idx_tasks_account_status ON tasks(account_id, status);
CREATE INDEX idx_tasks_plan ON tasks(plan_id);

CREATE TRIGGER trg_tasks_updated_at BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE agent_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    goal_id         UUID REFERENCES goals(id),
    current_state   VARCHAR(32) NOT NULL DEFAULT 'IDLE',
    checkpoint      JSONB,
    payload         JSONB NOT NULL DEFAULT '{}',
    status          VARCHAR(16) NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'success', 'failed', 'cancelled', 'timeout')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ
);

CREATE INDEX idx_agent_runs_status ON agent_runs(status);
CREATE INDEX idx_agent_runs_goal ON agent_runs(goal_id);

CREATE TRIGGER trg_agent_runs_updated_at BEFORE UPDATE ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE agent_checkpoints (
    run_id          UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    ts              TIMESTAMPTZ NOT NULL,
    from_state      VARCHAR(32) NOT NULL,
    to_state        VARCHAR(32) NOT NULL,
    payload         JSONB,
    PRIMARY KEY (run_id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE agent_checkpoints_default PARTITION OF agent_checkpoints DEFAULT;

-- =============================================================================
-- 交互
-- =============================================================================

CREATE TABLE interactions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      UUID NOT NULL REFERENCES accounts(id),
    target_note_id  UUID REFERENCES notes(id),
    target_user     VARCHAR(64),
    type            VARCHAR(16) NOT NULL
                       CHECK (type IN ('like', 'comment', 'follow', 'share', 'collect')),
    content         TEXT,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    result          VARCHAR(16) NOT NULL DEFAULT 'pending'
                       CHECK (result IN ('pending', 'success', 'failed')),
    error_message   TEXT,
    request_id      VARCHAR(64) UNIQUE
);

CREATE INDEX idx_interactions_account_ts ON interactions(account_id, ts DESC);
CREATE INDEX idx_interactions_target_note ON interactions(target_note_id);
CREATE INDEX idx_interactions_result ON interactions(result);

CREATE TABLE comments (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    note_id         UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    account_id      UUID NOT NULL REFERENCES accounts(id),
    text            TEXT NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    replied_to      UUID REFERENCES comments(id)
);

CREATE INDEX idx_comments_note ON comments(note_id);
CREATE INDEX idx_comments_account ON comments(account_id);

-- =============================================================================
-- LLM 使用统计
-- =============================================================================

CREATE TABLE llm_usage (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model           VARCHAR(64) NOT NULL,
    call_type       VARCHAR(32) NOT NULL,
    prompt_tokens   INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL,
    cost_usd        NUMERIC(10, 6),
    latency_ms      INTEGER,
    run_id          UUID REFERENCES agent_runs(id),
    account_id      UUID REFERENCES accounts(id)
);

CREATE INDEX idx_llm_usage_ts ON llm_usage(ts DESC);
CREATE INDEX idx_llm_usage_model_ts ON llm_usage(model, ts DESC);

-- =============================================================================
-- 审计日志
-- =============================================================================

CREATE TABLE audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id         VARCHAR(64),
    action          VARCHAR(64) NOT NULL,
    resource_type   VARCHAR(32),
    resource_id     UUID,
    before_state    JSONB,
    after_state     JSONB,
    ip_address      INET,
    user_agent      TEXT
);

CREATE INDEX idx_audit_logs_ts ON audit_logs(ts DESC);
CREATE INDEX idx_audit_logs_user_ts ON audit_logs(user_id, ts DESC);
CREATE INDEX idx_audit_logs_resource ON audit_logs(resource_type, resource_id);

-- =============================================================================
-- 配置存储
-- =============================================================================

CREATE TABLE app_config (
    key             VARCHAR(128) PRIMARY KEY,
    value           JSONB NOT NULL,
    description     TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 视图
-- =============================================================================

CREATE VIEW v_device_status AS
SELECT
    d.id,
    d.nickname,
    d.model,
    d.tailnet_ip,
    d.status,
    d.last_heartbeat,
    EXTRACT(EPOCH FROM (NOW() - d.last_heartbeat)) AS heartbeat_age_sec,
    COUNT(DISTINCT a.id) AS bound_accounts
FROM devices d
LEFT JOIN accounts a ON a.device_id = d.id AND a.deleted_at IS NULL
WHERE d.deleted_at IS NULL
GROUP BY d.id;

CREATE VIEW v_account_risk AS
SELECT
    a.id,
    a.handle,
    a.status,
    a.risk_score,
    a.last_active,
    COUNT(r.id) FILTER (WHERE r.ts > NOW() - INTERVAL '7 days') AS recent_signals,
    COUNT(r.id) FILTER (WHERE r.ts > NOW() - INTERVAL '7 days' AND r.severity >= 4) AS recent_high_signals
FROM accounts a
LEFT JOIN risk_signals r ON r.account_id = a.id
WHERE a.deleted_at IS NULL
GROUP BY a.id;

CREATE VIEW v_task_queue AS
SELECT
    t.status,
    COUNT(*) AS count,
    MIN(t.scheduled_at) AS earliest,
    MAX(t.scheduled_at) AS latest
FROM tasks t
GROUP BY t.status;
""")


def downgrade() -> None:
    # 反序删除（依赖顺序倒过来）
    op.execute("DROP VIEW IF EXISTS v_task_queue;")
    op.execute("DROP VIEW IF EXISTS v_account_risk;")
    op.execute("DROP VIEW IF EXISTS v_device_status;")
    op.execute("DROP TABLE IF EXISTS app_config;")
    op.execute("DROP TABLE IF EXISTS audit_logs;")
    op.execute("DROP TABLE IF EXISTS llm_usage;")
    op.execute("DROP TABLE IF EXISTS comments;")
    op.execute("DROP TABLE IF EXISTS interactions;")
    op.execute("DROP TABLE IF EXISTS agent_checkpoints CASCADE;")
    op.execute("DROP TABLE IF EXISTS agent_runs CASCADE;")
    op.execute("DROP TABLE IF EXISTS tasks CASCADE;")
    op.execute("DROP TABLE IF EXISTS plans CASCADE;")
    op.execute("DROP TABLE IF EXISTS goals CASCADE;")
    op.execute("DROP TABLE IF EXISTS kb_chunks CASCADE;")
    op.execute("DROP TABLE IF EXISTS kb_documents CASCADE;")
    op.execute("DROP TABLE IF EXISTS note_metrics CASCADE;")
    op.execute("DROP TABLE IF EXISTS notes CASCADE;")
    op.execute("DROP TABLE IF EXISTS rules CASCADE;")
    op.execute("DROP TABLE IF EXISTS topics CASCADE;")
    op.execute("DROP TABLE IF EXISTS personas CASCADE;")
    op.execute("DROP TABLE IF EXISTS risk_signals CASCADE;")
    op.execute("DROP TABLE IF EXISTS account_login_sessions CASCADE;")
    op.execute("DROP TABLE IF EXISTS accounts CASCADE;")
    op.execute("DROP TABLE IF EXISTS device_heartbeats CASCADE;")
    op.execute("DROP TABLE IF EXISTS device_hmac_keys CASCADE;")
    op.execute("DROP TABLE IF EXISTS devices CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
    # 扩展保留不删（可能被其他库用）
