//! 跨平台系统托盘。
//!
//! - macOS：菜单栏图标；点击图标显示/隐藏主窗口；右键菜单含「显示窗口 / 退出」。
//! - Windows：系统托盘图标，同样的交互逻辑。
//!
//! Linux 上 Tauri 2 的 tray-icon 实验性支持（feature `tray-icon`），本文件假定
//! 该特性已开启；如果你的 target 暂不支持，去 Cargo.toml 移除 feature。

use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, Runtime,
};

const MENU_SHOW: &str = "show-window";
const MENU_QUIT: &str = "quit-app";
const TRAY_ID: &str = "main-tray";

/// 创建托盘 + 菜单，绑定到 `app` 上。
pub fn setup<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let show_item = MenuItem::with_id(app, MENU_SHOW, "显示窗口", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, MENU_QUIT, "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show_item, &quit_item])?;

    TrayIconBuilder::with_id(TRAY_ID)
        .menu(&menu)
        .tooltip("Matrix Master")
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            MENU_SHOW => {
                if let Some(win) = app.get_webview_window("main") {
                    let _ = win.show();
                    let _ = win.set_focus();
                    let _ = win.unminimize();
                }
            }
            MENU_QUIT => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            // macOS / Windows 上单击托盘图标 → 切回主窗口
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if let Some(win) = app.get_webview_window("main") {
                    let _ = win.show();
                    let _ = win.set_focus();
                    let _ = win.unminimize();
                }
            }
        })
        .build(app)?;

    Ok(())
}
