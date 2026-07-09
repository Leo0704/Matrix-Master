//! Python 后端管理。
//!
//! - `spawn_backend()` 用 `tokio::process::Command` 启动 `python -m matrix.api.app`
//!   （不阻塞 Tauri 主线程）。
//! - 等待 health 端点返回 200（最多等 30 秒）。
//! - 探活任务每 10 秒跑一次（独立的 tokio task）。
//! - 关闭时通过 cancel handle 终止探活 + kill 子进程。
//!
//! 配置来源（环境变量优先，未设则用默认）：
//! - `MATRIX_PYTHON_BIN`：`python`（默认）
//! - `MATRIX_API_MODULE`：`matrix.api.app`（默认）
//! - `MATRIX_API_PORT`：`8666`（默认）

use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tokio::process::{Child, Command};
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

use crate::error::{AppError, AppResult};
use crate::state::AppState;
use tracing::Instrument;

const DEFAULT_PYTHON_BIN: &str = "python";
const DEFAULT_API_MODULE: &str = "matrix.api.app";
const DEFAULT_API_PORT: u16 = 8666;

const HEALTH_PATH: &str = "/api/v1/health";
const READY_TIMEOUT_SECS: u64 = 30;
const HEALTH_PROBE_INTERVAL_SECS: u64 = 10;

/// 进程级单调计数器 + 高精度时间戳，构成 32 hex 字符 trace_id。
static TRACE_COUNTER: AtomicU64 = AtomicU64::new(0);

/// 生成 32 hex 字符 trace_id（与 Python middleware `_normalize_trace_id` 兼容）。
pub fn generate_trace_id_hex() -> String {
    let n = TRACE_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    format!("{:016x}{:016x}", nanos, n)
}

/// 取消 + 清理句柄，关闭时由 Tauri RunEvent::ExitRequested / ExitRequested handle 触发。
pub struct BackendHandle {
    /// 取消探活任务。
    pub cancel: oneshot::Sender<()>,
    /// 探活 task 的 JoinHandle（关时丢弃即可）。
    pub task: JoinHandle<()>,
}

impl BackendHandle {
    pub fn cancel(self) {
        let _ = self.cancel.send(());
    }
}

/// Backend 健康检查返回值，与 [`crate::ipc::commands::proxy_health`] 共用。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BackendHealth {
    pub reachable: bool,
    pub status_code: Option<u16>,
    pub body: Option<serde_json::Value>,
    pub elapsed_ms: u64,
    pub error: Option<String>,
}

/// 读取配置（环境变量 / 默认值）。
#[derive(Debug, Clone)]
pub struct BackendConfig {
    pub python_bin: String,
    pub module: String,
    pub port: u16,
    pub base_url: String,
}

impl BackendConfig {
    pub fn from_env() -> Self {
        let python_bin = std::env::var("MATRIX_PYTHON_BIN")
            .unwrap_or_else(|_| DEFAULT_PYTHON_BIN.to_string());
        let module = std::env::var("MATRIX_API_MODULE")
            .unwrap_or_else(|_| DEFAULT_API_MODULE.to_string());
        let port: u16 = std::env::var("MATRIX_API_PORT")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(DEFAULT_API_PORT);
        let base_url = format!("http://localhost:{}", port);
        Self {
            python_bin,
            module,
            port,
            base_url,
        }
    }
}

/// 装配 spawn 命令（提取出来便于测试）。
pub fn build_command(cfg: &BackendConfig) -> Command {
    let mut cmd = Command::new(&cfg.python_bin);
    cmd.arg("-m")
        .arg(&cfg.module)
        .arg("--port")
        .arg(cfg.port.to_string())
        .env("MATRIX_API_PORT", cfg.port.to_string())
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    cmd
}

/// 拉起 Python 后端；返回子进程句柄。**不**等待 ready，由 `wait_ready` 单独处理。
#[tracing::instrument(skip_all, fields(base_url = %cfg.base_url, python_bin = %cfg.python_bin))]
pub async fn spawn_backend(cfg: &BackendConfig) -> AppResult<Child> {
    let cmd = build_command(cfg);
    cmd.kill_on_drop(true)
        .spawn()
        .map_err(|e| AppError::PythonBackend(format!("spawn failed: {}", e)))
}

/// 等待 `/api/v1/health` 连续返回 200，最多等 [`READY_TIMEOUT_SECS`] 秒。
#[tracing::instrument(skip_all, fields(base_url = %cfg.base_url, timeout_secs = READY_TIMEOUT_SECS, trace_id))]
pub async fn wait_ready(cfg: &BackendConfig) -> AppResult<()> {
    let url = format!("{}{}", cfg.base_url, HEALTH_PATH);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()?;
    let deadline = std::time::Instant::now() + Duration::from_secs(READY_TIMEOUT_SECS);
    let trace_id = generate_trace_id_hex();
    tracing::Span::current().record("trace_id", tracing::field::display(&trace_id));

    while std::time::Instant::now() < deadline {
        if let Ok(resp) = client
            .get(&url)
            .header("X-Request-ID", &trace_id)
            .send()
            .await
        {
            if resp.status() == reqwest::StatusCode::OK {
                return Ok(());
            }
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    Err(AppError::PythonBackend(format!(
        "backend not ready after {}s (url={})",
        READY_TIMEOUT_SECS, url
    )))
}

/// 主动探活一次。
#[tracing::instrument(skip_all, fields(base_url = %cfg.base_url, trace_id))]
pub async fn probe_health(cfg: &BackendConfig) -> BackendHealth {
    let url = format!("{}{}", cfg.base_url, HEALTH_PATH);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
        .expect("reqwest client");
    let start = std::time::Instant::now();
    let trace_id = generate_trace_id_hex();
    tracing::Span::current().record("trace_id", tracing::field::display(&trace_id));
    match client
        .get(&url)
        .header("X-Request-ID", &trace_id)
        .send()
        .await
    {
        Ok(resp) => {
            let status = resp.status();
            let body: Option<serde_json::Value> = resp.json().await.ok();
            BackendHealth {
                reachable: status.is_success(),
                status_code: Some(status.as_u16()),
                body,
                elapsed_ms: start.elapsed().as_millis() as u64,
                error: None,
            }
        }
        Err(e) => BackendHealth {
            reachable: false,
            status_code: None,
            body: None,
            elapsed_ms: start.elapsed().as_millis() as u64,
            error: Some(e.to_string()),
        },
    }
}

/// 拉起 Python 后端 → 等待 ready → 启动探活任务。返回子进程 + health 句柄。
#[tracing::instrument(skip_all, fields(base_url = %cfg.base_url))]
pub async fn start_with_health_loop(app: &AppHandle) -> AppResult<(Child, BackendHandle)> {
    let cfg = BackendConfig::from_env();

    // 如果配置 / 端口已被占用，先 try 检测；没问题再 spawn
    if probe_health(&cfg).await.reachable {
        tracing::info!(base_url = %cfg.base_url, "python.backend.already_reachable");
    }

    let child = spawn_backend(&cfg).await?;
    wait_ready(&cfg).await?;

    tracing::info!(base_url = %cfg.base_url, "python.backend.ready");

    // 启动探活 loop（独立 task，oneshot 取消）。
    // 同上：spawned task 不继承父 span，手动 .instrument。
    let (cancel_tx, mut cancel_rx) = oneshot::channel();
    let cfg_for_loop = cfg.clone();
    let app_for_loop = app.clone();
    let task = tauri::async_runtime::spawn(
        async move {
            loop {
                tokio::select! {
                    _ = &mut cancel_rx => {
                        tracing::info!("backend.health_loop.cancelled");
                        break;
                    }
                    _ = tokio::time::sleep(Duration::from_secs(HEALTH_PROBE_INTERVAL_SECS)) => {
                        let h = probe_health(&cfg_for_loop).await;
                        if !h.reachable {
                            tracing::warn!(error = ?h.error, "backend.health_probe.failed");
                        }
                        // 广播事件给前端
                        let _ = app_for_loop.emit("backend://health", &h);
                    }
                }
            }
        }
        .instrument(tracing::info_span!("backend.health_loop")),
    );

    Ok((child, BackendHandle { cancel: cancel_tx, task }))
}

/// 优雅地 kill 子进程（先 SIGTERM，等 2s 再 SIGKILL）。
#[tracing::instrument(skip_all)]
pub async fn kill_child(child: &mut Child) {
    if let Some(pid) = child.id() {
        tracing::info!(pid = pid, "killing python backend");
    }
    let _ = child.start_kill();
    let deadline = std::time::Instant::now() + Duration::from_secs(2);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill().await;
                    return;
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
            Err(_) => return,
        }
    }
}

/// 关闭并清理 backend：取消探活 + kill 子进程。
#[tracing::instrument(skip_all)]
pub async fn shutdown(state: &AppState) {
    if let Some(handle) = state.backend_handle.lock().await.take() {
        handle.cancel();
    }
    let mut slot = state.python_backend.lock().await;
    if let Some(mut child) = slot.child.take() {
        kill_child(&mut child).await;
    }
}

/// 重启：先 kill 旧进程，再 spawn 新进程 + 等待 ready。
///
/// 适用于 IPC `restart_python_backend` 命令——用户主动触发。
#[tracing::instrument(skip_all)]
pub async fn restart(state: &AppState) -> AppResult<()> {
    shutdown(state).await;
    let cfg = BackendConfig::from_env();
    let mut child = spawn_backend(&cfg).await?;
    wait_ready(&cfg).await?;
    state.python_backend.lock().await.child = Some(child);
    Ok(())
}
