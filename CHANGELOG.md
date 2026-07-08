# Changelog

本文档记录项目的所有重要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

## [Unreleased]

### 计划
- 多设备 / 多账号接入
- 互动闭环（评论 / 关注 / 点赞）
- 知识库自更新
- 规模化风控

---

## [0.4.0] - 2026-07-08

### Added
- 完整技术文档集（**22 份**）覆盖架构 / API / 数据库 / 开发 / 测试 / 用户 / 部署 / 运维 / 规划
  - 产品 1 份：PRD.md
  - 仓库 2 份：LICENSE（MIT）+ CHANGELOG.md
  - architecture/ 2 份：SDD.md + threat-model.md
  - api/ 3 份：apk-http.openapi.yaml + master-rest.openapi.yaml + mcp-tools.schema.json
  - database/ 2 份：schema.sql + README.md
  - development/ 1 份：dev-setup.md
  - testing/ 2 份：test-plan.md + kb-writing-guide.md
  - user/ 1 份：manual.md
  - deployment/ 2 份：runbook.md + release-process.md
  - operations/ 3 份：monitoring-runbook.md + postmortem-template.md + faq.md
  - planning/ 2 份：capacity-plan.md + cost-model.md
- 系统设计文档（SDD）：模块设计 / 数据流 / 状态机 / 接口契约 / 错误处理 / 安全 / 扩展点
- 威胁模型：STRIDE 分类的攻击面 / 缓解 / 责任
- 完整数据库 DDL（schema.sql）：23 张表（含 app_config）+ 时序分区 + 触发器 + 视图
- 三个 API 规范：APK HTTP / 主控 REST / MCP tools JSON Schema
- 监控 Runbook：6 类告警 + 应急处理
- 部署 Runbook：Headscale / DERP / VPS / 备份 / 恢复
- 容量规划：MVP / 早期 / 成长 / 中等 / 大型 5 个场景
- 成本模型：LLM / VLM / VPS / 移动数据 4 类成本的详细估算
- 自然语言指令入口（运营者通过对话下目标 / 干预）
- 知识库评审流程：按 persona / rule severity 分级 review

### Changed
- 通信架构从 USB + WiFi LAN 改为 Tailscale mesh（解决多设备规模化与 CGNAT 限制）
- Tailscale 后端从 SaaS 改为 Headscale 自托管 + 自建 DERP（解决国内 DERP 访问）
- Tailnet 账号管理：单 tailnet 团队共用
- 设备-账号绑定：设备亲和强约束（掉线等待恢复，不跨设备调度）
- Agent 状态机：9 个状态 + guard 条件 + checkpoint 持久化
- 限速模型：令牌桶 + 抖动 + 活跃窗 + 频次上限
- 错误处理：5 类错误 + 降级链 + 熔断机制
- MCP tools 完整化：10 个工具 + 错误码 + 幂等
- 监控指标：补全 P50/P95 / Tailscale DERP 切换 / URL 可达性
- 桌面框架：Tauri shell + Python 后端（前后端分离）
- 任务调度：asyncio + 持久化队列

### Fixed
- 解决多设备共享 WiFi 出口 IP 关联封号的风险
- 解决设备掉线时跨设备调度触发 XHS 风控的问题
- 解决 HMAC 共享密钥下发路径不明确的问题
- 解决 APK 进程存活但 Tailscale 失联的检测盲区

### Security
- HMAC 共享密钥生命周期管理（生成 / 下发 / 存储 / 轮换 / 撤销）
- APK 权限收紧：必须 `INTERNET` + `CHANGE_WIFI_STATE`
- 审计日志：所有写操作可追溯
- LLM prompt injection 防护：输入清洗 + 工具白名单 + schema 校验

### Removed
- §13 决策记录（决策已内嵌各章节，单独维护易漂移）
- §6.7-6.11 技术细节（已迁移至 docs/architecture/SDD.md 等）
- §14 测试策略（已迁移至 docs/testing/test-plan.md）
- §15 部署与运行（已迁移至 docs/deployment/runbook.md）
- 决策过程痕迹（"已决策" / "已评估但未采纳" / "备选" / "理由" 列等）

---

## [0.3.0] - 2026-07-08

### Added
- 数据模型（16 张表）
- MCP tools 详细接口（10 个工具 / 错误码 / 幂等）
- Agent 状态机（9 个状态）
- 限速与拟人模型
- 错误处理与降级机制
- 可观测性（日志 / 指标 / trace）

### Changed
- 扩展 §6.7-6.11
- 强化 §10 技术选型理由

---

## [0.2.0] - 2026-07-08

### Added
- companion APK 形态定义
- 通信方式双通道（USB + WiFi LAN）
- APK 内部实现（方案 A）
- REST 接口示例
- 主控应用功能模块初版

---

## [0.1.0] - 2026-07-08

### Added
- 项目立项
- 产品定位（AI-Native 自媒体矩阵主控系统）
- 平台范围（小红书单平台）
- 整体架构图
- 关键原则（主控唯一决策 / APK 唯一桥梁 / 工具抽象）
- 决策记录（后于 v0.4.0 删除）

---

## 版本约定

- **MAJOR**：架构变更 / 破坏性 API
- **MINOR**：新功能
- **PATCH**：bug 修复

详细发布流程见 [docs/deployment/release-process.md](./docs/deployment/release-process.md)。
