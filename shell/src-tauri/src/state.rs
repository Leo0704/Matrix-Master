//! 全局应用状态。
//!
//! Tauri 2 中 `State<T>` 通过 `manage()` 注入；这里集中持有与 Python 子进程、
//! 后端健康探活、UI 显示相关的可变状态。

use std::sync::Arc;
use std::time::Instant;

use serde::Serialize;
use tokio::process::Child;
use tokio::sync::Mutex;

use crate::python_backend::BackendHandle;

/// 当前持有 / 不持有 Python 后端子进程。
#[derive(Default)]
pub struct PythonBackendSlot {
    pub child: Option<Child>,
}

/// 全局应用状态。
pub struct AppState {
    /// 当前 Python 后端子进程（如果已经 spawn）。
    pub python_backend: Arc<Mutex<PythonBackendSlot>>,

    /// 后端健康句柄（用于取消探活任务）。
    pub backend_handle: Arc<Mutex<Option<BackendHandle>>>,

    /// 应用启动时间（用于 `uptime_sec`）。
    pub start_time: Instant,

    /// 应用版本。
    pub version: String,

    /// 应用名。
    pub name: String,
}

impl AppState {
    pub fn new(version: String) -> Self {
        Self {
            python_backend: Arc::new(Mutex::new(PythonBackendSlot::default())),
            backend_handle: Arc::new(Mutex::new(None)),
            start_time: Instant::now(),
            version,
            name: "Matrix Master".to_string(),
        }
    }

    pub fn uptime_seconds(&self) -> u64 {
        self.start_time.elapsed().as_secs()
    }
}

/// `get_app_info` 命令返回的载荷。
#[derive(Debug, Serialize)]
pub struct AppInfo {
    pub name: String,
    pub version: String,
    pub uptime_sec: u64,
    pub db_status: String,
    pub tailscale_status: String,
}
