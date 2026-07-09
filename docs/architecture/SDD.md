# 系统设计文档（SDD）

| 项 | 内容 |
|---|---|
| 版本 | v0.1 |
| 适用范围 | 主控桌面应用 + 手机端 companion APK + 知识库 + 自主运营 Agent |
| 主要读者 | 后端开发 / Agent 开发 / APK 开发 / 架构师 |
| 配套文档 | [PRD.md](../../PRD.md) / [API 规范](../api/) / [数据库](../database/) / [威胁模型](./threat-model.md) |

本文档是写代码的**直接依据**。所有模块的接口、状态机、数据流、错误处理都在此规定。**与代码不一致时，以代码为准并提 issue 更新本文档**。

---

## 1. 范围与非范围

### 范围
- 主控（macOS / Windows 桌面应用，Tauri shell + Python 后端）
- 手机端 companion APK（Android，Kotlin）
- 知识库（PostgreSQL + pgvector）
- 自主运营 Agent（LangGraph 状态机）
- Tailscale mesh（Headscale 自托管）控制通道

### 非范围
- 多平台支持（仅小红书）
- 云手机 / 模拟器
- Web 版主控
- 服务端化部署（架构预留，但不实现）
- 多租户

---

## 2. 系统全景

```
┌────────────────────────────────────────────────────────────────┐
│ 主控桌面应用（Tauri shell + Python 后端，本地运行）               │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ 知识库子系统  │  │ Agent 子系统 │  │ 任务调度子系统        │  │
│  │ (RAG)        │←→│ (LangGraph)  │←→│ (asyncio + 持久队列)  │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│         ↑                                    ↓                   │
│  ┌──────────────┐                  ┌──────────────────────┐    │
│  │ 监控子系统    │                  │ 设备-账号管理子系统  │    │
│  │ (OTel + 指标) │                  │ (devices/accounts)   │    │
│  └──────────────┘                  └──────────────────────┘    │
│                                              │                   │
│  ┌────────────────────────────────────────┐  │                   │
│  │ 持久化：PostgreSQL + pgvector          │  │                   │
│  │  - 业务表（devices/accounts/notes/...）│  │                   │
│  │  - Agent checkpoint                    │  │                   │
│  │  - 向量库（kb_chunks）                 │  │                   │
│  └────────────────────────────────────────┘  │                   │
└──────────────────────────────────────────────┼───────────────────┘
                                               │ Tailscale mesh
                                               │ (HMAC + 双向 TLS)
                                               │
                              ┌────────────────┴────────────────┐
                              │                                 │
                  ┌───────────┴──────────┐         ┌───────────┴──────────┐
                  │ 手机① companion APK  │   ...   │ 手机⑩ companion APK  │
                  │ (Kotlin)              │         │ (Kotlin)              │
                  │  - HTTP Server        │         │                       │
                  │  - AccessibilityService│        │                       │
                  │  - Foreground Service │         │                       │
                  │  - Tailscale client   │         │                       │
                  └───────────────────────┘         └───────────────────────┘
                              ↓
                       XHS App（无障碍驱动）
```

### 2.1 关键不变量

1. **主控是唯一决策中心**：手机端不决策，只执行下发的指令。
2. **APK 是主控操作手机的唯一桥梁**：禁止绕过 APK 直接调 adb（开发调试除外）。
3. **所有写操作幂等**：tool 调用必带 `request_id`，重试不重复执行。
4. **设备亲和强约束**：账号绑定某台手机后，不跨设备调度任务。
5. **手机仅用蜂窝数据**：WiFi 强制关闭，IP 隔离防关联。

---

## 3. 模块设计

### 3.1 主控应用总览

#### 3.1.1 进程结构

主控包含两个进程：

| 进程 | 语言 | 职责 |
|---|---|---|
| **Tauri shell** | Rust + Web（前端） | UI 渲染 / 用户交互 / 系统托盘 / 通知 |
| **Python 后端** | Python 3.11+ | 业务逻辑（LangGraph / LLM / RAG / DB / 调度） |

#### 3.1.2 进程通信

- Tauri shell 通过本地 HTTP（`localhost:8666`）调用 Python 后端 REST。
- Python 后端启动时自动拉起（或 Tauri 检测未启动则启动）。
- 双方通过 heartbeat（10s）确认对方在线；任一方失联，UI 展示离线状态。

#### 3.1.3 启动顺序

```
Tauri shell 启动
  → 检测 Python 后端是否运行
    → 否：spawn Python 子进程，等待 ready
    → 是：直连
  → 读取本地配置（~/.matrix/config.yaml）
  → 初始化 DB 连接池
  → 启动 LangGraph Agent runtime
  → 启动任务调度器
  → 启动 OTel exporter
  → 启动 Tailscale 客户端（如未启动）
  → 展示 UI
```

#### 3.1.4 目录结构

```
matrix-master/
├── shell/                    # Tauri shell
│   ├── src/                  # Rust 代码
│   └── ui/                   # Web 前端
├── backend/                  # Python 后端
│   ├── matrix/
│   │   ├── agent/            # LangGraph 状态机
│   │   ├── kb/               # 知识库
│   │   ├── scheduler/        # 任务调度
│   │   ├── device/           # 设备-账号管理
│   │   ├── monitoring/       # 监控 / OTel
│   │   ├── api/              # 内部 REST API（Tauri 调用）
│   │   ├── db/               # 数据库连接 / 迁移
│   │   └── llm/              # LLM 客户端封装
│   ├── migrations/           # Alembic 迁移
│   ├── tests/
│   └── pyproject.toml
├── docs/                     # 本文档集
└── README.md
```

---

### 3.2 知识库子系统

#### 3.2.1 内容分类

| 类型 | 用途 | 写入频率 | 示例 |
|---|---|---|---|
| `brand` | 品牌定位 / 视觉 / 价值观 | 季度更新 | "美妆品牌，目标 25-35 岁女性" |
| `persona` | 账号人设 | 账号初始化 | 名称 / 语气 / 风格 / 违禁词 |
| `rule` | 平台规则 / 违禁词 | 平台变更 | XHS 违禁词表 / 限流规避 |
| `topic` | 选题库 | 持续追加 | 季节性选题 / 热点追踪 |
| `history` | 历史笔记 + 表现 | 每条笔记发布后 | 标题 / 内容 / 阅读 / 赞藏 |
| `template` | 文案 / 标题模板 | 持续优化 | 爆款标题模板 / 收尾模板 |

#### 3.2.2 存储

- **结构化字段**：`personas` / `topics` / `rules` 表存完整结构。
- **检索内容**：`kb_documents` + `kb_chunks` 表存 chunked 文本 + embedding。
- **chunk 策略**：每个 doc 按 500 token 切分，overlap 50 token。
- **embedding 维度**：1536（OpenAI text-embedding-3-small）或按选型调整。

#### 3.2.3 检索

```python
def retrieve(query: str, type: str, top_k: int = 5, filters: dict = None) -> list[Chunk]:
    """混合检索：向量 + 关键词 + 过滤"""
    embedding = embed(query)
    vector_results = pgvector_search(embedding, type, top_k * 2, filters)
    keyword_results = ts_search(query, type, top_k * 2, filters)
    return rerank(vector_results, keyword_results, top_k)
```

- 向量检索：pgvector cosine 距离
- 关键词：PostgreSQL `ts_vector` + `ts_query`
- 重排序：RRF（Reciprocal Rank Fusion）

#### 3.2.4 更新流程

1. 运营者在 UI 触发"添加 persona"等动作。
2. 后端写入 `personas` 表 + 同步生成 `kb_documents` + `kb_chunks` + embedding。
3. 版本号 +1，写入 `version` 字段。
4. 触发 Agent 状态机刷新（如有运行中的 run 引用了旧版本，回退到 IDLE 重新开始）。

---

### 3.3 Agent 子系统

#### 3.3.1 状态机定义

见 PRD §6.3.1，此处补充实现细节。

**节点清单**：

| 节点 | 触发 | 输入 | 输出 | 异常处理 |
|---|---|---|---|---|
| `research` | IDLE 转移 | 目标（goal） | 候选选题（1-N 个） | KB 检索失败 → 转人工 |
| `draft` | research 转移 | 选题 | 文案 + 配图 | LLM 超时 → 重试 3 次 |
| `review` | draft 转移 | 文案 | 评分 + 通过/失败 | 违禁词命中 → 拒收 |
| `revise` | review 失败 | 失败原因 | 新文案 | 次数 > N → 转人工 |
| `schedule` | review 通过 | 通过的内容 | 排期 + 设备 + 账号 | 无可用设备 → 排队 |
| `dispatch` | schedule 转移 | 排期 | N 个 task | 限速 / 设备异常 → 排队 |
| `publish` | dispatch 转移 | task | 平台 note_id + url | 风控 → 隔离 |
| `collect` | publish 成功 | note_id | metrics | 失败 → 30min 后重试 |
| `analyze` | collect 转移 | metrics | 知识库更新 + 策略 | 写库失败 → 重试 3 次 |
| `alert` | 任意失败 | error | 告警 + 隔离 | 通知 + 人工兜底 |

#### 3.3.2 checkpoint 格式

```json
{
  "run_id": "uuid",
  "from_state": "draft",
  "to_state": "review",
  "payload": {
    "topic_id": "uuid",
    "draft": "..."
  },
  "ts": "2026-07-08T10:30:00Z"
}
```

写入 `agent_checkpoints` 表（按 `run_id` + `ts` 索引）。

#### 3.3.3 续跑逻辑

主控启动后扫描 `agent_runs.status = 'running'`，按 `started_at` 升序：

```python
async def resume_runs():
    for run in db.query(AgentRun).filter_by(status='running').all():
        last_cp = get_last_checkpoint(run.id)
        if now() - last_cp.ts > 24h:
            run.status = 'failed'
            run.error = 'checkpoint timeout'
            alert(run, 'checkpoint timeout')
        else:
            state_machine.resume(run, last_cp)
```

#### 3.3.4 LLM 调用规范

- **客户端**：`matrix.llm.AnthropicClient` / `OpenAIClient` 统一封装。
- **超时**：60s（生成）/ 30s（决策）/ 10s（embedding）。
- **重试**：指数退避 1s/3s/9s，最多 3 次。
- **缓存**：相同 prompt 命中本地 cache（LRU 1h TTL）。

---

### 3.4 任务调度子系统

#### 3.4.1 任务模型

```python
@dataclass
class Task:
    id: UUID
    plan_id: UUID
    device_id: UUID
    account_id: UUID
    action: str           # 'device_publish' / 'device_interact' / ...
    payload: dict
    request_id: str       # 幂等 key
    status: str           # pending / running / success / failed / cancelled
    attempts: int
    last_error: dict | None
    scheduled_at: datetime
    executed_at: datetime | None
```

#### 3.4.2 调度器

基于 `asyncio` + 持久化队列（`tasks` 表）。

```python
class Scheduler:
    async def run(self):
        while True:
            now = utcnow()
            ready = db.query(Task).filter(
                Task.status == 'pending',
                Task.scheduled_at <= now,
            ).limit(100).all()
            for task in ready:
                asyncio.create_task(self._dispatch(task))
            await asyncio.sleep(1)

    async def _dispatch(self, task: Task):
        task.status = 'running'
        db.commit()
        try:
            result = await rate_limiter.execute(task)
            task.status = 'success' if result.ok else 'failed'
            task.executed_at = utcnow()
        except Exception as e:
            task.status = 'failed'
            task.last_error = {'code': 'UNEXPECTED', 'message': str(e)}
        db.commit()
```

#### 3.4.3 限速器

见 PRD §6.8，此处补充实现。

```python
class TokenBucket:
    def __init__(self, capacity: int = 30, refill_rate: float = 1/30):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = monotonic()

    async def acquire(self, timeout: float = 600):
        while True:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return
            if timeout <= 0:
                raise RateLimitTimeout()
            sleep = min(1 / self.refill_rate, timeout)
            await asyncio.sleep(sleep)
            timeout -= sleep

    def _refill(self):
        now = monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
```

#### 3.4.4 抖动注入

```python
def jitter_delay(base: float, sigma: float = 0.5) -> float:
    return base * exp(normalvariate(0, sigma))
```

#### 3.4.5 熔断

```python
class CircuitBreaker:
    def __init__(self, window: int = 600, threshold: int = 5, cool_off: int = 1800):
        self.failures: list[float] = []
        self.window = window
        self.threshold = threshold
        self.cool_off = cool_off
        self.open_until: float = 0

    def record_failure(self):
        self.failures.append(monotonic())
        self._prune()
        if len(self.failures) >= self.threshold:
            self.open_until = monotonic() + self.cool_off

    def is_open(self) -> bool:
        return monotonic() < self.open_until
```

---

### 3.5 设备-账号管理子系统

#### 3.5.1 设备注册流程

```
1. APK 启动
2. 检测 Tailscale，未就绪 → 引导用户
3. Tailscale 就绪后获取 tailnet IP
4. 读取本地的 device_id（首次生成 UUID 写入 SharedPreferences）
5. 通过 Tailscale mesh 调主控 `POST /api/v1/devices/register`
6. 主控创建 devices 记录，返回 device 列表（供 UI 展示）
7. APK 启动 Foreground Service + Watchdog
8. 进入心跳循环
```

#### 3.5.2 密钥配对流程

```
1. 主控 UI 触发"添加设备"
2. 主控生成 6 位数字配对码 + 临时 token（5 分钟有效）
3. UI 展示配对码 + 二维码（含主控 tailnet IP + token）
4. APK 端输入配对码（或扫码）
5. APK 调主控 `POST /api/v1/devices/pair` 带配对码
6. 主控校验：device_id 已在 register 步骤预创建 + 配对码匹配
7. 主控通过 Tailscale 通道下发 HMAC 共享密钥（仅一次）
8. APK 用 Android Keystore 加密保存密钥
9. 后续所有指令带 HMAC-SHA256(secret, timestamp + body) 签名
```

#### 3.5.3 设备亲和

- 账号-设备绑定存储在 `accounts.device_id`。
- 任务调度时按 `device_id` 过滤。
- 设备掉线 → 该设备绑定账号的任务进入 `pending` 状态等待恢复。
- **设备恢复后**：pending task 自动按 `scheduled_at` 续跑；状态机本身不动（device 仍属于原 run 的设备亲和）。
- 不跨设备调度（避免触发 XHS 跨设备登录风控）。

#### 3.5.4 登录态维护

```
APK 心跳中携带 XHS 登录状态
  → 已登录：正常
  → 未登录：APK 尝试拉起 XHS 自动恢复
    → 成功：上报"已恢复"
    → 失败：上报"需人工登录" + 触发主控告警
  → 登录态校验：定期调 `XHS /me` 探活
```

人工登录：运营者在监控控制台点击"接管" → APK 启动 Activity 拉起 XHS → 运营者完成登录 → APK 检测登录态恢复。

---

### 3.6 监控子系统

#### 3.6.1 指标采集

- 进程内：通过 `prometheus_client` 暴露 `/metrics`（Python 后端）。
- 系统级：Tauri shell 采集自身指标 + 转发到 Python。
- 设备端：APK 定期上报到主控 → 主控记录 + 转 Exporter。

#### 3.6.2 关键指标

详见 [operations/monitoring-runbook.md](../operations/monitoring-runbook.md)。

#### 3.6.3 Trace

OpenTelemetry SDK，所有关键路径打 span：

- `agent.run.start` / `agent.run.end`
- `agent.state.{from}->{to}`
- `task.dispatch`
- `device.call.{tool_name}`
- `llm.call.{model}`

导出到本地 OTel Collector（端口 4317），后转 Jaeger / Tempo。

#### 3.6.4 日志

结构化 JSON，写入 `~/.matrix/logs/{date}.jsonl`，按大小滚动（每文件 100MB，保留 7 天）。

---

### 3.7 APK 端（执行代理）

#### 3.7.1 组件

| 组件 | 作用 |
|---|---|
| **HTTP Server** | 接收主控指令（OkHttp 嵌入式 server） |
| **AccessibilityService** | 驱动 XHS App |
| **Foreground Service** | 长驻进程 |
| **Watchdog** | 检测进程健康 |
| **Tailscale client** | 维持 mesh 隧道 |
| **Local Keystore** | 加密保存 HMAC 密钥 |

#### 3.7.2 进程结构

```
APK 启动
  → MainActivity（仅首次展示引导）
  → Tailscale Service（独立进程）
  → CompanionService（主服务，AccessibilityService + HTTP Server）
    → HTTP Server（监听 0.0.0.0:8765）
    → AccessibilityService（无障碍事件回调）
  → WatchdogService（每 30s 检查 CompanionService 状态）
```

#### 3.7.3 关键时序：发布一篇笔记

```
主控                          APK
 │                            │
 │ POST /xhs/publish          │
 │ (HMAC + body)              │
 ├──────────────────────────→│
 │                            │ 校验 HMAC
 │                            │ AccessibilityService:
 │                            │   1. 打开 XHS（如果未在前台）
 │                            │   2. 点击"发布" Tab
 │                            │   3. 等待"发布笔记"界面
 │                            │   4. 填标题
 │                            │   5. 填正文
 │                            │   6. 上传图片
 │                            │   7. 添加标签
 │                            │   8. 点击"发布"
 │                            │   9. 等待"发布成功"提示
 │                            │  10. 截图 / 解析 URL
 │ 200 {platform_note_id,url} │
 │←──────────────────────────┤
 │                            │
```

**超时**：单次发布 120s，超时上报 `PUBLISH_TIMEOUT`。

**失败重试**：选择器失效 → VLM 读屏；其他错误 → 3 次指数退避。

---

## 4. 数据流

### 4.1 端到端发布（简图）

```
运营者设定 Goal
  ↓
Agent.RESEARCH  →  KB 检索（topics + history + rules）
  ↓
Agent.DRAFT    →  LLM 生成 + persona 改写
  ↓
Agent.REVIEW   →  违禁词 / 去重 / 拟人化
  ↓
Agent.SCHEDULE →  选时间窗 / 设备 / 账号
  ↓
Agent.DISPATCH →  创建 task
  ↓
Scheduler      →  限速器 / 活跃窗检查
  ↓
APK device_publish
  ↓
APK 无障碍驱动
  ↓
XHS App 发布
  ↓
APK 回报
  ↓
Agent.COLLECT  →  24h 后回采 metrics
  ↓
Agent.ANALYZE  →  写 history + 更新 KB
```

### 4.2 数据回采

```
Scheduler (定时)
  ↓
APK device_collect(scope='recent_24h')
  ↓
APK 截屏 + OCR 解析
  ↓
返回 metrics: {views, likes, collects, comments, follows_gained}
  ↓
写入 note_metrics 表
  ↓
触发 Agent.ANALYZE
```

### 4.3 设备掉线与重连

```
APK Tailscale 失联
  ↓
APK Watchdog 检测
  ↓
APK 指数退避重连 Tailscale（1s/3s/9s/30s）
  ↓
3 次失败 → 标记 tailscale_degraded + 持续尝试
  ↓
心跳经 Tailscale 通道，超时窗口延长到 10min
  ↓
主控检测心跳超时
  ↓
设备标记 offline
  ↓
该设备绑定账号的 task 保持 pending
  ↓
30 分钟未恢复 → 告警 + 通知运营者
```

---

## 5. 状态机详述

### 5.1 节点间转移表

| From | To | Guard | Side effect |
|---|---|---|---|
| IDLE | RESEARCH | 定时触发 / 事件触发 / 人工指令 | 写 checkpoint |
| IDLE | ANALYZE | 事件触发（新评论/数据异常） | 写 checkpoint |
| RESEARCH | DRAFT | 候选选题 ≥ 1 | 写 checkpoint |
| DRAFT | REVIEW | 字数达标 / 关键词覆盖 / persona 一致 | 写 checkpoint |
| REVIEW | SCHEDULE | 违禁词 0 / 相似度 < 阈值 / 拟人化 ≥ 阈值 | 写 checkpoint |
| REVIEW | REVISE | 违禁词 > 0 / 相似度 ≥ 阈值 / 拟人化 < 阈值 | 写 checkpoint |
| REVISE | DRAFT | 次数 < N | 写 checkpoint |
| REVISE | ALERT | 次数 ≥ N | 写 checkpoint |
| SCHEDULE | DISPATCH | 设备 idle / 账号活跃 / 限速允许 | 创建 N 个 task |
| DISPATCH | PUBLISH | task 全部下发成功 | 写 checkpoint |
| PUBLISH | COLLECT | APK 回报成功 | 写 checkpoint |
| PUBLISH | ALERT | APK 回报失败 / 风控 | 写 checkpoint + 告警 |
| COLLECT | ANALYZE | 至少回采阅读量 | 写 checkpoint |
| ANALYZE | IDLE | 知识库更新完成 | 写 checkpoint |
| ALERT | IDLE | 告警已确认 | 写 checkpoint |

### 5.2 超时与强制 break

- 单节点运行超过 10 分钟 → 强制 break + ALERT。
- 单 run 累计超过 24 小时 → 强制 break + ALERT。
- run 内任意节点异常 3 次 → ALERT + 转人工。

### 5.3 持久化

- 每个状态转移写 `agent_checkpoints` 表。
- `agent_runs.current_state` 字段实时更新。
- 重启后扫描 `status='running'` 的 run 续跑。

---

## 6. 接口契约

### 6.1 主控内部 API（Tauri ↔ Python）

详见 [api/master-rest.openapi.yaml](../api/master-rest.openapi.yaml)。

### 6.2 主控 ↔ APK（HTTP over Tailscale）

详见 [api/apk-http.openapi.yaml](../api/apk-http.openapi.yaml)。

### 6.3 Agent MCP Tools

详见 [api/mcp-tools.schema.json](../api/mcp-tools.schema.json)。

---

## 7. 错误处理

### 7.1 错误分类

| 类别 | 错误码 | 策略 |
|---|---|---|
| 临时网络 | `TIMEOUT` / `DEVICE_OFFLINE` | 指数退避 3 次 |
| 选择器失效 | `SELECTOR_NOT_FOUND` | 触发 VLM 读屏 |
| 限速 | `RATE_LIMITED` | 等待令牌 |
| 风控 | `RISK_BLOCKED` | 隔离账号 + 告警 |
| 致命 | `APK_CRASH` / `ADB_LOST` | 设备下线 + 任务重调度 |
| 业务 | `DRAFT_FAILED` / `UPLOAD_FAILED` / `PARSE_FAILED` | 错误分类处理 |
| 配置 | `INVALID_PARAMS` / `MISSING_CONFIG` | 立即失败 + 告警 |

### 7.2 降级链

- 选择器 → VLM 读屏 → 人工
- 商业 VLM → 本地 VLM → 人工
- 主控 A → 主控 B（热备）
- 商业 embedding → 本地 embedding

### 7.3 重试策略

| 错误类别 | 重试次数 | 退避 |
|---|---|---|
| 临时网络 | 3 | 1s/3s/9s |
| 限速 | 无限 | 等令牌 |
| 业务 | 0 | 立即失败 + 告警 |
| 风控 | 0 | 隔离 + 告警 |

---

## 8. 性能与容量

### 8.1 目标

- 单 run 准备阶段 < 1 min
- 单次 APK 发布 < 2 min
- 单 run 总耗时 < 10 min
- 主控支持 100 台设备并发
- 单设备心跳 30s 一次

### 8.2 关键资源估算

| 资源 | 100 台设备 |
|---|---|
| 心跳 QPS | 100 / 30 = 3.3/s |
| LLM 调用 | ~500 calls/天（假设 100 设备 × 5 发布） |
| DB 连接 | ~50（pool size） |
| 内存 | Python 后端 ~500MB / Tauri shell ~300MB |
| 存储 | 100GB/年（notes + metrics） |

详见 [planning/capacity-plan.md](../planning/capacity-plan.md)。

---

## 9. 安全设计

详见 [threat-model.md](./threat-model.md)。要点：

- Tailscale mesh + HMAC 鉴权
- 账号凭据加密存储（Android Keystore + 主机 keyring）
- LLM prompt 注入防护（输入清洗 + 工具调用白名单）
- 通信加密（Tailscale 隧道 + 应用层 HMAC）
- 审计日志（所有写操作）

---

## 10. 扩展点

### 10.1 接入新平台

1. 在 `platforms/` 目录新增平台适配器。
2. 实现 `PlatformAdapter` 接口：`open_app` / `publish` / `interact` / `collect`。
3. APK 端在 `package_mapping.yaml` 加包名。
4. Agent 状态机无需改动（通过 tool name 路由）。

### 10.2 接入新 LLM

1. 在 `matrix.llm` 加新 client 实现 `LLMClient` 接口。
2. 在 `config.yaml` 配置 API key。
3. Agent 通过 `llm.get_client(model_name)` 路由。

### 10.3 接入新 VLM

1. 在 `matrix.vlm` 加新实现。
2. 在 `device_screenshot` 工具的 fallback 配置中指定。

### 10.4 替换 APK 内部实现

只要实现 §6.2 的 HTTP 接口契约即可，Agent 层零改动。

---

## 附录 A：术语表

| 术语 | 含义 |
|---|---|
| **Agent** | LangGraph 状态机编排的自主运营循环 |
| **Companion APK** | 安装在手机上的执行代理 |
| **DERP** | Tailscale 的 NAT 穿透中继 |
| **Headscale** | Tailscale 控制面的开源实现 |
| **MCP** | Model Context Protocol（Agent 工具调用协议） |
| **RAG** | Retrieval-Augmented Generation |
| **Tailnet** | Tailscale mesh 网络 |
| **VLM** | Vision-Language Model（多模态模型） |

## 附录 B：参考

- LangGraph 文档：https://langchain-ai.github.io/langgraph/
- Tailscale 文档：https://tailscale.com/kb/
- Headscale 文档：https://headscale.net/
- OpenTelemetry 文档：https://opentelemetry.io/docs/
- 小红书开放平台（无自动化接口，仅参考）
