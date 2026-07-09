//! Matrix Master Tauri Shell 入口。
//!
//! - 启动日志；
//! - 注册通知 / shell / opener 插件；
//! - 管理 `AppState`（含 Python backend 句柄）；
//! - 注册系统托盘；
//! - setup 阶段异步拉起 Python 后端，等待 health 200；
//! - 注册 IPC 命令；
//! - 优雅关闭（清理 Python 子进程）。
//!
//! 命令清单与权限声明见 `capabilities/default.json`。

pub mod error;
pub mod hmac;
pub mod ipc;
pub mod keyring_store;
pub mod notifications;
pub mod python_backend;
pub mod state;
pub mod system_tray;

use tauri::Manager;
use tracing::Instrument;
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

use crate::state::AppState;

/// Tauri 2.x 应用启动入口。被 `main.rs` 调用（同时也是 mobile entry point）。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
#[tracing::instrument]
pub fn run() {
    // 1. 日志初始化（设不到 RUST_LOG 就走默认级别；JSON 输出便于聚合）。
    let _ = tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with(fmt::layer().json().with_current_span(true).with_span_list(false))
        .try_init();

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .manage(AppState::new(env!("CARGO_PKG_VERSION").to_string()))
        .invoke_handler(tauri::generate_handler![
            ipc::commands::get_app_info,
            ipc::commands::probe_backend,
            ipc::commands::restart_python_backend,
            ipc::commands::generate_hmac_key,
            ipc::commands::get_hmac_key,
            ipc::commands::revoke_hmac_key,
            ipc::commands::rotate_hmac_key,
            ipc::commands::open_external_url,
            ipc::commands::show_notification,
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            // 注册系统托盘
            if let Err(e) = system_tray::setup(&handle) {
                tracing::warn!(error = %e, "tray setup failed");
            }

            // 异步拉起 Python 后端（不阻塞 Tauri setup）。
            // spawned task 不会自动继承 run() 的 span —— 手动 .instrument。
            tauri::async_runtime::spawn(
                async move {
                    match python_backend::start_with_health_loop(&handle).await {
                        Ok((child, h)) => {
                            let state = handle.state::<AppState>();
                            state.python_backend.lock().await.child = Some(child);
                            *state.backend_handle.lock().await = Some(h);
                            tracing::info!("python.backend.health_loop.started");
                        }
                        Err(e) => {
                            tracing::error!(error = %e, "python.backend.startup_failed");
                            let _ = notifications::show_notification(
                                &handle,
                                "Matrix Master — 后端启动失败",
                                &format!("请检查 Python 环境（{}）。可在监控控制台手动重启。", e),
                            );
                        }
                    }
                }
                .instrument(tracing::info_span!("backend.startup")),
            );

            Ok(())
        })
        .on_window_event(|window, event| {
            // 关闭主窗口时，阻止默认关闭（macOS 习惯），改为隐藏窗口；
            // 用户只能通过托盘菜单的「退出」真正退出 app。
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    let _ = window.hide();
                    api.prevent_close();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build tauri app")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested { .. } = &event {
                // 同步触发：tokio runtime 仍在，但 Tauri 在退出事件里
                // 提供了 hook —— 这里只需 spin 一个 task 去 kill 子进程。
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    let state = handle.state::<AppState>();
                    python_backend::shutdown(state.inner()).await;
                });
            }
        });
}
