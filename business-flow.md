# 「矩阵」业务流程 — 完整闭环版

---

## 一句话总结

▎ 这是一个两层循环系统：
▎ - 外层 Goal 循环：每周/每天自动跑一圈，每圈挑一批设备、发一批内容
▎ - 内层 13 节点状态机循环：每篇内容从「调研→写稿→审核→排程→发→互动→回采→复盘→自动总结→决策」走一遍
▎
▎ 两层循环套在一起，加上"复盘→学习→下次写得更好"的飞轮，再叠两套看门狗兜底防崩。

---

## 一、数据怎么流转（9 张主表 + 辅助表）

### 9 张主表

| 表 | 它是啥 | 关键字段 | 大白话 |
|---|---|---|---|
| `goals` | 一个目标 | `phase(PENDING/PREPARING/EXECUTING/MONITORING/SUMMARIZING/DECIDING/DONE)`、`current_round`、`max_rounds=3`、`notes_per_round=3`、`target_likes=500` | 一个任务单 |
| `goal_rounds` | 一轮的执行记录 | `goal_id`、`round_number`、`kpi_summary`、`created_at` | 任务单下的每一圈 |
| `agent_runs` | 一次状态机跑 | `current_state`、`status(running/success/failed/timeout)`、`payload(brief + preassigned_slot)` | 任务单下的一篇内容 |
| `agent_checkpoints` | 状态机每跳一格记一条 | `from_state → to_state + payload` | 任务进度的脚印 |
| `notes` | 内容本身 | `status(draft/reviewing/scheduled/publishing/published/failed/deleted)`、`platform_note_id`、`platform_url`、`scheduled_collect_at` | 实际产出的一篇文章 |
| `note_metrics` | 笔记表现数据 | `note_id`、`views`、`likes`、`collects`、`comments`、`follows_gained` | 一篇文章跑出来的数据 |
| `kb_documents` | 知识库 | `type(brand/persona/rule/history/strategy_card/topic)`、`is_published` | AI 的"参考资料柜" |
| `kb_chunks` | KB 切片（向量检索单元） | `doc_id`、`text`、`embedding` | 参考资料的每一段 |
| `tasks` | 调度任务池 | `type(device_collect_metrics/...)`、`scheduled_at`、`status` | 调度器要干的事 |

### 辅助表

| 表 | 它是啥 | 关键字段 | 备注 |
|---|---|---|---|
| `devices` | 手机 | `tailnet_ip(INET)`、`hmac_key_id`、`last_heartbeat`、`status(pending/active/offline/tailscale_degraded/disabled)` | 真机 |
| `accounts` | 平台账号 | `handle(unique)`、`persona_id`、`device_id` | 绑到设备 + 人设 |
| `device_hmac_keys` | HMAC 密钥 | `id`、`device_id`、`key_hash`、`rotated_at`、`revoked_at` | 防请求伪造 |
| `device_heartbeats` | 心跳 | `device_id + ts(复合主键)`、`battery`、`network`、`signal_dbm`、`foreground_app`、`errors`、`tailscale_state` | **按 `ts` 做 RANGE 分区**（PostgreSQL 原生分区表） |
| `interactions` | 互动记录 | `account_id`、`target_note_id`、`type(like/comment/follow/share/collect)`、`result(pending/success/failed)`、`request_id(unique)` | INTERACT 节点写入 |
| `comments`、`risk_signals`、`personas`、`topics`、`rules`、`plans`、`alerts` 等 | — | — | 见 `db/models.py` |

---

## 二、外层 Goal 循环 — GoalOrchestratorWorker

文件：`backend/matrix/agent/orchestrator_runner.py` + `orchestrator.py`

每 **5 秒**（`poll_interval`）扫一次数据库：
```sql
SELECT * FROM goals
 WHERE status='active' AND phase!='DONE'
 ORDER BY created_at ASC LIMIT 20
```

对每个 goal 调 `advance_goal(session, goal)` 推进一步。

### 一个 Goal 的完整生命周期（7 个 phase）

```
PENDING ──► PREPARING ──► EXECUTING ──► MONITORING ──► SUMMARIZING ──► DECIDING ──┬──► PREPARING（开下一轮）
   │            │             │              │              │              │       │
   └────────────┴─────────────┴──────────────┴──────────────┴──────────────┘       └──► DONE（收工）
                  （任何阶段失败 → ALERT 节点 → 人工 ack → 回 PENDING）
```

### 每个 phase 干啥

#### phase = PENDING（待启动）
- 协调员第一次扫到这个 goal → 直接推进到 PREPARING
- 文件：`orchestrator.py:612-623`

#### phase = PREPARING（准备开干）
- 调 `round_allocator.allocate(brief, n=active_devices数, stagger_minutes=...)` 抢坑位
- 抢到 N 个 slot：每个 slot = `(device_id, account_id, scheduled_at, style_hint)`
- 给每个 slot 写一条 `agent_runs`（payload 里塞 `brief + preassigned_slot`）
- 同时写一条 `goal_rounds`（记录这一轮的开始）
- 写完 → phase 切到 **EXECUTING**
- 文件：`orchestrator.py:625-649`（`PHASE_PREPARING` 分支）

#### phase = EXECUTING（执行中）
- 给每条 AgentRun 起一个 RunManager 任务跑状态机
- 协调员每 5s 扫一次确认还在跑
- 全部 AgentRun 跑完 → phase 切到 **MONITORING**
- 文件：`orchestrator.py:651-672`（`PHASE_EXECUTING` 分支）

#### phase = MONITORING（监控）
- 等所有本轮 run 跑完（`_check_runs_done`）
- 收集本轮 KPI（views/likes/collects/comments/follows_gained 平均值）
- 把 KPI 写到 `goal_rounds.kpi_summary`
- 跑完 → phase 切到 **SUMMARIZING**
- 文件：`orchestrator.py:674-695`

#### phase = SUMMARIZING（自动总结）⭐
- **这是飞轮的关键阶段**——每轮自动把跑出来的 run 数据总结成经验卡
- 调 `_summarize_round` → 提炼出多条 `strategy_card` 写进 KB（`type=strategy_card`）
- 总结完 → phase 切到 **DECIDING**
- 文件：`orchestrator.py:697-715`
- 同时还有手动路径：`POST /learning/summarize-goal/{goal_id}` 可对整个 goal 一次性总结（`auto_publish=False` 默认不自动发布）

#### phase = DECIDING（决策）⭐
- 读 `goal_rounds.kpi_summary` → 调 `_should_continue(goal, kpi)` 判断
- 满足继续条件 → `current_round += 1` → phase 切回 **PREPARING** 开下一轮
- 达到 `max_rounds` 或 KPI 不达标 → phase 切 **DONE**，goal.status = 'achieved'
- 文件：`orchestrator.py:717-774`

### 阶段 7：Goal 看门狗（防卡死）

文件：`backend/matrix/agent/goal_stuck_watchdog.py`

- 每 **60 秒**（`poll_interval_sec=60.0`，比协调员主循环 5s 慢一个数量级）扫一次孤儿 goal：
  ```sql
  SELECT id FROM goals
   WHERE status = 'active'
     AND phase = 'PENDING'
     AND deleted_at IS NULL
     AND created_at < NOW() - INTERVAL '120 seconds'  -- stuck_threshold_sec=120
     AND phase_updated_at IS NULL                      -- 协调员压根没碰过的最准信号
   ORDER BY created_at ASC LIMIT 20
  ```
- 扫到孤儿 → 调 `advance_goal` 推进救活
- 复用主流程的事务/commit 语义，不另写 UPDATE

---

## 三、内层 13 节点状态机 — 跑一篇内容

文件：`backend/matrix/agent/state_machine.py` + 11 个节点文件

关键代码就一段（`state_machine.py:110` 起的 `_build()`）：

```
START ──► IDLE ──► RESEARCH ──► DRAFT ──► IMAGE_GEN ──► REVIEW
                                                         │
                                              ┌──────────┼──────────┐
                                              ▼          ▼          ▼
                                           SCHEDULE   REVISE     ALERT
                                              │         │  │       │
                                              │         ▼  └───────┤
                                              │      (回 DRAFT)    │
                                              │                   │
                                              ▼                   │
                                           DISPATCH               │
                                              │                   │
                                              ▼                   │
                                           PUBLISH                │
                                              │                   │
                                              ▼                   │
                                        ┌─────┼─────┐             │
                                        ▼     ▼     ▼             │
                                    INTERACT COLLECT ALERT ◄────────┘
                                        │     │
                                        └──►──┘
                                              ▼
                                           ANALYZE ──► IDLE ──► END
```

### 每节点干啥（全部基于源码）

| 节点 | 文件 | 真实干的活 | 人话 |
|---|---|---|---|
| RESEARCH | `nodes/research.py` | 查 KB **4 类**资料：`history(top_k=5)` + `rule(top_k=3)` + `brand(top_k=2)` + `persona(top_k=2)`，让 LLM 基于这些 + 日期动态生成 **3 个**选题 | 翻资料库 + 让 AI 出 3 个题目 |
| DRAFT | `nodes/draft.py` | 查 KB **5 类**资料：`persona(2)` + `rule(3)` + `brand(1)` + `strategy_card(5)` + `history(3)` → 渲染 prompt → 调 LLM 出 JSON 标题正文 tags → **不立刻落 notes 表**（v0.7+：dispatch 时才落库） | 写稿，稿子先进草稿箱（state 里） |
| IMAGE_GEN | `nodes/image_gen.py` | 调生图模型配图，失败 fallback 到纯文（route_after_image_gen 决定） | 配图 |
| REVIEW | `nodes/review.py` | 质量门：route_after_review 决定 → SCHEDULE / REVISE / ALERT | 审稿 |
| REVISE | `nodes/revise.py` | 回炉重写；route_after_revise 决定 → DRAFT 或 ALERT | 改稿 |
| SCHEDULE | `nodes/schedule.py` | 校验 orchestrator 预分配的 slot（不调 choose_slot），查限流、活跃窗 | 定时间定手机 |
| DISPATCH | `nodes/dispatch.py` | 把 draft 落到 notes 表（status=scheduled），写一条 publish 任务到 tasks 表 | 把任务丢给发布调度器 |
| PUBLISH | `nodes/publish.py` | `asyncio.sleep` 干等到 `scheduled_at` → 调 APK HTTP `POST /xhs/publish` → 拿回 `platform_note_id` + `url`；成功后把 notes.status 改成 `published` + 写 `scheduled_collect_at = now + 24h` | 真发，到点才发 |
| INTERACT | `nodes/interact.py` | 按 `interact_plan` 逐条 like/comment；用 `InteractPolicy` 做去重（24h 跨 plan）+ 风险自适应（risk_score ≥ 0.85 跳过、≥ 0.7 只 like 不 comment、账号 banned/suspended 跳过）；评论内容调 LLM 生成 **≤ 140 字**（XHS 评论硬上限） | 发完引流 |
| COLLECT | `nodes/collect.py` | 24h 后由调度器调 APK HTTP 拉 `views/likes/collects/comments/follows_gained`，落 `note_metrics` 表 + 回填 `notes.collected_at` | 收数据 |
| ANALYZE | `nodes/analyze.py` | 调 LLM 出 `review_text`（自由文本）+ `strategy_updates`（最多 5 条）→ 把本次发布写一条 `kb_documents.type=history`；同时把 `strategy_updates` 提炼成 `type=strategy_card`（强类型 JSON 卡片，下一轮 DRAFT 召回） | 写复盘报告 + 提炼经验卡 |
| ALERT | `nodes/alert.py` | 8 类错误码 → 2 档严重度（severity=3 高 / severity=2 低）→ 调 notifier（webhook 写到 notifications 表）→ 不自动 ack，等人工；ack 后才回 IDLE，否则停在 END | 出事了发警报 |

---

## 四、复盘 → 学习闭环（这才是飞轮）

文件：`backend/matrix/agent/summarize.py` + `learning_prompt.py` + `nodes/draft.py` + `nodes/analyze.py`

### 4.1 数据怎么提炼成"硬规则"

每次 ANALYZE 跑完，LLM 会返回两类东西：
1. `review_text`（自由文本复盘）→ 写进 `kb_documents.type=history`
2. `strategy_updates`（最多 5 条经验）→ 提炼成 StrategyCard：

```python
@dataclass
class StrategyCard:
    title_patterns: list[str]     # 标题模板（"数字+痛点"）
    hook_phrases: list[str]       # 开头钩子（"救命"、"后悔没早买"）
    structure: list[str]          # 内容顺序（"开头钩子→痛点→产品→价格→CTA"）
    tone_keywords: list[str]      # 调性词（"平价"、"真实"、"测评"）
    forbidden_patterns: list[str] # 禁用模式（"绝对化用词"、"未验证数据"）
```

注意：这是强类型 JSON 卡片，不是软文本。`render_for_prompt` 会渲染成"硬规则"：

▎ 【标题硬规则】标题里必须出现以下至少 1 个关键词：30天、实测、后悔没早买
▎ 【结构硬规则】内容按此顺序：开头钩子 → 痛点场景 → 解决产品 → 价格锚 → CTA
▎ 【禁用模式】绝对化用词 / 未验证数据 / 竞品直名

### 4.2 下次写稿时怎么召回（实际路径）

**DRAFT 节点的实际召回路径**（`nodes/draft.py:35-50`）：
```python
# 用 topic_title（短词）整句做语义检索，一次性拉 5 类
persona_chunks       = retrieve(query=topic_title, doc_types=("persona",),       top_k=2)
rule_chunks          = retrieve(query=topic_title, doc_types=("rule",),          top_k=3)
brand_chunks         = retrieve(query=topic_title, doc_types=("brand",),         top_k=1)
strategy_card_chunks = retrieve(query=topic_title, doc_types=("strategy_card",), top_k=5)
history_chunks       = retrieve(query=topic_title, doc_types=("history",),       top_k=3)
```

然后用节点自己的 `_format_strategy_cards` 和 `_format_history_chunks` 渲染进 prompt（不经过 `fetch_relevant_learnings`）。

**`fetch_relevant_learnings` 函数本身**（`learning_prompt.py:95`）：
- 实现确实是中文 2-gram 切分 + limit=5 + 召回 `strategy_card + rule`（两个类型）
- 但**目前没有任何生产节点调用它**，只被 `tests/test_learning.py` 引用
- 设计意图应该是给未来的"主题摘要"或"批量预生成 prompt"留接口，目前是死代码

### 4.3 自动飞轮的关键

- **第 1 次跑**：KB 几乎是空的 → 没经验卡 → 写得一般
- **跑 N 次后**：每轮 ANALYZE 自动写 strategy_card，每轮 DRAFT 自动召回 strategy_card → 写稿越来越稳
- **外层 SUMMARIZING 阶段**（每轮跑完后自动触发）会再额外提炼一轮 strategy_card → KB 增长更快
- **复盘越多越聪明，是真的飞轮，不是营销话术**

### 4.4 人工复盘路径（可选）

`POST /learning/summarize-goal/{goal_id}`：把整个 goal 的所有 run 数据一次性喂给 LLM，提炼成多条 strategy_card 写进 KB（默认 `auto_publish=False`，需要人工 review 后再发布）。
- 老板可以手动按一下这个按钮，让系统把最近 N 轮的经验总结一遍
- 文件：`backend/matrix/agent/summarize.py:321` 的 `summarize_goal_to_kb`

---

## 五、防崩机制（4 层兜底）

### 5.1 断点续跑 — Checkpoint

文件：`backend/matrix/agent/checkpoint.py` + `_default_repository.py`

- 状态机每跳一格 → 写一条 `agent_checkpoints` 记录（`from_state / to_state / payload / ts`）
- 如果进程崩溃，`resume_run(run_id)` 读最后一条 checkpoint，把 state 还原成 `{to_state, payload}` → 从断点继续
- 意味着：跑到一半崩了不会前功尽弃

### 5.2 AgentRun 看门狗 — `watcher.py`

- **每 30 秒**（`WatchdogConfig.poll_interval_sec=30.0`）扫一次 `agent_runs`：
  ```sql
  SELECT id FROM agent_runs
   WHERE status='running'
     AND ended_at IS NULL
     AND started_at < NOW() - INTERVAL ':threshold_sec seconds'
  ```
- 默认阈值 **1800 秒（30 分钟）**（`stuck_threshold_sec=1800`）
- 卡死的标 `status='timeout'` + 写 `agent_run_stuck_timeout` 通知
- 用 `started_at` 而非 `updated_at` 是因为中间状态切换不写 `updated_at`

### 5.3 Goal 看门狗 — `goal_stuck_watchdog.py`

- **每 60 秒**扫一次孤儿 goal：
  ```sql
  SELECT id FROM goals
   WHERE status='active' AND phase='PENDING'
     AND deleted_at IS NULL
     AND created_at < NOW() - INTERVAL '120 seconds'
     AND phase_updated_at IS NULL
   ORDER BY created_at ASC LIMIT 20
  ```
- 用 `phase_updated_at IS NULL`（不是 `<`）做最准信号：`phase_updated_at` 只在 `_set_phase` 写，`create_goal` 永远不写 → NULL = "协调员压根没碰过我"
- 扫到孤儿 → 调 `advance_goal` 救活

### 5.4 失败兜底 — ALERT 节点

- **8 类错误码**（`nodes/alert.py:14-23`）：
  | 错误码 | 描述 |
  |---|---|
  | `KB_RETRIEVE_FAILED` | knowledge base unreachable |
  | `LLM_FAILED` | LLM provider failure |
  | `DRAFT_LLM_FAILED` | draft generation failed |
  | `REVISE_LLM_FAILED` | revision failed |
  | `PUBLISH_FAILED` | platform publish failed |
  | `RISK_BLOCKED` | platform risk control |
  | `DEVICE_OFFLINE` | device offline |
  | `OUT_OF_ACTIVE_WINDOW` | out of active posting window |

- **2 档严重度**（不是 3 档，`_severity_for` 只返回 2 或 3）：
  | severity | 错误码 |
  |---|---|
  | **3（高）** | `RISK_BLOCKED` / `DEVICE_OFFLINE` / `PUBLISH_FAILED` |
  | **2（其他）** | `KB_RETRIEVE_FAILED` / `LLM_FAILED` / `DRAFT_LLM_FAILED` / `REVISE_LLM_FAILED` / `OUT_OF_ACTIVE_WINDOW` |

- 通知走 webhook → 写 `notifications` 表 + 可选 POST 外部 webhook
- 不自动 ack，必须人工进 `POST /alerts/{id}/resolve` 才放行；ack 后 ALERT → IDLE，否则停在 END

---

## 六、APK 怎么被指挥（最底层）

文件：`backend/matrix/device/adapters.py` 的 `ApkHttpClient` + `device/hmac.py`

后端要发小红书笔记：
1. `resolver(device_id)` 拿到 APK 的 tailnet IP + HMAC key
2. 组装 payload：`{account_id, title, content, images, tags, request_id}`
3. `compute_signature(hmac_key, timestamp, request_id, body)` 算签名（`hmac.py:45`）
4. `POST http://<tailnet_ip>:8080/xhs/publish`
   - Header：`X-Timestamp`、`X-Request-Id`、`X-Signature`
   - Content-Type：`application/json`
5. APK 在真机里调小红书 App 真实操作
6. 返回 `{ok, platform_note_id, url}`

链路安全：HMAC 签名防伪造 + Request ID 防重放 + Tailscale 内网防泄漏

采集数据走对称路径：`POST /xhs/collect_metrics` → 返回 `{views, likes, collects, comments, follows_gained}`

---

## 七、完整闭环一张图（最终版）

```
老板在前端
   │
   ▼
[1] 创建 Goal（type, target={theme,audience,persona}, notes_per_round, max_rounds）
   │
   ▼
外层 GoalOrchestratorWorker（每 5s 扫）
   │
   ▼  phase: PENDING → PREPARING
[2] round_allocator.allocate() 抢 N 个 (device, account, time, style_hint)
[3] 写 N 条 agent_runs（带 preassigned_slot）+ 1 条 goal_rounds
   │
   ▼  phase: PREPARING → EXECUTING
[4] 给每条 AgentRun 启动 RunManager
   │
   ▼  内层状态机开始（每条 AgentRun 独立跑 13 节点）
   │
   │ ┌─── 跑一篇：
   │ │     RESEARCH → DRAFT → IMAGE_GEN
   │ │     → REVIEW → SCHEDULE → DISPATCH（落 notes.status=scheduled）
   │ │     → PUBLISH（asyncio.sleep 等到点 → APK 真发 → 拿回 ID/URL，
   │ │                notes.status=published + scheduled_collect_at=now+24h）
   │ │     → INTERACT（去重 + 风险自适应 + 限流 + LLM 写评论 ≤140 字）
   │ │     → COLLECT（24h 后由调度器调 APK 拉数据 → 落 note_metrics）
   │ │     → ANALYZE（LLM 复盘 + 写 history + 提炼 strategy_card）
   │ │     → IDLE（→ 再开新一篇）
   │ │
   │ └─── 任何节点炸 → ALERT（8 类错误码 / 2 档严重度）→ notifier 通知 →
   │      人工 ack → 回 IDLE；未 ack 停在 END
   │
   ▼  所有 N 条 AgentRun 跑完 → phase: EXECUTING → MONITORING
[5] 收集本轮 KPI 写到 goal_rounds.kpi_summary
   │
   ▼  phase: MONITORING → SUMMARIZING  ⭐ 飞轮自驱点
[6] _summarize_round 自动提炼本轮经验成 strategy_card 入 KB
   │
   ▼  phase: SUMMARIZING → DECIDING
[7] _should_continue(goal, kpi) 判断：
      - 满足继续 → current_round += 1 → 回 PREPARING 开下一轮
      - 达到 max_rounds → phase: DONE，goal.status='achieved'
   │
   ▼
[8] 老板按一下「复盘」按钮（可选）
    POST /learning/summarize-goal/{goal_id}
    → LLM 一次性总结所有 run → 写多条 strategy_card 进 KB（默认未发布）
   │
   ▼
[9] 下次新 Goal / 下轮 DRAFT
    → nodes/draft.py 直接调 kb_retriever.retrieve() 按 topic_title 拉 5 类
      （persona + rule + brand + strategy_card + history）
    → strategy_card 走 _format_strategy_cards 渲染成"硬规则"塞进 prompt
    → LLM 写稿必须遵守
   │
   ▼
（飞轮：跑得越多 → SUMMARIZING 自动沉淀越多 strategy_card → 写稿召回越准 → 数据更好 → 更多 strategy_card ...）
```

---

## 八、附录：所有相关文件路径

### 后端核心
- `backend/matrix/agent/orchestrator_runner.py` — GoalOrchestratorWorker 外层循环
- `backend/matrix/agent/orchestrator.py` — advance_goal 状态推进（7 阶段）
- `backend/matrix/agent/state_machine.py` — 13 节点状态机构建（_build）
- `backend/matrix/agent/nodes/*.py` — 11 个节点实现
- `backend/matrix/agent/run_manager.py` — 单条 AgentRun 的 lifecycle 管理
- `backend/matrix/agent/checkpoint.py` — 断点续跑
- `backend/matrix/agent/watcher.py` — AgentRun 看门狗（30s）
- `backend/matrix/agent/goal_stuck_watchdog.py` — Goal 看门狗（60s）
- `backend/matrix/agent/summarize.py` — StrategyCard + summarize_goal_to_kb
- `backend/matrix/agent/learning_prompt.py` — fetch_relevant_learnings（当前未被生产节点调用）
- `backend/matrix/agent/interact_policy.py` — INTERACT 去重 + 风险自适应
- `backend/matrix/agent/llm_rate_limiter.py` — LLM 限流

### 设备层
- `backend/matrix/device/adapters.py` — ApkHttpClient（/xhs/publish）
- `backend/matrix/device/hmac.py` — compute_signature
- `backend/matrix/device/registry.py` — device 注册 + 心跳

### 数据库
- `backend/matrix/db/models.py` — 全部 ORM 模型（含分区定义）
- `backend/matrix/db/migrations/versions/*.py` — 25 张表的迁移历史

### API
- `backend/matrix/api/routes/learning.py` — `POST /learning/summarize-goal/{goal_id}`
- `backend/matrix/api/routes/alerts.py` — `POST /alerts/{id}/resolve`
- `backend/matrix/api/schemas/goal.py` — GoalPhase Literal（7 阶段）
- `backend/matrix/api/schemas/note.py` — NoteStatus Literal（7 档）