//! 系统通知封装。
//!
//! 走 `tauri-plugin-notification` 提供的跨平台 API：
//! - macOS / iOS：NSUserNotification
//! - Windows     ：Toast notifications
//! - Linux       ：libnotify / DBus

use tauri::AppHandle;
use tauri_plugin_notification::NotificationExt;

use crate::error::AppResult;

/// 发送一条系统通知。
///
/// 调用方负责 title/body 的脱敏（不要塞 secret / token）。
pub fn show_notification(app: &AppHandle, title: &str, body: &str) -> AppResult<()> {
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|e| crate::error::AppError::Command(e.to_string()))?;
    Ok(())
}
