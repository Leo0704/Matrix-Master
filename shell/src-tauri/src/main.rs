// 主控 Tauri 桌面外壳入口。
// 所有实际逻辑都在 `lib.rs::run()` 中；这里只是 thin wrapper，
// 便于 Tauri 2 mobile entry point 复用同一份代码。
//
// Windows 上 `[#![windows_subsystem = "windows"]]` 用来去除 console window;
// macOS / Linux 不需要。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    matrix_master_shell::run()
}
