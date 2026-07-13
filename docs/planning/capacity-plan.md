# 容量规划

| 项 | 内容 |
|---|---|
| 适用对象 | 架构师 / 决策者 |
| 配套 | [SDD.md §8](../architecture/SDD.md) |

## 1. 目标场景

| 场景 | 设备数 | 账号数 | 日均发布 | 日均互动 |
|---|---|---|---|---|
| MVP | 1 | 1 | 1 | 5 |
| 早期 | 5 | 5 | 5 | 25 |
| 成长 | 20 | 20 | 20 | 100 |
| 中等 | 100 | 100 | 100 | 500 |
| 大型 | 500 | 500 | 500 | 2500 |

## 2. 单设备资源

| 资源 | 估算 |
|---|---|
| 心跳 QPS | 1 / 30s = 0.033 |
| 发布操作 | 5 / 天（含失败重试） |
| 互动操作 | 30 / 天 |
| 数据回采 | 24 / 天（每小时 1 次） |
| 截图 | 1 / 小时 = 24 / 天 |

## 3. 主控资源（按场景）

| 场景 | CPU | 内存 | DB | 网络 | VPS |
|---|---|---|---|---|---|
| 1 设备 | 5% | 300MB | 100MB | 1Mbps | 不需要 |
| 5 设备 | 15% | 500MB | 500MB | 5Mbps | 不需要 |
| 20 设备 | 40% | 1GB | 2GB | 20Mbps | 监控小 VPS |
| 100 设备 | 80% | 2GB | 10GB | 100Mbps | 中等 VPS + DB 分离 |
| 500 设备 | 200%（多核） | 4GB | 50GB | 500Mbps | 大型 VPS + DB 集群 |

## 4. 数据库容量

### 4.1 表大小估算（100 设备，1 年）

| 表 | 日增量 | 年累计 |
|---|---|---|
| `device_heartbeats` | 100 * 2880 = 288K 行 | 1 亿行 (~30GB) |
| `note_metrics` | 100 篇 * 24 = 2400 行 | 88 万行 (~200MB) |
| `notes` | 100 篇 | 36K 行 (~50MB) |
| `tasks` | 100 * 65 = 6500 | 240 万行 (~500MB) |
| `agent_checkpoints` | 100 * 100 = 10K | 360 万行 (~1GB) |
| `risk_signals` | 100 * 5 = 500 | 18 万行 (~50MB) |
| **总计** | | ~35GB / 年 |

### 4.2 时序表清理策略

| 表 | 保留期 | 清理方式 |
|---|---|---|
| `device_heartbeats` | 30 天 | detach 旧分区 |
| `note_metrics` | 1 年 | detach 旧分区 |
| `agent_checkpoints` | 7 天 | DELETE 旧行 |
| `audit_logs` | 1 年 | 归档到冷存储 |
| `risk_signals` | 90 天 | DELETE 旧行 |
| `daily_counters` | 7 天 | DELETE 旧行（按天） |

## 5. LLM / VLM 调用量

### 5.1 单 run LLM 调用

| 节点 | 调用类型 | 单次 token | 单 run 次数 |
|---|---|---|---|
| research | embedding + 1 generate | 2K + 5K | 3 |
| draft | generate | 8K | 1 |
| review | generate | 3K | 1 |
| schedule | generate（小） | 1K | 1 |
| analyze | embedding + 1 generate | 2K + 3K | 2 |
| **单 run 合计** | | ~25K input + ~5K output | ~9 calls |

### 5.2 每日 LLM 调用（100 设备）

- 单 run 9 calls
- 日发布 100 篇 + 互动 500 次
- 假设每 5 个互动算 1 个 run（含分析）
- 日 run 数：100 + 100 = 200
- 日 LLM calls：200 * 9 = 1800
- 日 token：~5M input + ~1M output

详见 [SDD.md §8](../architecture/SDD.md)。

## 6. VLM 调用

- 触发条件：选择器失效（约 5% 概率）
- 100 设备 * 5% = 5 次 / 天
- 单次 VLM：~1000 tokens
- 日 VLM token：5000
- 主要成本是 API 费用（按张 / 按 token）

## 7. Tailscale / DERP 容量

### 7.1 节点数

- 1 主控 + N 设备 = N+1 节点
- 100 设备场景：101 节点（Headscale 轻松支撑）

### 7.2 DERP 带宽

- 100 设备 * 平均 10KB/s = 1MB/s = 8Mbps
- 单 DERP 实例：1Gbps 轻松支撑
- 单 DERP 区域可支撑 10000+ 节点

### 7.3 多 DERP 节点

当以下情况需多 DERP：
- 跨地区（不同地理区域用户）
- 单 DERP 带宽 / CPU 饱和
- 高可用

## 8. 存储 I/O

### 8.1 主控 DB I/O

- 心跳：~3 QPS（100 设备 / 30s）
- 任务：~5 QPS
- Agent checkpoint：~10 QPS
- 总：~20 QPS，单 SSD 即可

### 8.2 时序表 I/O

- 时序表读多写少
- 分区裁剪后单分区查询 < 10ms
- 无需特殊优化

## 9. 内存

| 进程 | 内存 |
|---|---|
| Web frontend（浏览器内 React） | ~150MB（浏览器配额，单独计） |
| Python 后端 | 500MB |
| PostgreSQL | 1GB（含 shared_buffers） |
| Prometheus | 500MB |
| Grafana | 200MB |
| **总** | ~2.5GB |

100 设备：建议 8GB VPS。
500 设备：建议 16GB VPS + 独立 DB。

## 10. 横向扩展

### 10.1 当前架构限制

- 主控单实例（无热备）
- DB 单实例
- 单 DERP 区域

### 10.2 未来扩展点

- **DB 读写分离**：主写从读（metrics 查询 / dashboard）
- **DB 分片**：按 account_id 分片
- **多主控**：1 主 + N 备
- **多 DERP**：跨地区 + 负载均衡

## 11. 监控指标（容量相关）

- DB 表大小
- 时序分区大小
- LLM 日成本
- Tailscale 节点数
- VPS CPU / 内存 / 磁盘
- 网络带宽

详细阈值见 [monitoring-runbook.md §2](../operations/monitoring-runbook.md)。

## 12. 扩容触发条件

| 资源 | 升级触发 |
|---|---|
| CPU > 70% 持续 1h | 升级 VPS / 加核 |
| 内存 > 80% | 升级 VPS |
| 磁盘 > 80% | 扩容 / 清理 |
| DB QPS 接近连接池上限 | 优化 / 读写分离 |
| Tailscale 节点 > 1000 | 评估 Headscale 性能 |
| LLM 月成本 > 预算 | 优化 / 换模型 |
