# 数据库设计说明

| 项 | 内容 |
|---|---|
| 数据库 | PostgreSQL 16+ |
| 扩展 | uuid-ossp / pgcrypto / vector (pgvector) / pg_trgm |
| Schema 版本 | v0.1 |
| 配套文件 | [schema.sql](./schema.sql) |

## 表清单

### 业务实体
- `devices` — 物理手机设备
- `device_hmac_keys` — 设备 HMAC 密钥历史（支持轮换）
- `device_heartbeats` — 设备心跳（时序，按天分区）
- `accounts` — 小红书账号
- `account_login_sessions` — 登录会话历史
- `risk_signals` — 风控信号（独立表，不散落业务表）
- `personas` — 账号人设
- `topics` — 选题库
- `rules` — 平台规则 / 违禁词
- `notes` — 笔记
- `note_metrics` — 笔记指标（时序）
- `kb_documents` / `kb_chunks` — 知识库（带 embedding）

### 任务 / Agent
- `goals` — 运营目标
- `plans` — 目标拆解
- `tasks` — 原子任务
- `agent_runs` — Agent 运行实例
- `agent_checkpoints` — Agent 状态机 checkpoint（时序）

### 交互
- `interactions` — 互动记录（点赞 / 评论 / 关注）
- `comments` — 评论内容

### 统计 / 审计 / 配置
- `audit_logs` — 审计日志
- `alerts` — 告警表
- `daily_counters` — 限速日上限原子计数
- `app_config` — 应用配置 KV

## 关键设计决策

### 1. 时序表按天分区
`device_heartbeats` / `note_metrics` / `agent_checkpoints` 三张时序表按 `ts` 字段 RANGE 分区：
- 默认分区兜底
- 月度分区由运维脚本提前创建
- 旧分区可单独 detach / drop 释放空间

### 2. 软删除
所有业务表带 `deleted_at` 字段，列表查询默认过滤 `WHERE deleted_at IS NULL`。物理删除仅在归档脚本中执行。

### 3. 风控信号独立表
`risk_signals` 不与 `accounts` / `devices` 强耦合（用 account_id 关联），便于：
- 跨账号分析风控模式
- 不影响主业务表查询性能
- 单独保留 / 清理策略

### 4. 任务幂等
`tasks.request_id` 字段 UNIQUE 约束，确保重试不重复执行。

### 5. HMAC 密钥不存明文
`device_hmac_keys.key_hash` 存密钥的 hash（不存明文），明文密钥仅在 APK 端通过 Tailscale 通道单次下发。

### 6. embedding 维度
默认 1536（OpenAI text-embedding-3-small）。换 embedding 模型需 ALTER TABLE。

### 7. 向量索引
`kb_chunks.embedding` / `kb_documents.embedding` 用 ivfflat 索引 + cosine 距离。`lists=100` 适用于 10万级 chunks；超过 50 万需重新调参。

## 迁移

迁移工具：Alembic。

```bash
# 创建新迁移
alembic revision --autogenerate -m "add_xxx"

# 应用迁移
alembic upgrade head

# 回滚
alembic downgrade -1
```

迁移文件存于 `backend/migrations/versions/`。

## 分区维护

每月 1 日前创建下月分区：

```sql
CREATE TABLE device_heartbeats_2026_08 PARTITION OF device_heartbeats
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
```

建议在 cron 跑：

```cron
0 0 25 * * psql -d matrix -c "SELECT create_monthly_partitions();"
```

`create_monthly_partitions()` 函数定义见 `migrations/`。

## 备份与恢复

详见 [operations/monitoring-runbook.md](../operations/monitoring-runbook.md) 和 [deployment/runbook.md](../deployment/runbook.md)。

要点：
- 每日全量 + 增量 WAL 归档
- 备份加密（AES-256-GCM）
- 异地存储（OSS / S3）
- 季度恢复演练

## 性能监控

关注：
- pg_stat_statements（慢查询）
- 表膨胀（`pg_stat_user_tables`）
- 索引使用（`pg_stat_user_indexes`）
- 连接池使用率
- 时序分区大小

## 参考

- [PostgreSQL 16 文档](https://www.postgresql.org/docs/16/)
- [pgvector 文档](https://github.com/pgvector/pgvector)
- [Alembic 文档](https://alembic.sqlalchemy.org/)
