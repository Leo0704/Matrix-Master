# Matrix Master — Tauri Shell（Rust）

主控桌面应用的外壳部分（Tauri 2.x + Rust）。负责：

- 拉起并管理 Python 后端子进程（`python -m matrix.api.app`，端口 8666）
- OS Keyring 加密保存每台 APK 的 HMAC 共享密钥
- 系统托盘（macOS / Windows）
- 系统通知（设备告警 / 后端宕机）
- 暴露一组 IPC 命令给前端（`@tauri-apps/api`）

业务逻辑（LangGraph Agent、知识库、任务调度、设备-账号管理）都在 `../../backend/` Python
服务里跑，前端通过 HTTP 调用 `localhost:8666/api/v1/*`。

> 前端代码请看 `../README.md`（agent 10 拥有）。本 README 只描述 Rust crate。

## 目录速览

```
src-tauri/
├── src/
│   ├── main.rs            # 入口（thin wrapper）
│   ├── lib.rs             # run() —— Tauri builder + setup
│   ├── state.rs           # AppState（python_backend / backend_handle / version）
│   ├── python_backend.rs  # 拉起 + 探活 + 关闭 + 重启
│   ├── hmac.rs            # HMAC-SHA256 工具（keyring 存 / 计算 / 校验）
│   ├── keyring_store.rs   # OS keyring 封装（com.matrix.master）
│   ├── notifications.rs   # 跨平台系统通知
│   ├── system_tray.rs     # macOS/Windows 托盘
│   ├── ipc/
│   │   ├── commands.rs    # #[tauri::command] 列表
│   │   ├── handlers.rs    # generate_handler! 注入
│   │   └── mod.rs
│   └── error.rs           # AppError（实现 Serialize，便于 IPC 返回）
│
├── capabilities/
│   └── default.json       # 权限声明（IPC command → capability）
├── tauri.conf.json        # Tauri 2.x 配置
├── Cargo.toml             # 依赖：tauri 2 + keyring + reqwest + tokio + ...
├── build.rs
└── icons/                 # 占位（见 icons/README.md），正式打包需补图
```

## 前置依赖

- Rust stable（`>= 1.70`）。[rustup](https://rustup.rs/)。
- Tauri CLI：`cargo install tauri-cli --version "^2.0"`，或 `npm i -D @tauri-apps/cli && npx tauri ...`。
- 平台依赖：

  | 平台 | 依赖 |
  | --- | --- |
  | macOS | Xcode Command Line Tools |
  | Windows | Microsoft Visual C++ Build Tools + WebView2 |
  | Linux | `libwebkit2gtk-4.1-dev` / `libssl-dev` / `libayatana-appindicator3-dev` |

## 开发模式

```bash
# 准备 Python 后端（确保 `python -m matrix.api.app` 能跑通）
cd ../../backend && uv venv && uv pip install -e . && cd -

cd ../                  # shell/
npm install             # 装前端依赖（含 @tauri-apps/cli）
npm run tauri dev       # 启动 Tauri + 前端 + 自动 spawn Python 后端
```

启动时序：

1. `lib.rs::run()` 初始化日志 → 创建 Builder → 注册插件 → `manage(AppState)`。
2. `setup` 阶段异步调 `python_backend::start_with_health_loop()`：
   - 用 `tokio::process::Command` 跑 `python -m matrix.api.app --port 8666`
   - 每 500ms 探一次 `http://localhost:8666/api/v1/health`，最多等 30s
   - 启动 10s 一次的探活 task，结果通过 `backend://health` 事件广播给前端
3. 关闭主窗口不真正退出（macOS 风格），必须从托盘菜单「退出」才走 ExitRequested，
   此时 `lib.rs` 里的 `RunEvent::ExitRequested` hook 会 spawn task 调
   `python_backend::shutdown` 去 kill 子进程。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MATRIX_PYTHON_BIN` | `python` | Python 解释器 |
| `MATRIX_API_MODULE` | `matrix.api.app` | Python 入口模块 |
| `MATRIX_API_PORT` | `8666` | Python 后端监听端口（也写进 `--port`） |
| `RUST_LOG` | `info` | 日志级别（设 `debug` 看 request_id 链路） |

## 构建发布版

```bash
cd ../
npm run tauri build
```

产物落在 `src-tauri/target/release/bundle/`：

- macOS: `.dmg` / `.app`
- Windows: `.msi` / `.exe`
- Linux: `.deb` / `.AppImage`

`tauri.conf.json -> bundle.targets` 当前是 `["dmg", "msi", "deb", "appimage"]`。

## IPC 命令一览（前端 → Rust）

```ts
await invoke('get_app_info')                          // AppInfo
await invoke('probe_backend')                         // BackendHealth
await invoke('restart_python_backend')                // void
await invoke('generate_hmac_key', { deviceId })       // base64 string
await invoke('get_hmac_key',      { deviceId })       // string | null
await invoke('revoke_hmac_key',    { deviceId })      // void
await invoke('rotate_hmac_key',    { deviceId })      // base64 string
await invoke('open_external_url',  { url })           // void
await invoke('show_notification', { title, body })    // void
```

错误返回统一形如 `{ "code": "...", "message": "..." }`（见 `src/error.rs`）。
权限声明在 `capabilities/default.json`：每个 `shell:allow-open` / `notification:allow-notify` 等
对应一个 IPC 命令调用。

## 后端 → 前端事件

```ts
import { listen } from '@tauri-apps/api/event';
await listen<BackendHealth>('backend://health', e => { ... });
```

## HMAC 签名约定

跟 Python 后端一致：

```
canonical = "{timestamp}\n{request_id}\n{body_sha256_hex}"
signature = HEX(HMAC-SHA256(secret, canonical))
```

主控生成密钥（256 bit 随机）→ 写入 OS keyring（`com.matrix.master / hmac:<device_id>`）
→ 仅在配对流程下发到 APK 一次，APK 用 Android Keystore 加密保存。详见
`docs/architecture/threat-model.md §6.3`。

## 安全注意

- 前端代码只能通过声明过的 IPC 命令触发 shell 行为，不可执行任意命令。
- CSP（见 `tauri.conf.json -> app.security.csp`）锁死 `default-src 'self'`，只放行
  `http://localhost:8666` 给前端调本地后端。
- HMAC 密钥**绝不**写入配置文件或数据库——只走 OS keyring。

## 已知约束

- Linux tray-icon 需要 `libayatana-appindicator3-dev`；缺这个包时菜单不显示，
  但应用本身能正常用。
- 图标是占位 README，真正打包前必须用 `cargo tauri icon path/to/source.png`
  生成 `icons/32x32.png`、`128x128.png`、`icon.icns`、`icon.ico` 等。
