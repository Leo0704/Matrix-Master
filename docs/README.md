# 文档集索引

本文档是 **AI-Native 自媒体矩阵主控系统** 完整技术文档的入口。文档按角色与目的组织，每份文档都有明确的读者与使用场景。

---

## 文档地图

| 类别 | 文档 | 主要读者 | 何时查阅 |
|---|---|---|---|
| **产品** | [PRD.md](../PRD.md) | 产品 / 老板 / 投资人 | 了解产品范围与目标 |
| **架构** | [architecture/SDD.md](./architecture/SDD.md) | 开发 / 架构师 | 写代码前的设计依据 |
| **架构** | [architecture/threat-model.md](./architecture/threat-model.md) | 安全 / 开发负责人 | 设计安全方案 / 安全评审 |
| **API** | [api/apk-http.openapi.yaml](./api/apk-http.openapi.yaml) | APK 开发 / Agent 开发 | 对接 APK HTTP 接口 |
| **API** | [api/master-rest.openapi.yaml](./api/master-rest.openapi.yaml) | 前端 / 外部集成 | 对接主控 REST 接口 |
| **API** | [api/mcp-tools.schema.json](./api/mcp-tools.schema.json) | Agent 开发 | 实现 / 校验 MCP tool |
| **数据库** | [database/schema.sql](./database/schema.sql) | 后端开发 | 写 migration / 调试 SQL |
| **数据库** | [database/README.md](./database/README.md) | 后端开发 / DBA | 了解表结构与设计意图 |
| **开发** | [development/dev-setup.md](./development/dev-setup.md) | 新入职开发 | 从 0 到跑通 hello world |
| **部署** | [deployment/runbook.md](./deployment/runbook.md) | 运维 / 部署 | 上线 / 改配置 / 故障恢复 |
| **部署** | [deployment/release-process.md](./deployment/release-process.md) | 发布负责人 | 发版 / 灰度 / 回滚 |
| **运维** | [operations/monitoring-runbook.md](./operations/monitoring-runbook.md) | 运维 / on-call | 收到告警后怎么办 |
| **运维** | [operations/postmortem-template.md](./operations/postmortem-template.md) | on-call / 团队 lead | 故障复盘 |
| **运维** | [operations/faq.md](./operations/faq.md) | 运营者 / 客服 | 高频问题速查 |
| **用户** | [user/manual.md](./user/manual.md) | 运营者 | 学习使用主控 |
| **测试** | [testing/test-plan.md](./testing/test-plan.md) | QA / 开发 | 写测试 / 验收 |
| **知识** | [testing/kb-writing-guide.md](./testing/kb-writing-guide.md) | 运营者 / 知识库管理员 | 写 persona / rule / topic |
| **规划** | [planning/capacity-plan.md](./planning/capacity-plan.md) | 架构师 / 老板 | 评估规模化可行性 |
| **规划** | [planning/cost-model.md](./planning/cost-model.md) | 老板 / 运营 | 评估商业可行性 |

---

## 按角色速查

### 我是开发，要写代码
1. [PRD.md](../PRD.md) — 先了解产品
2. [architecture/SDD.md](./architecture/SDD.md) — 系统设计
3. [api/](./api/) — 接口规范
4. [database/](./database/) — 数据模型
5. [development/dev-setup.md](./development/dev-setup.md) — 搭环境
6. [testing/test-plan.md](./testing/test-plan.md) — 测试要求

### 我是运维 / on-call
1. [deployment/runbook.md](./deployment/runbook.md) — 部署
2. [operations/monitoring-runbook.md](./operations/monitoring-runbook.md) — 监控
3. [operations/postmortem-template.md](./operations/postmortem-template.md) — 复盘
4. [deployment/release-process.md](./deployment/release-process.md) — 发布

### 我是运营者
1. [user/manual.md](./user/manual.md) — 使用手册
2. [testing/kb-writing-guide.md](./testing/kb-writing-guide.md) — 知识库写作
3. [operations/faq.md](./operations/faq.md) — 常见问题

### 我是老板 / 决策者
1. [PRD.md](../PRD.md) — 产品范围
2. [planning/capacity-plan.md](./planning/capacity-plan.md) — 容量上限
3. [planning/cost-model.md](./planning/cost-model.md) — 成本估算
4. [architecture/threat-model.md](./architecture/threat-model.md) — 风险

---

## 文档维护

- **版本**：本文档集跟随主仓库版本，重大变更同步更新版本号。
- **反馈**：发现问题请在 issue 中标注 `docs:` 前缀。
- **更新规则**：
  - 代码变更必须同步更新对应 API 规范 / DDL / SDD。
  - PRD 变更必须由产品确认后修改。
  - 运维 / 监控 / 部署文档必须由对应负责人 review。
