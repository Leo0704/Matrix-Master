//! 系统通知封装。
//!
//! 走 `tauri-plugin-notification` 提供的跨平台 API：
//! - macOS / iOS：NSUserNotification
//! - Windows     ：Toast notifications
//! - Linux       ：libnotify / DBus

use tauri::AppHandle;
use tauri_plugin_notification::NotificationExt;

use crate::error::AppResult;

/// 通知唯一标题前缀，用于合并同一告警。
pub const NOTIFICATION_TAG: &str = "matrix-master";

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

/// 杀掉通知权限弹窗控制流（按需调用）。返回当前权限状态。
pub fn ensure_permission(app: &AppHandle) -> AppResult<bool> {
    app.notification()
        .request_permission()
        .map_err(|e| crate::error::AppError::Command(e.to_string()))
}

/// 兜底确认通知插件已正确初始化（在 setup() 中调用）。
pub fn is_initialized(app: &AppHandle) -> bool {
    app.try_state::<tauri_plugin_notification::Notification>()
        .is_some()
}
