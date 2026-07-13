# 发布流程

| 项 | 内容 |
|---|---|
| 适用对象 | 发布负责人 / 团队 lead |
| 配套 | [runbook.md](./runbook.md) / [monitoring-runbook.md](../operations/monitoring-runbook.md) |

## 1. 版本号

语义化版本：`vMAJOR.MINOR.PATCH`

- `MAJOR`：架构变更 / 破坏性 API
- `MINOR`：新功能
- `PATCH`：bug 修复

示例：v0.3.0（当前）

## 2. 发布窗口

| 组件 | 频率 | 窗口 |
|---|---|---|
| Python 后端 | 双周 | 周二 / 周四 14:00-16:00 |
| Web frontend（React + vite） | 跟随 Python 后端 | 同时 |
| APK | 月度（XHS 改 UI 时紧急） | 月初 |
| Headscale / DERP | 按需 | 任意 |
| 知识库 schema | 跟随 Python 后端 | 同时 |

## 3. 发布检查清单

### 3.1 预发布

- [ ] 单元测试通过（≥ 80% 覆盖）
- [ ] 集成测试通过
- [ ] E2E 测试通过（最近 24h 内）
- [ ] 性能基线无回归
- [ ] 文档更新（CHANGELOG.md / API 规范 / SDD）
- [ ] 备份最近一次成功
- [ ] 回滚脚本就绪

### 3.2 发布中

- [ ] 通知运营者
- [ ] 监控 1h 无异常
- [ ] 验证关键路径（注册 / 发布 / 互动 / 回采）

### 3.3 发布后

- [ ] 24h 后回顾指标
- [ ] 关闭 milestone
- [ ] 写 release notes

## 4. 发布流程

### 4.1 Python 后端 + Web frontend

```
1. 创建 release branch
   git checkout -b release/v0.4.0

2. 更新版本号
   - backend/matrix/__init__.py
   - shell/package.json

3. 更新 CHANGELOG.md

4. 跑完整测试
   pytest backend/tests/
   ./build.sh  # 构建 wheel

5. Tag
   git tag -a v0.4.0 -m "release v0.4.0"
   git push origin v0.4.0

6. CI 自动：
   - 跑测试
   - 构建 wheel
   - 构建 Web frontend（vite build）
   - 上传 GitHub Release

7. 灰度发布
   - 第一周：5% 用户（内部团队）
   - 第二周：30%
   - 第三周：100%

8. 监控关键指标 24h
```

### 4.2 APK

```
1. 更新 versionCode + versionName
   - apk/app/build.gradle.kts

2. 构建 release APK
   cd apk
   ./gradlew assembleRelease

3. 签名
   - 用 keystore 签名
   - 校验签名（apksigner verify）

4. 内部测试
   - 上传到 Firebase App Distribution
   - 5 设备真机跑 24h

5. 通过后批量推
   - 主控触发 APK 推送（见 runbook §2.9）
   - 升级流程：用户点确认安装
```

### 4.3 Headscale

```
1. 看官方 release notes
2. 备份数据库
3. 拉新镜像
4. 重启
5. 验证
```

## 5. 灰度策略

### 5.1 设备分批

| 批次 | 设备数 | 监控时长 | 升级条件 |
|---|---|---|---|
| 1 | 1（内部） | 24h | 无 P0 |
| 2 | 10% | 48h | 无 P0/P1 |
| 3 | 50% | 48h | 无 P0/P1 |
| 4 | 100% | - | - |

### 5.2 升级条件

每批次升级需满足：
- 24h 内无 P0 告警
- 任务成功率 ≥ 95%
- LLM 成本无异常增长
- 心跳 / 选择器无异常

## 6. 回滚

### 6.1 回滚触发

- P0 告警 5 分钟内未恢复
- 任务失败率突增 > 30%
- 设备掉线率突增 > 20%
- 任何安全事件

### 6.2 回滚步骤

```bash
# Python 后端
pip install matrix==<previous_version>
systemctl restart matrix  # 或重启后端

# Web frontend
# 切回旧版本 vite build 输出（`git checkout <tag> -- shell/dist/`）

# APK
# 主控推旧版本 APK，用户确认安装
```

### 6.3 回滚时限

- 自动回滚：P0 触发后 30 分钟内
- 人工回滚：P1 触发后 4 小时内
- 不回滚：P2 / P3，下个版本修复

## 7. 紧急修复流程

### 7.1 触发场景

- P0 安全漏洞
- 重大功能 bug
- XHS App 紧急更新导致选择器失效

### 7.2 流程

```
1. 团队 lead 评估
2. 跳过常规发布窗口
3. 紧急 tag + push
4. 灰度只跑内部设备
5. 24h 观察
6. 决定：灰度扩量 / 回滚
```

## 8. CHANGELOG 规范

格式（[Keep a Changelog](https://keepachangelog.com)）：

```markdown
# Changelog

## [0.4.0] - 2026-07-22

### Added
- 新增 XXX 功能
- 接入 YYY 模型

### Changed
- 优化 ZZZ 性能
- 调整限速器默认参数

### Fixed
- 修复某条件下选择器失效

### Security
- HMAC 密钥轮换机制
```

## 9. Release Notes

每次发版给运营者一份通俗说明：

```
【v0.4.0 更新说明】

新功能：
- 现在可以批量管理 persona
- 监控面板新增账号风险分布图

修复：
- 修了个偶发的发布超时
- 设备掉线时任务不会丢

注意事项：
- 升级后请重启主控
- APK 升级会弹窗，请点确认

回滚：如有问题 24h 内联系技术
```

## 10. 发布后回顾

每次发版后 1 周做 post-release review：
- 实际指标 vs 预期
- 客户反馈
- 改进点
