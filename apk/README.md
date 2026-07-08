# Matrix Companion APK

> 一台被装在你手机里的"哑巴仆人"。电脑发指令，它就照做——打开小红书、点这里、输那段字、截这张图、发这篇笔记。

跟随主控 `docs/architecture/SDD.md § 3.7` 的设计实现，契约 1:1 落在 `docs/api/apk-http.openapi.yaml`。

---

## 编译

```bash
cd apk/
./gradlew :app:assembleDebug          # → app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:assembleRelease        # 需要 release keystore，见 app/build.gradle.kts
```

最低环境：JDK 17、Android SDK Platform 34、Android Build-Tools 34.x。

## 安装到手机

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.matrix.companion.debug/com.matrix.companion.MainActivity
```

更顺手的开发循环：`./scripts/dev-loop.sh`（重建 + 安装 + 拉日志一条龙）。

## 第一次使用

1. 打开 App，**点"开启无障碍服务"**，跳到系统设置，开启"Matrix Companion (无障碍)"。
2. 回到 App，**输入主控给你的配对码**（master 在 `Devices` 面板点"新增设备"会拿到 6 位数字），按"配对"。
3. 配对成功显示"已配对"，**点"启动主控"**。CompanionService + WatchdogService 拉起，HTTP server 监听 `0.0.0.0:8765`。

主控可通过 `adb reverse tcp:8765 tcp:8765` 直接打到手机（不需要 Tailscale 也能调），或经 Tailscale mesh 由 100.x 段 IP 访问。

## 8 个 HTTP 端点

| 路径 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | 不需鉴权，返回服务存活 + 无障碍是否绑定 |
| `/device/status` | GET | 设备状态、电量、前台 App、Tailscale 连接 |
| `/app/open` | POST | 打开指定包名 App |
| `/action/tap` | POST | 点击（坐标 / resource-id / content-desc / 文本 / xpath） |
| `/action/swipe` | POST | 滑动 |
| `/action/input` | POST | 向当前聚焦字段输入文本 |
| `/screen/screenshot` | GET | 截屏（API 30+） |
| `/xhs/publish` | POST | 走 XHS 发布流程（标题 / 正文 / 标签 / 图片） |
| `/xhs/interact` | POST | 点赞 / 评论 / 关注 / 收藏 / 分享 |
| `/xhs/collect_metrics` | POST | 个人页解析点赞 / 评论 / 收藏 |

所有非 `/health` 路径必须带 3 个 header：

```
X-Timestamp: <unix-seconds>      # 5 分钟内有效
X-Signature: base64(HMAC-SHA256(secret, "{timestamp}\n{request_id}\n{body_sha256_hex}"))
X-Request-Id: <uuid>             # 写操作必带，服务端 LRU 7 天去重
```

完整协议参考 `../docs/api/apk-http.openapi.yaml`。

## 调试 —— 自己签请求打这台手机

```bash
# 假设主控已经给你分发了 base64 编码的密钥
export SECRET_B64="$(adb shell run-as com.matrix.companion.debug cat /data/data/com.matrix.companion.debug/shared_prefs/matrix_companion_secrets.xml \
  | grep -oE 'hmac_secret=[^"]*\"[A-Za-z0-9+/=]+\"' | tail -1 | sed 's/.*"//; s/"//')"

# 用项目根目录的脚本一步签名
./scripts/sign-hmac.sh GET  http://127.0.0.1:8765/device/status
./scripts/sign-hmac.sh POST http://127.0.0.1:8765/action/input '{"text":"hello","request_id":"00000000-0000-0000-0000-000000000001"}'
```

## 小红书自动化

`xhs/XhsSelectors.kt` 收录了一组基于公开反编译的 `resource-id` 与 `content-desc` 常量，**小红书每次发版都可能改 selector**：

- 装到目标版本的小红书上跑一次，先 `adb logcat | grep MatrixCompanion` 看 `tap XhsSelectors.BTN_CREATE_NOTE → ...` 哪一步失败。
- 把 `XhsSelectors` 里 `resource-id` 改成目标版本对应的 ID 即可（不需要改 `XhsPublisher.kt`）。

## 关键文件

| 文件 | 干啥 |
|---|---|
| `service/HttpServer.kt` | Ktor CIO server，跑在 `0.0.0.0:8765`，HMAC + 幂等中间件 |
| `accessibility/AccessibilityDriver.kt` | 无障碍 API 统一入口（tap / swipe / input / screenshot） |
| `accessibility/CompanionAccessibilityService.kt` | 系统绑定的无障碍服务 |
| `auth/HmacVerifier.kt` | HMAC-SHA256 校验 + 5min 时间戳容忍 |
| `auth/IdempotencyCache.kt` | request_id LRU 去重（防重放） |
| `crypto/HmacSecretStore.kt` | EncryptedSharedPreferences + Keystore 主密钥 |
| `crypto/KeystoreManager.kt` | Android Keystore AES-256 key 生成 |
| `net/DeviceRegistrar.kt` | 配对流程（拿 HMAC 密钥） |
| `status/Heartbeat.kt` | 30s 一次心跳上报主控 |
| `xhs/XhsPublisher.kt` | 发布笔记流程 |
| `xhs/XhsInteractor.kt` | 点赞 / 评论 / 关注 / 收藏 |
| `xhs/XhsMetricsCollector.kt` | 个人页 DOM 解析 -> 互动数据 |
| `service/CompanionService.kt` | 前台服务，跑 HTTP server + 心跳 |
| `service/WatchdogService.kt` | 守护 CompanionService，被杀拉起 |
| `service/BootReceiver.kt` | BOOT_COMPLETED 后自动启动 |

## 已知限制 & 接下来要做

- **没有 VLM 读屏兜底**：`TapBySelector.fallback_vlm=true` 现在是 no-op。要接 Claude / GPT-4V 视觉读屏，等你拍板 API key 走哪条。
- **图片上传没接**：XHS 的 `编辑→从相册选图` 选 3 张需要走专门的 system picker flow，目前在 `XhsPublisher` 里只打印路径占位。
- **没有 APK 自身的端到端集成测试**：单元测试只覆盖 HMAC、幂等、路由 smoke；androidTest 必须实机跑。
- **`uiautomator dump` 也没集成**：定位难搞时手工备选。

## 调试日志等级

通过 `adb shell setprop log.tag.MatrixCompanion VERBOSE` 打开 V 级。`MainActivity` 还会把最近 200 行同步渲染到屏幕底部。
