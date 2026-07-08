# 运营者 FAQ

| 项 | 内容 |
|---|---|
| 适用对象 | 运营者 / 客服 |
| 配套 | [user-manual.md](../user/manual.md) / [monitoring-runbook.md](./monitoring-runbook.md) |

## 启动 / 安装

**Q：主控打不开？**

A：
1. 看 `~/.matrix/logs/` 最新日志
2. 重启主控
3. 卸载重装

**Q：首次启动卡住？**

A：setup wizard 在等 Headscale 部署。检查：
- VPS 是否可达
- SSH 凭据是否正确
- Docker 是否在 VPS 上安装

**Q：APK 装不上？**

A：
1. 检查 Android 版本 ≥ 10
2. 检查"未知来源安装"已开启
3. 重装 Tailscale 后再装 APK

## 设备

**Q：设备不在线？**

A：
1. 检查手机是否开机 / 有电
2. 检查手机信号（蜂窝）
3. 检查 Tailscale：`adb shell` 后 `tailscale status`
4. 等 5 分钟看是否自动恢复

**Q：设备显示 tailscale_degraded？**

A：
1. 切换飞行模式再切回
2. 重启 Tailscale app
3. 严重时重启手机
4. 30 分钟未恢复：通知运维

**Q：APK 反复被杀？**

A：
1. 检查电池优化：把 APK 加入"不优化"白名单
2. 锁定 APK 在最近任务（防止清理）
3. 检查"自启动"权限

## 账号

**Q：账号登录失败？**

A：
1. 滑块 / 短信验证：人工完成
2. 提示"设备异常"：换手机或换 IP
3. 提示"账号异常"：可能是触发风控，等待或弃用

**Q：账号被封？**

A：立即暂停 + 通知运营。**不要**立刻尝试登录新设备。详见 [monitoring-runbook §3.2](./monitoring-runbook.md)。

**Q：账号风险分数高？**

A：
1. 查 `risk_signals` 表：触发原因
2. 减少操作频次
3. 暂停 24h

## 内容

**Q：笔记发布失败？**

A：看错误码：
- `DRAFT_FAILED`：内容含违禁词 / 选择器失效
- `UPLOAD_FAILED`：图片过大 / 格式不支持
- `RISK_BLOCKED`：账号触发风控
- `TIMEOUT`：网络问题，重试

**Q：选择器失效？**

A：
1. 自动触发 VLM 读屏降级
2. VLM 也不识别：标记需人工
3. 紧急：人工在手机上完成

**Q：发布后看不到笔记？**

A：
1. 等 1-2 分钟（平台审核）
2. 检查 `notes.platform_url` 是否有效
3. 直接在小红书 app 内搜索

**Q：内容生成质量差？**

A：
1. 检查 persona 是否丰富
2. 检查 topic 是否相关
3. 调整 LLM 模型（Sonnet 质量更好但更贵）
4. 在 prompt 加 few-shot 例子

## 数据

**Q：metrics 没回采到？**

A：
1. 笔记可能太新（曝光延迟）
2. 截图 OCR 失败
3. 重试：24h 后再 collect

**Q：数据看板空白？**

A：
1. 检查时间范围
2. 检查过滤条件
3. 看 metrics 任务是否成功

## 知识库

**Q：改 persona 后 Agent 没用到？**

A：
1. 改后需要 1 分钟重建索引
2. 检查 Agent 是否引用了新版本
3. 试"知识库测试"功能检索

**Q：rule 不生效？**

A：
1. 检查 rule severity
2. 检查判据是否清晰
3. 看 review 节点的日志

## 性能

**Q：发布很慢？**

A：
1. 检查网络（主控 / 手机 / DERP）
2. 看 LLM 延迟（可能是 LLM 慢）
3. 看 APK 截图延迟

**Q：主控卡顿？**

A：
1. 检查 CPU / 内存（监控面板）
2. 关闭不用的 task
3. 重启主控

## 应急

**Q：账号被大规模封？**

A：立即：
1. 全部账号暂停
2. 通知运维
3. 排查共因（IP / 内容 / 行为）
4. 评估是否切换账号池

**Q：主控挂了但设备还在跑？**

A：设备会按本地任务缓存继续，但不会接收新指令。修主控：
1. 拉日志
2. 重启
3. 主控恢复后自动重连

**Q：Headscale 失联？**

A：
1. 备用 DERP 自动接管
2. 完全失联：远程 SSH VPS
3. 重启 Headscale：`docker compose restart headscale`

## 联系

紧急：触发告警 → 通知
非紧急：邮件 / 文档
