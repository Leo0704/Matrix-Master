# 图标占位

Tauri 2.x 在 `tauri.conf.json -> bundle.icon` 引用以下文件：

- `32x32.png`
- `128x128.png`
- `128x128@2x.png`
- `icon.icns`（macOS）
- `icon.ico`（Windows）

仓库**只放 README**；正式打包前请用 `cargo tauri icon path/to/source.png` 生成完整套图。
Tauri CLI 会同时输出全部尺寸 + 各平台专属格式。

托盘图标 `tray.png` 在 `tauri.conf.json -> app.trayIcon.iconPath` 引用；
macOS 推荐用 `trayTemplate.png`（单色 + alpha），并把 `iconAsTemplate: true`。
