//! 把所有 `#[tauri::command]` 关联到 Tauri builder。
//!
//! `tauri::generate_handler!` 必须直接传给 `Builder::invoke_handler()`，
//! 所以本模块只暴露「命令列表」的常量名 + 把 macro 调用内联进 `lib.rs`。

/// 在 lib.rs 里直接调用：
/// ```ignore
/// tauri::generate_handler![
///     commands::get_app_info,
///     commands::restart_python_backend,
///     ...,
/// ]
/// ```
///
/// 不在本模块再次包装——`generate_handler!` 的输出是带生命周期 / 内部状态的
/// 闭包类型，无法装箱后跨位置传递。

#[allow(dead_code)]
pub const HANDLER_NAMES: &[&str] = &[
    "get_app_info",
    "probe_backend",
    "restart_python_backend",
    "generate_hmac_key",
    "get_hmac_key",
    "revoke_hmac_key",
    "rotate_hmac_key",
    "open_external_url",
    "show_notification",
];
