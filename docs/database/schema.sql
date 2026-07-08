-- =============================================================================
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

-- =============================================================================
-- 通用：updated_at 触发器函数
-- =============================================================================
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
    model           VARCHAR(64) NOT NULL,             -- e.g. "Pixel 7"
    android_version VARCHAR(32) NOT NULL,             -- e.g. "Android 14"
    adb_serial      VARCHAR(64) UNIQUE,
    apk_version     VARCHAR(32) NOT NULL,
    tailnet_ip      INET,                              -- Tailscale 分配的 IP
    tags            TEXT[] NOT NULL DEFAULT '{}',     -- 品牌/产品线/运营组
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'active', 'offline', 'tailscale_degraded', 'disabled')),
    hmac_key_id     VARCHAR(64),                       -- 当前 HMAC 密钥 ID
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

-- HMAC 密钥历史（支持轮换）
CREATE TABLE device_hmac_keys (
    id              VARCHAR(64) PRIMARY KEY,           -- key_id
    device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    key_hash        BYTEA NOT NULL,                    -- HMAC 密钥的 hash（不存明文）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rotated_at      TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX idx_device_hmac_keys_device ON device_hmac_keys(device_id);

-- 设备心跳（时序，按天分区）
CREATE TABLE device_heartbeats (
    device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts              TIMESTAMPTZ NOT NULL,
    battery         SMALLINT,                          -- 0-100
    network         VARCHAR(16),                       -- 4G/5G/none
    signal_dbm      SMALLINT,
    foreground_app  VARCHAR(128),
    errors          JSONB,                             -- {error_count, last_error_code, ...}
    tailscale_state VARCHAR(32),                       -- connected/disconnected/connecting
    PRIMARY KEY (device_id, ts)
) PARTITION BY RANGE (ts);

-- 默认分区 + 月度分区（由运维脚本创建下月分区）
CREATE TABLE device_heartbeats_default PARTITION OF device_heartbeats DEFAULT;

-- =============================================================================
-- 账号
-- =============================================================================

CREATE TABLE accounts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    handle          VARCHAR(64) NOT NULL UNIQUE,        -- XHS 用户名
    persona_id      UUID REFERENCES personas(id),
    device_id       UUID REFERENCES devices(id),        -- 设备亲和
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'active', 'suspended', 'banned', 'disabled')),
    last_active     TIMESTAMPTZ,
    risk_score      REAL NOT NULL DEFAULT 0
                       CHECK (risk_score >= 0 AND risk_score <= 1),
    auto_suspend_until TIMESTAMPTZ,                    -- 自动暂停到期时间
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

-- 账号登录会话（时序）
CREATE TABLE account_login_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    device_id       UUID NOT NULL REFERENCES devices(id),
    result          VARCHAR(16) NOT NULL
                       CHECK (result IN ('success', 'failed', 'captcha', 'logout', 'expired')),
    risk_signal     VARCHAR(32),                       -- 登录时的风控信号
    error_message   TEXT
);

CREATE INDEX idx_account_login_sessions_account_ts ON account_login_sessions(account_id, ts DESC);

-- 风控信号（时序，独立表，不散落到业务表）
CREATE TABLE risk_signals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    device_id       UUID REFERENCES devices(id),
    type            VARCHAR(32) NOT NULL,               -- captcha/frequency/anomaly/...
    severity        SMALLINT NOT NULL CHECK (severity BETWEEN 1 AND 5),
    source          VARCHAR(32) NOT NULL,               -- xhs/agent/internal
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
    tone            VARCHAR(256) NOT NULL,              -- "活泼 / 二次元 / 治愈"
    style_guide     TEXT NOT NULL,                     -- 风格指南长文
    forbidden_words TEXT[] NOT NULL DEFAULT '{}',
    sample_note_ids UUID[] NOT NULL DEFAULT '{}',       -- 示范笔记 ID
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
    source          VARCHAR(32) NOT NULL,               -- 手工/热点/历史
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
    category        VARCHAR(32) NOT NULL,               -- forbidden/best_practice/limit_avoidance
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
    images              TEXT[] NOT NULL DEFAULT '{}',   -- 图片 URL / 路径
    tags                TEXT[] NOT NULL DEFAULT '{}',
    status              VARCHAR(16) NOT NULL DEFAULT 'draft'
                           CHECK (status IN ('draft', 'reviewing', 'scheduled', 'publishing', 'published', 'failed', 'deleted')),
    platform_note_id    VARCHAR(64),                    -- XHS 笔记 ID
    platform_url        TEXT,
    request_id          VARCHAR(64) UNIQUE,             -- 幂等 key
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

-- 笔记指标（时序，按天分区）
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
    ref_id          UUID,                              -- 关联到 personas/topics/rules/notes
    title           VARCHAR(256),
    content         TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    embedding       vector(1536),                      -- 文档级 embedding
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
    type            VARCHAR(32) NOT NULL,               -- net_followers / engagement / ...
    target          JSONB NOT NULL,                    -- 目标具体值
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
    steps           JSONB NOT NULL,                    -- 步骤定义
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
    action          VARCHAR(32) NOT NULL,               -- device_publish / device_interact / device_collect
    payload         JSONB NOT NULL,
    request_id      VARCHAR(64) NOT NULL UNIQUE,        -- 幂等 key
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
    checkpoint      JSONB,                              -- 最后一次 checkpoint 摘要
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

-- Agent checkpoint（时序）
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
    content         TEXT,                                -- 评论内容（仅 type=comment）
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
    replied_to      UUID REFERENCES comments(id)        -- 回复的评论
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
    call_type       VARCHAR(32) NOT NULL,               -- generate/embed/vlm
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
-- 审计日志（追加写入）
-- =============================================================================

CREATE TABLE audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id         VARCHAR(64),                        -- 运营者 ID（操作系统用户）
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

-- 设备在线状态视图
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

-- 账号风险视图
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

-- 任务队列视图
CREATE VIEW v_task_queue AS
SELECT
    t.status,
    COUNT(*) AS count,
    MIN(t.scheduled_at) AS earliest,
    MAX(t.scheduled_at) AS latest
FROM tasks t
GROUP BY t.status;

-- =============================================================================
-- 初始数据
-- =============================================================================

INSERT INTO app_config (key, value, description) VALUES
    ('version', '"0.4"', 'Schema 版本'),
    ('active_persona_count', '0', '当前活跃 persona 数（缓存）'),
    ('active_goal_count', '0', '当前活跃 goal 数（缓存）');
