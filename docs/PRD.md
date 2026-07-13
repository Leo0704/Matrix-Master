# 产品需求文档（PRD）：AI-Native 自媒体矩阵主控系统

| 项 | 内容 |
|---|---|
| 文档状态 | 决策版 v0.4 |
| 日期 | 2026-07-08 |
| 范围 | 主控桌面应用 + 手机端 companion APK + 知识库 + 自主运营 Agent |
| 平台 | 小红书（单一平台，本期不做多平台） |
| 配套文档 | [docs/README.md](./docs/README.md) |

---

## 1. 文档信息

- **范围**：主控桌面应用、手机端 companion APK、知识库、自主运营 Agent。
- **平台范围**：小红书（单平台深做）。

## 2. 背景与问题

自媒体矩阵运营（多账号、跨设备、规模化内容生产）目前高度依赖人工：

- **操作分散**：每个账号在一台手机上手动打开 App、写文案、配图、发布、互动，账号/设备一多即失控。
- **效率瓶颈**：内容生产慢、跨账号复用难，人写人发的模式无法规模化。
- **关联封号风险**：多账号同 IP、同设备指纹、同文风，易被平台聚类识别、团灭。
- **数据孤岛**：发布数据与复盘分散在各手机，无法统一回收与优化。

现有开源工具多为「网页版单账号自动化」或「纯群控脚本」，缺少一个以知识库为驱动、能自主闭环运营的中枢。本产品定位即填补这一缺口。

## 3. 产品目标

**一句话**：一个主控桌面应用作为大脑，编排多台物理安卓手机（每台挂小红书账号），基于用户维护的知识库，自主完成「选题→生成→发布→互动→数据回收→复盘优化」的运营闭环。

**核心目标（可验证）**
- 用单一主控接入并编排 N 台物理手机。
- 运营者只需维护知识库、设定目标、监控兜底，日常运营由 Agent 自主执行。
- 单设备自动化发布稳定，且不被平台封禁（风控达标）。

**非目标**
- 不做多平台（只小红书）。
- 不做云手机 / 模拟器（只物理手机）。
- 不定位为灰产 / 刷量代运营工具（见 §9）。

## 4. 整体架构

```
┌──────────────────────────────────────────────────────────┐
│  主控桌面应用（唯一大脑 / 编排中枢，本地运行）                │
│  ├─ 知识库（RAG）        品牌/人设/规则/选题/历史数据         │
│  ├─ 自主运营 Agent       LangGraph 状态机 + LLM（多模态）    │
│  ├─ 任务编排与调度        Goal→Plan→Tasks→DeviceDispatch     │
│  ├─ 设备/账号管理         注册表/心跳/绑定/登录态            │
│  └─ 监控控制台            状态/日历/数据/告警/自然语言入口    │
└───────────────┬───────────────────────┬──────────────────┘
                │  Tailscale mesh（带鉴权，跨蜂窝 CGNAT）         │
        ┌───────┴────────┐      ┌────────┴───────┐
        │ 手机① companion│      │ 手机② companion│   ← 哑执行节点
        │ APK（执行代理） │      │ APK（执行代理） │
        │   ↓ 无障碍/UI自动化    │   ↓ 无障碍/UI自动化
        │  XHS App 账号A  │      │  XHS App 账号B  │
        └────────────────┘      └────────────────┘
```

**关键原则**
- 主控是唯一的决策中心；手机与 APK 不决策，只执行。
- APK 是主控操作物理机的**唯一桥梁**：所有对手机的操作都经 APK 下发，不直接裸 ADB 散落调用。
- 能力以「工具」形式抽象（见 §6.2）。

## 5. 手机端 Companion APK（执行代理）

**形态**：自研轻量 companion APK。

**作用**：安装在每台物理手机上，作为主控操作该机的执行端点。

**核心能力**
- 接收主控指令：打开 XHS、发布笔记、点赞/评论/关注、回采数据/截图。
- 通过无障碍服务（AccessibilityService）+ UI 自动化驱动 XHS App。
- 上报设备/App 状态、心跳、执行结果。

### 5.1 设备网络约束

**约束**：手机**仅使用蜂窝移动数据**，禁用 WiFi。
- 多设备共享同一 WiFi 出口 IP 是关联封号的关键风险。
- 每台手机使用独立手机卡 / 独立运营商 / 独立基站小区，最小化 IP 关联。
- APK 启动时强制关闭 WiFi（需 `CHANGE_WIFI_STATE` 权限）。

**对连接的挑战**
- 蜂窝网络普遍有 CGNAT，手机无公网 IP。
- 部分运营商限制入站连接，APK 无法被主控主动拨号。
- 解决：见 §5.2。

### 5.2 控制通道

**主通道：Tailscale mesh**
- 每台手机与主控安装 Tailscale，加入同一 tailnet。
- 移动数据下通过 DERP 中继建立加密点对点通道；运营商只见 TLS 出口流量。
- 主控以手机 tailnet IP（`100.x.x.x:PORT`）拨号，APK 仍以 HTTP 服务形式监听。

**Tailscale 后端**
- Headscale 自托管 + 自建 DERP 中继（国内 VPS 部署）。
- Headscale 是 Tailscale 控制面的开源实现。
- 维护成本：1 台轻量 VPS（2C/2G）+ DERP 容器化部署 + 1 名运维兼职。

**adb USB：开发工具**
- 用途：APK 安装、logcat 抓取、性能分析、问题定位。
- 不作为生产控制通道。

### 5.3 连接生命周期

- **注册**：APK 首次启动 → 启用 Tailscale → 携带 `device_id` + 鉴权 token 向主控注册。
- **密钥配对（首次）**：主控生成临时配对码（QR 码展示在控制台）→ 手机扫码确认 → 主控经 Tailscale 通道下发 HMAC 共享密钥 → APK 用 Android Keystore 加密保存本地。
- **心跳**：APK 每 30s 推送一次（含电量、信号强度、前台 App、错误计数）。
- **掉线重连**：APK 指数退避重连（1s/3s/9s/30s），超过 5 分钟标记离线；Tailscale 异常时窗口延长到 10 分钟。
- **Tailscale 健康检查**：APK 周期性 ping DERP 中继；Tailscale 失联时 APK 唤起 Tailscale app，3 次失败上报主控 + 标记设备 `tailscale_degraded`。
- **鉴权**：每条指令带 HMAC token；APK 仅响应已注册主控的 device_id。
- **主控热备**：主控异常时，APK 60s 内重连到备主控（见 §15）。

**Tailnet 账号管理**
- 单 tailnet：所有手机 + 主控在同一个 tailnet。ACL 隔离通过 Headscale policy 控制。

### 5.4 APK 内部实现
- 语言：Kotlin（Android）。
- 自动化内核：AccessibilityService + UiSelector/资源 ID/内容描述定位。
- 自恢复：Foreground Service + Watchdog，进程被杀 30s 内重启。
- 权限：默认不依赖 root；无障碍服务首次手动授权；Shizuku 可选增强；`INTERNET`（APK HTTP 服务 + Tailscale 通信）；`CHANGE_WIFI_STATE`（强制关闭 WiFi）。
- APK 启动时关闭 WiFi。
- APK 启动时检测 Tailscale Android app 是否安装并加入 tailnet；未就绪时引导用户安装 / 登录。APK 监听 localhost:PORT，由系统 Tailscale 暴露到 tailnet。

### 5.5 接口示例（REST，APK 暴露）
- `GET  /device/status` — 在线/忙碌/异常、当前前台 App、电量、网络
- `POST /app/open` `{package:"com.xingin.xhs"}`
- `POST /action/tap` `{x,y}` 或 `{uiSelector}`
- `POST /action/input` `{text}`
- `POST /action/swipe` `{from,to}`
- `GET  /screen/screenshot` → 图片（供主控 VLM 理解界面）
- `POST /xhs/publish` `{title, content, images[], tags[], visibility}`
- `POST /xhs/interact` `{action, target, request_id}` (action: like / comment / follow / collect / share)
- `POST /xhs/collect_metrics` — 回收当前笔记/账号数据（截图 + OCR 或界面解析）

## 6. 主控应用功能模块

### 6.1 知识库（RAG）
- **内容**：
  - `brand`：品牌名、价值观、产品线、视觉风格。
  - `personas`：每账号人设（名称、语气、简介、发文节奏、违禁词、示范笔记）——**人设隔离是风控关键**。
  - `rules`：平台规则、违禁词库、最佳实践、限流规避要点。
  - `topics`：爆款选题库、季节/热点选题。
  - `history`：历史笔记及其表现数据（阅读/赞/藏/评/涨粉）。
  - `templates`：文案/标题模板。
- **形式**：结构化文档 + 向量库（pgvector / Chroma），提供检索接口供 Agent 调用。

### 6.2 执行工具抽象（MCP tools）

主控把对手机的操作封装为工具，Agent 通过工具调用驱动设备。工具底层统一走 APK，未来换 APK 实现或加 VLM 控屏不影响 Agent 层。

**通用约定**
- 返回结构统一：`{ok, data?, error?: {code, message, retryable}}`。
- 写操作工具带 `request_id` 实现幂等，重试不会重复执行。
- 所有写操作经限速器（见 [docs/architecture/SDD.md §3.4](./docs/architecture/SDD.md)）节流后再下发，Agent 不感知节流。

**工具清单**

| Tool | 输入关键字段 | 输出 | 主要错误码 | 幂等 |
|---|---|---|---|---|
| `device_status` | `device_id` | `{online, busy, app, battery, network}` | `DEVICE_OFFLINE` | 是 |
| `device_open_app` | `device_id, package` | `{ok}` | `APP_NOT_FOUND` | 否 |
| `device_screenshot` | `device_id, region?` | `{image_b64, ts}` | `DEVICE_OFFLINE` | 是 |
| `device_tap` | `device_id, target` | `{ok}` | `SELECTOR_NOT_FOUND`, `TIMEOUT` | 否 |
| `device_input` | `device_id, text` | `{ok}` | `IME_ERROR` | 否 |
| `device_swipe` | `device_id, from, to` | `{ok}` | - | 否 |
| `device_publish` | `device_id, note, request_id` | `{platform_note_id, url}` | `DRAFT_FAILED`, `UPLOAD_FAILED`, `RISK_BLOCKED` | 是 |
| `device_interact` | `device_id, action, target, request_id` | `{ok}` | `RATE_LIMITED`, `RISK_BLOCKED` | 是 |
| `device_collect` | `device_id, scope` | `{metrics[]}` | `PARSE_FAILED` | 是 |
| `device_wait_for` | `device_id, predicate, timeout` | `{matched, snapshot}` | `TIMEOUT` | 是 |

`device_tap.target` 支持 `{x, y}` 或 `{selector, fallback_vlm?}`，主控按序尝试（VLM 读屏见 [docs/architecture/SDD.md §3.3.4](./docs/architecture/SDD.md)）。

### 6.3 自主运营 Agent（核心）

- **定位**：读知识库 → 决策 → 编排 → 执行 → 回采 → 分析 → 更新策略 的闭环。
- **框架**：LangGraph 状态机编排，LLM 负责生成与决策，多模态模型用于读屏理解。
- **能力节点**：
  - 选题生成（检索 `topics`/`history`/`rules`）
  - 文案/脚本生成 + 按 `personas` 人设化改写
  - 发布决策（时机、目标账号、标签）
  - 互动策略（评论回复、关注、点赞节奏）
  - 数据分析与复盘（对比 `history`，更新策略/知识库）
  - 安全审查（违禁词、内容去重、拟人化抖动）前置拦截
- **调度**：定时任务 + 事件驱动（新评论、数据达标/异常触发）。

#### 6.3.1 状态机

**与 tasks 表的关系**：状态机（§6.3.1）是 run 级别，描述 Agent 一次完整运营循环；`tasks` 表是单任务级别，记录原子操作。状态机驱动生成 tasks（如 DISPATCH 节点创建 N 个 publish / interact tasks），tasks 状态反馈驱动状态机转移（如所有 publish tasks 成功 → PUBLISH 节点进入 COLLECT）。

```
IDLE
  ├─ 定时触发 ───────────────→ RESEARCH
  ├─ 事件触发（新评论/数据异常）→ ANALYZE
  └─ 人工指令 ──────────────→ RESEARCH

RESEARCH    检索知识库（topics/history/rules）→ 候选选题
DRAFT       生成文案 + 配图，按 persona 改写
REVIEW      违禁词 / 去重 / 拟人化评分
  ├─ pass → SCHEDULE
  └─ fail → REVISE

REVISE      按失败原因改写文案
  ├─ 次数 < N → DRAFT
  └─ 次数 ≥ N → ALERT
SCHEDULE    选时间窗 / 设备 / 账号
DISPATCH    编排任务，下发到 APK
PUBLISH     等待 APK 回报
  ├─ success → COLLECT
  └─ fail    → ALERT
COLLECT     回采阅读/赞藏/评论
ANALYZE     对比 history，更新知识库与策略 → IDLE
ALERT       上报 + 隔离 + 重调度 / 人工兜底 → IDLE
```

#### 6.3.2 关键 guard
- `DRAFT → REVIEW`：字数达标、关键词覆盖、persona 一致。
- `REVIEW → SCHEDULE`：违禁词 0、相似度 < 阈值、拟人化评分 ≥ 阈值。
- `SCHEDULE → DISPATCH`：设备 idle、账号在活跃窗、限速器允许。
- `PUBLISH → COLLECT`：APK 回报发布成功。
- 单 run 超过 24h 强制 break 并告警。

#### 6.3.3 持久化与恢复
- 每个状态转移写 checkpoint：`{run_id, from, to, payload, ts}` 到 `agent_checkpoints` 表。
- 主控重启后扫描未完成 run，从最后一个 checkpoint 续跑。
- checkpoint 保留 7 天，超期归档。

### 6.4 任务编排与调度

- 任务模型：`Goal → Plan → Tasks → DeviceDispatch`。Goal 长期、Plan 短中期、Tasks 原子。
- 并发与限速：按设备/账号配置并发度与操作间隔，详细规则见 §6.8。
- 失败处理：任务幂等、超时重试、设备掉线重调度、封号隔离，分类与降级见 §6.10。

### 6.5 设备 / 账号管理
- 设备注册表：deviceId、nickname、型号、android_version、tailnet_ip、APK 版本、tags、last_heartbeat、status。
- 账号-设备绑定：账号固定跑在某台手机（设备亲和 = 强约束，避免跨设备登录异常）。设备掉线时该账号的任务**等待设备恢复**，不跨设备调度。
- 设备分组：通过 `devices.tags` 字段标识（品牌 / 产品线 / 运营组），调度时支持按 tag 过滤与批量操作。
- 登录态维护：APK 周期性检测 XHS 登录态；检测到掉线 → APK 上报主控 → 主控通过监控控制台通知运营者 → 运营者触发自然语言指令 / 一键接管 → APK 启动交互式重新登录流程（短信 / 滑块由运营者在手机上完成）。

### 6.6 监控控制台
- 单屏总览：设备/账号状态、内容日历、数据看板、告警。
- 自然语言指令入口：人用自然语言下目标/干预。
- 人工兜底：异常任务可一键接管或暂停。

### 6.7 数据模型

技术实现见 [docs/database/schema.sql](./docs/database/schema.sql) / [docs/database/README.md](./docs/database/README.md)。

### 6.8 限速与拟人模型

技术实现见 [docs/architecture/SDD.md §3.4](./docs/architecture/SDD.md)。

### 6.9 VLM 读屏路径

技术实现见 [docs/architecture/SDD.md §3.3.4](./docs/architecture/SDD.md)。

### 6.10 错误处理与降级

技术实现见 [docs/architecture/SDD.md §7](./docs/architecture/SDD.md)。

### 6.11 可观测性

技术实现见 [docs/operations/monitoring-runbook.md](./docs/operations/monitoring-runbook.md)。

## 7. 关键流程

**主闭环**
1. 运营者设定目标（如「本周美妆号净涨 500 粉」）或 Agent 按日程自启。
2. Agent 检索知识库（选题/人设/规则/历史）。
3. 生成内容 + 人设化改写 + 安全审查（去重/违禁词/拟人）。
4. 编排任务，经 APK 在目标手机发布 / 互动。
5. 回采数据，分析表现，更新知识库与策略。
6. 循环优化。

**初始化流程**
装 APK → 主控发现并注册设备 → 绑定小红书账号 → 灌入知识库 → 跑通首条笔记。

**异常处理**
封号/掉线/任务失败 → 告警 + 隔离 + 重调度 + 人工兜底。

## 8. 非功能性需求

- **风控（最高优先级）**：每台设备独立 IP（流量卡/代理）、独立设备指纹、内容去重、操作拟人抖动、人设隔离避免语言指纹聚类。
- **可靠性**：APK 自恢复（守护进程）、主控崩溃可恢复、任务幂等。
- **安全**：主控↔APK 通信加密 + 指令鉴权、账号凭据隔离存储。
- **可观测性**：全链路日志、指标、执行 trace，便于排查封号/失败。
- **性能**：支持 MVP 单设备；架构预留到数十/百级设备并发。

## 9. 合规与产品边界

- **产品定位**：「自有账号内容运营提效工具」。
- **明确禁止**：刷量、虚假互动、绕过平台限制的越权能力。内置频率控制与合规提示，运营者须对账号合规负责。
- 小红书明确禁止自动化发布与刷量；本项目所有开源参考均带「学习研究」免责声明，本产品延续该边界。
- 不提供规避平台风控的越权能力；频率与行为模拟以「拟人合规」为底线。

## 10. 技术选型

| 层 | 选型 |
|---|---|
| 主控形态 | Web frontend（React + vite）+ Python 后端（uvicorn） |
| 前端框架 | React + vite（浏览器访问 http://localhost:1420） |
| Agent 编排 | LangGraph |
| LLM | 商业 API（Anthropic / OpenAI） |
| 知识库 | PostgreSQL + pgvector |
| Embedding | 商业 embedding API |
| 多模态 | 商业 VLM API |
| 手机端 APK | 自研轻量 companion APK |
| 通信 | Tailscale mesh + HMAC 鉴权 |
| 设备 mesh | Headscale 自托管 + 自建 DERP |
| 开发工具 | adb USB |
| 任务调度 | asyncio + 持久化队列 |
| 监控 | OpenTelemetry + Jaeger / Tempo |
| 前端打包 | vite build（纯静态资源；浏览器访问或被任意静态服务器托管） |

详细选型理由见 [docs/architecture/SDD.md §2.1](./docs/architecture/SDD.md)。

## 11. 里程碑 / MVP

**MVP（最小可验证闭环）**
- 1 主控桌面应用 + 1 手机 + 1 小红书账号 + 1 自研轻量 companion APK。
- 跑通：`主控据知识库生成 1 篇笔记 → 经 APK 在真机发布 → 回采阅读/赞藏数据 → 落库`。
- **验证最硬假设**：真机经 APK 自动发布稳定且不被封。

**MVP In-Scope**
- 单设备 / 单账号 / 单知识库 / 单 persona。
- 主控桌面应用核心模块：知识库 / Agent / 任务编排 / 设备-账号管理 / 监控控制台。
- Tailscale mesh 接入：主控 + 1 手机加入同一 tailnet，主控可经 tailnet IP 拨号到 APK HTTP 服务。
- Headscale 部署引导：主控带 setup wizard，引导用户填 VPS 信息（IP / SSH 凭据）后自动 docker-compose 部署 Headscale + 自建 DERP 中继；MVP 用户无需手写配置文件。
- 1 条端到端流程：知识库 → 生成 → 审核 → 发布 → 回采 → 入库。
- MCP tools：`device_status` / `device_open_app` / `device_screenshot` / `device_tap` / `device_input` / `device_publish` / `device_collect`。
- Agent 状态机：`IDLE / RESEARCH / DRAFT / REVIEW / SCHEDULE / DISPATCH / PUBLISH / COLLECT / ANALYZE`。
- 选择器：首页 / 发布页 / 个人页 三个核心界面。
- 本地 PostgreSQL + pgvector；本地 OpenTelemetry trace。

**MVP Out-of-Scope**
- 多设备 / 多账号 / 设备管理 UI。
- 互动闭环（评论 / 关注 / 点赞）。
- VLM 读屏（仅预留接口）。
- 知识库自更新与策略自动调优。
- 跨设备任务重调度、设备熔断、账号熔断。
- 灰度发布、APK OTA、主控自更新。
- 服务端化部署 / 多租户 / 鉴权中心。

**验收用例（MVP Definition of Done）**
1. 启动主控 → 注册 1 台设备 → 心跳正常。
2. 灌入 1 份知识库（1 persona + 1 topic + 5 rules）。
3. 触发 Agent：跑通 RESEARCH → DRAFT → REVIEW → SCHEDULE → DISPATCH → PUBLISH → COLLECT → ANALYZE → IDLE 完整链路。
4. 笔记成功发布到 XHS，URL 写入 `notes.platform_url`。
5. 回采阅读/赞/藏数据，写入 `note_metrics`。
6. 重启主控，从 checkpoint 续跑未完成任务，行为正确。
7. 选择器失效时触发告警，APK 进程崩溃后 30s 内自动恢复。
8. 单 run 总耗时 < 10 min（其中 Agent 准备 < 1 min、APK 发布执行 < 2 min），发布成功率 ≥ 95%。

**后续里程碑**
1. 多设备 / 多账号接入，设备管理与调度。
2. 互动闭环（评论/关注/点赞）+ 数据分析复盘。
3. 知识库自更新 + Agent 自主优化。
4. 规模化风控（IP/指纹/拟人）与机群监控。

## 12. 风险与缓解

- **风控 / 封号（最高风险）**：独立 IP、设备指纹隔离、内容去重、操作拟人抖动、人设隔离避免语言指纹聚类。
- **XHS App 更新致选择器失效**：选择器集中管理 + 快速修复机制 + 版本适配回归。
- **APK 权限受限**：无障碍服务首次手动授权；默认不依赖 root，Shizuku 为可选增强。
- **LLM 成本 / 延迟**：缓存、批处理、本地小模型、读屏结果复用。
- **合规**：内置频率控制与合规提示，明确产品边界，禁越权能力。

详细缓解措施见 [docs/operations/monitoring-runbook.md](./docs/operations/monitoring-runbook.md) / [docs/architecture/threat-model.md](./docs/architecture/threat-model.md)。

## 13. 成功标准

- **MVP**：单设备自动发布成功率 ≥ 95% 且 7 日内不被封。
- **产品**：N 个账号自主运营，人工干预率 < 10%，涨粉/互动达标且账号存活率达标。

---

## 14. 测试策略

详见 [docs/testing/test-plan.md](./docs/testing/test-plan.md)。

## 15. 部署与运行

详见 [docs/deployment/runbook.md](./docs/deployment/runbook.md) / [docs/deployment/release-process.md](./docs/deployment/release-process.md)。

---

*参考开源：uiautomator2、AndroidRPA(Yyds.Auto)、xiaohongshu-mcp-python、ReaJason/xhs、MediaCrawler、xhs_ai_publisher。*
