# AI-Native 自媒体矩阵主控系统

> 一个主控桌面应用编排多台物理安卓手机，基于用户维护的知识库，自主完成小红书运营闭环（选题 → 生成 → 发布 → 互动 → 回采 → 复盘）。

---

## 状态

- **当前版本**：v0.6.0（v0.5 闭环 349 测试全绿 + 互动闭环 MVP：发后流量互推 like + comment）
- **文档**：完整（22 份），详见 [docs/README.md](./docs/README.md)
- **代码**：脚手架阶段

## 项目结构

```
.
├── PRD.md                      产品需求文档（v0.4）
├── LICENSE                     MIT
├── CHANGELOG.md                版本历史
├── README.md                   本文件
│
├── backend/                    Python 后端（主控核心）
│   └── matrix/                 业务代码
│       ├── agent/              LangGraph 状态机
│       ├── kb/                 知识库 + RAG
│       ├── scheduler/          任务调度
│       ├── device/             设备-账号管理
│       ├── monitoring/         监控 / OTel
│       ├── api/                内部 REST API
│       ├── db/                 数据库连接 / 迁移
│       └── llm/                LLM 客户端封装
│
├── shell/                      Tauri shell（待建）
│
├── apk/                        Companion APK（待建）
│
├── docs/                       完整技术文档（19 份）
│   ├── README.md               文档集索引
│   ├── architecture/           SDD + threat-model
│   ├── api/                    OpenAPI × 2 + JSON Schema × 1
│   ├── database/               schema.sql + README
│   ├── development/            dev-setup
│   ├── testing/                test-plan + kb-writing-guide
│   ├── user/                   manual
│   ├── deployment/             runbook + release-process
│   ├── operations/             monitoring + postmortem + faq
│   └── planning/               capacity + cost-model
│
├── Dockerfile                  后端镜像（默认 CMD = pytest；backend service 用 command 覆盖为 uvicorn）
├── docker-compose.yml          开发环境（PostgreSQL + Headscale + DERP + backend + frontend + test）
```

## 快速开始

### 前置

- Docker + Docker Compose（基础设施 + 后端 + 前端 vite dev server）
- Rust toolchain（仅 host 跑 Tauri Rust 进程 + WebView 用）
- [Tauri CLI v2](https://tauri.app/start/prerequisites/)（`cargo install tauri-cli --version "^2.0" --locked`）
- Git

### 安装与启动

```bash
git clone <repo-url> matrix
cd matrix

# 1. 起基础设施（PostgreSQL + Headscale + DERP）
docker compose up -d postgres headscale derp

# 2. 起后端 + 前端 vite dev server（都在 docker 内）
docker compose up -d backend frontend

# 3. 应用数据库迁移（容器内跑）
docker compose run --rm test alembic upgrade head

# 4. 在 host 上启动 Tauri 桌面应用（Rust 进程 + WebView）
cd shell
npm install         # 装 @tauri-apps/cli（在 devDependencies）
npx tauri dev       # Tauri CLI 启动 Rust 进程 + 创建 WebView 加载 localhost:1420
```

> **为什么 Tauri Rust 进程必须在 host 跑？**
> Tauri 用系统 WebView（macOS WKWebView / Windows WebView2），需要 Cocoa/Win32 API，docker 容器无法承载 GUI。
> Rust 进程创建 WebView 窗口，**加载 host 上 localhost:1420** → 经 docker port mapping 转到 frontend 容器内的 vite。
> Rust 进程**调用 host 上 localhost:8666** → 经 port mapping 转到 backend 容器内的 uvicorn。

### 验证

```bash
# 后端健康检查
curl http://localhost:8666/api/v1/health

# 前端 vite dev server 已起来（host 上）
curl -I http://localhost:1420

# 数据库
psql -h localhost -U matrix -d matrix

# 跑测试（容器内）
docker compose run --rm test                          # 全部
docker compose run --rm test pytest tests/test_x.py   # 单文件
docker compose run --rm test pytest tests -k agent    # 按关键字
```

详细开发环境搭建见 [docs/development/dev-setup.md](./docs/development/dev-setup.md)。

## 角色入口

- **老板 / 决策者**：[PRD.md](./PRD.md) / [docs/planning/cost-model.md](./docs/planning/cost-model.md)
- **后端开发**：[docs/architecture/SDD.md](./docs/architecture/SDD.md) / [docs/api/](./docs/api/) / [docs/database/schema.sql](./docs/database/schema.sql)
- **APK 开发**：[docs/api/apk-http.openapi.yaml](./docs/api/apk-http.openapi.yaml) / [docs/architecture/SDD.md §3.7](./docs/architecture/SDD.md)
- **运维 / on-call**：[docs/deployment/runbook.md](./docs/deployment/runbook.md) / [docs/operations/monitoring-runbook.md](./docs/operations/monitoring-runbook.md)
- **运营者**：[docs/user/manual.md](./docs/user/manual.md) / [docs/operations/faq.md](./docs/operations/faq.md)
- **安全 / 合规**：[docs/architecture/threat-model.md](./docs/architecture/threat-model.md)

## 里程碑

| 版本 | 内容 | 状态 |
|---|---|---|
| v0.1 | 项目立项 | ✅ |
| v0.2 | 技术选型与架构初版 | ✅ |
| v0.3 | 详细设计扩展 | ✅ |
| v0.4 | 完整文档集 | ✅ |
| v0.5 | Backend 脚手架 + db/llm/monitoring/scheduler 4 模块 | ✅ |
| v0.6 | 互动闭环 MVP（发后流量互推 like + comment） | ✅ 当前 |
| v0.7 | MVP 端到端跑通（含 APK 真机联调） | ⏳ |
| v1.0 | 多设备 + 互动全动作（follow/share/collect）+ 日常养号 | ⏳ |

## 贡献

代码风格 / PR 流程见 [docs/development/dev-setup.md](./docs/development/dev-setup.md) §9。

## 许可证

MIT — 详见 [LICENSE](./LICENSE)
