# 监控告警 Runbook

| 项 | 内容 |
|---|---|
| 适用对象 | 运维 / on-call |
| 配套 | [architecture/SDD.md §3.6](../architecture/SDD.md) / [deployment/runbook.md](../deployment/runbook.md) |

## 1. 监控架构

```
APK 端指标 ──┐
             ├─→ 主控 OTel Collector ──→ 本地 Prometheus ──→ Grafana
主控指标 ────┤                                          
OS 指标 ─────┘                                          
              ↓
          Alertmanager ──→ 邮件 / Webhook / 飞书 / 短信
```

## 2. 关键指标

### 2.1 设备指标

| 指标 | 阈值 | 告警级别 |
|---|---|---|
| `device_online_count` | < N * 0.8 | warning |
| `device_offline_count` | > N * 0.2 | warning |
| `device_heartbeat_age_seconds` | > 300 | critical（单设备） |
| `device_battery_low_count` | < 20% 设备 > 30% | warning |
| `device_tailscale_degraded_count` | > 0 | critical |
| `device_apk_http_latency_p95` | > 5s | warning |
| `device_derp_switch_count_per_hour` | > 10 | warning（频繁切换） |

### 2.2 账号指标

| 指标 | 阈值 | 告警级别 |
|---|---|---|
| `account_high_risk_count` | risk_score > 0.7 | critical |
| `account_banned_count_24h` | > 0 | critical |
| `account_publish_success_rate_24h` | < 0.8 | warning |
| `account_login_failure_count_24h` | > 3 | warning |
| `account_risk_signal_count_severity_4_plus_24h` | > 0 | critical |

### 2.3 任务指标

| 指标 | 阈值 | 告警级别 |
|---|---|---|
| `task_pending_age_p95` | > 600s | warning |
| `task_failure_rate_5m` | > 0.3 | critical |
| `task_dispatch_throughput_per_min` | < 5（> 10 分钟） | warning |
| `task_queue_depth_pending` | > 1000 | warning |
| `checkpoint_write_latency_p95` | > 200ms | warning |

### 2.4 Agent 指标

| 指标 | 阈值 | 告警级别 |
|---|---|---|
| `agent_run_duration_p95` | > 600s | warning |
| `agent_state_machine_stuck_count` | > 0（任何状态 > 10min） | critical |
| `agent_human_takeover_rate_24h` | > 0.2 | warning |
| `vlm_call_count_per_run` | > 5 | info |
| `vlm_confidence_below_threshold_rate` | > 0.3 | warning |

### 2.5 LLM / VLM 成本

| 指标 | 阈值 | 告警级别 |
|---|---|---|
| `llm_cost_usd_per_day` | > 日预算 | critical |
| `llm_cost_usd_per_run_p95` | > $1 | warning |
| `llm_latency_p95` | > 30s | warning |
| `llm_rate_limit_hit_count_1h` | > 5 | warning |
| `vlm_cost_usd_per_day` | > 日预算 | critical |

### 2.6 系统指标

| 指标 | 阈值 | 告警级别 |
|---|---|---|
| `master_process_cpu_percent` | > 80% | warning |
| `master_process_memory_mb` | > 2000 | warning |
| `postgres_connections_active` | > pool_size * 0.8 | warning |
| `postgres_disk_usage_percent` | > 80% | warning |
| `disk_free_gb` | < 20 | warning |
| `tailscale_derp_reachable` | false | critical |

## 3. 告警处理 Runbook

### 3.1 DEVICE_OFFLINE

**触发**：单设备心跳超时 > 5 分钟

**初步排查**：
```bash
# 1. 检查 Tailscale
tailscale status | grep <device-tailnet-ip>

# 2. 通过 Tailscale SSH 进 APK（如配置）
ssh <device> 'uptime; dumpsys batterystats | head -50'

# 3. 检查 APK 进程
adb shell ps -A | grep matrix
```

**常见原因**：
- 手机没电 / 自动关机
- Tailscale 失联 → 看 DERP 中继
- APK 进程被系统杀 → 看 Watchdog 日志
- 蜂窝信号差

**恢复**：
1. 等 5 分钟看是否自动恢复（Tailscale / 网络瞬断）
2. 不恢复：远程通知运营者人工检查
3. 30 分钟未恢复：触发"设备下线 + 任务等待"流程
4. 设备恢复后心跳自动续上

### 3.2 RISK_BLOCKED

**触发**：账号触发平台风控

**立即行动**：
1. **暂停该账号**（自动）
2. 通知运营者
3. **不要立刻重新登录 / 重新发内容**（加重风险）

**排查**：
- 看该账号最近 24h 操作：频次 / 内容 / 异常
- 看 `risk_signals` 表：触发原因
- 关联其他账号是否同时被封（IP / 内容关联）

**恢复**：
- 短期（1-7 天）：保持暂停，观察
- 中期（7-30 天）：低频试探（每 3 天 1 次手动登录）
- 长期：评估是否弃用

**预防**：
- 限速器确认开启
- 内容去重确认生效
- 人设隔离确认到位

### 3.3 SELECTOR_NOT_FOUND

**触发**：选择器在当前 UI 找不到节点

**初步排查**：
```bash
# 1. 拉取 APK 截图
adb shell screencap -p /sdcard/selector_fail.png
adb pull /sdcard/selector_fail.png .

# 2. 查看 UI 树
adb shell uiautomator dump /sdcard/ui.xml
adb pull /sdcard/ui.xml .
```

**常见原因**：
- XHS App 更新，UI 改了
- 设备分辨率不同，节点不匹配
- 内容渲染中（等待不到位）

**恢复**：
1. 立即触发 VLM 读屏降级
2. 人工接管（如有）→ 截图发到开发群
3. 开发修复选择器 → 部署 APK → 重试任务

**预防**：
- 每日 E2E 回归
- 关键页面多选择器 fallback

### 3.4 TAILSCALE_DERP_LOST

**触发**：Tailscale 与 DERP 中继失联

**排查**：
```bash
# 1. 检查 DERP 状态
tailscale netcheck

# 2. 检查 Headscale 健康
curl -fsS https://<headscale-url>/health

# 3. 测试到 DERP 的连通性
curl -v telnet://<derp-host>:3478
```

**恢复**：
1. 检查 DERP 容器是否存活（`docker ps`）
2. 重启 DERP：`docker compose restart derp`
3. Headscale 控制面不可用：检查 VPS
4. 切换到备用 DERP 区域

**预防**：
- 多 DERP 节点（不同区域）
- Headscale 自托管而非依赖官方 DERP

### 3.5 BUDGET_EXCEEDED

**触发**：LLM 成本超日预算

**立即行动**：
1. **暂停所有 Agent 运行**（自动）
2. 通知运营者
3. 排查预算消耗来源

**排查**：
```sql
-- 查今日 LLM 用量
SELECT model, call_type, COUNT(*), SUM(total_tokens), SUM(cost_usd)
FROM llm_usage
WHERE ts > NOW() - INTERVAL '1 day'
GROUP BY model, call_type
ORDER BY SUM(cost_usd) DESC;
```

**常见原因**：
- Agent 重试循环（state machine 卡住）
- VLM 调用激增（大量 SELECTOR_NOT_FOUND）
- 某账号触发 LLM 频繁调用

**恢复**：
1. 修根本原因
2. 调整预算上限
3. 重启 Agent

**预防**：
- 监控 LLM 调用频次 + 异常检测
- 单 run / 单账号 LLM 上限

### 3.6 POSTGRES_DISK_FULL

**触发**：数据库磁盘使用 > 80%

**立即行动**：
1. 删旧 checkpoint（90 天前）
2. 删旧 device_heartbeats（30 天前）
3. 评估是否要扩盘

**长期**：
- 监控表大小
- 自动清理任务（cron 跑）

## 4. 值班

### 4.1 on-call 排班

- 每周轮换
- 主 on-call + 备 on-call

### 4.2 升级路径

| 严重度 | 响应时间 | 升级 |
|---|---|---|
| P0（服务完全不可用） | 5 分钟 | 主 → 备 → 团队 lead |
| P1（功能受损） | 30 分钟 | 主 → 备 |
| P2（边界） | 4 小时 | 主 |

### 4.3 通知渠道

- 邮件：所有 P0/P1
- 飞书 / Slack：P0/P1
- 短信：仅 P0

## 5. 监控面板

Grafana 预置面板：
- **System Overview**：所有指标总览
- **Devices**：设备状态 / 心跳 / 电池
- **Accounts**：账号风险 / 状态
- **Tasks**：队列 / 吞吐 / 错误
- **Agent**：状态机 / run 耗时
- **LLM Cost**：成本趋势
- **Errors**：错误码分布

## 6. 日志

- 主控：`~/.matrix/logs/{date}.jsonl`
- APK：`adb logcat -s MatrixAPK:*`
- Tailscale：`journalctl -u tailscaled`
- Headscale：`docker logs headscale`
- PostgreSQL：`docker logs postgres`

## 7. 性能基线

| 指标 | 基线 | 上限 |
|---|---|---|
| 主控 CPU | 5% | 50% |
| 主控内存 | 300MB | 1GB |
| DB CPU | 10% | 70% |
| 任务 P95 | 2s | 5s |
| LLM P95 | 10s | 30s |
| 心跳延迟 | 30s | 60s |

超出基线 2x 持续 10 分钟触发 warning。

## 8. 演练

每季度一次故障演练：
- 模拟设备掉线
- 模拟账号封禁
- 模拟 Headscale 失联
- 模拟数据库满

验证 Runbook 可执行性 + 团队响应速度。
