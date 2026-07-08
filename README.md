# AI-Native 自媒体矩阵主控系统

> 一个主控桌面应用编排多台物理安卓手机，基于用户维护的知识库，自主完成小红书运营闭环（选题 → 生成 → 发布 → 互动 → 回采 → 复盘）。

---

## 状态

- **当前版本**：v0.5.0（开发启动，Batch 1 完成：db / llm / monitoring / scheduler）
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
└── docker-compose.yml          开发环境（PostgreSQL + Headscale + DERP）
```

## 快速开始

### 前置

- Python 3.11+
- Poetry
- Docker + Docker Compose
- Git

### 安装

```bash
git clone <repo-url> matrix
cd matrix

# Python 依赖
cd backend
poetry install

# 启动开发数据库 + Tailscale 后端
cd ..
docker compose up -d
```

### 验证

```bash
# 健康检查（后端在 8666）
curl http://localhost:8666/api/v1/health

# 数据库
psql -h localhost -U matrix -d matrix

# 跑测试
cd backend
pytest
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
| v0.5 | Backend 脚手架 + db/llm/monitoring/scheduler 4 模块 | 🚧 当前 |
| v0.6 | 上层业务模块（kb/device/api/agent） | ⏳ |
| v0.7 | MVP 端到端跑通 | ⏳ |
| v1.0 | 多设备 + 互动闭环 | ⏳ |

## 贡献

代码风格 / PR 流程见 [docs/development/dev-setup.md](./docs/development/dev-setup.md) §9。

## 许可证

MIT — 详见 [LICENSE](./LICENSE)
