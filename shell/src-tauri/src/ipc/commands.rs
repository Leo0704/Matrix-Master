//! Tauri 2.x IPC 命令（Tauri 中通过 `#[tauri::command]` 暴露给前端）。
//!
//! 设计原则：
//! - 每个命令都是 `async fn`，签名参数自动反序列化（来自前端 `invoke`）。
//! - 返回 `Result<T, AppError>`；`AppError` 实现 `serde::Serialize`，前端会拿到 `{code,message}`。
//! - 命令本身在 Tauri runtime 上跑；底层耗时操作（spawn 后端、调 keyring）走
//!   `tokio::spawn_blocking` 或真正的 tokio task，避免阻塞 UI。
//!
//! 不要在这里写业务逻辑——业务走 Python 后端的 REST API（见 `docs/api/master-rest.openapi.yaml`）。

use tauri::{AppHandle, State};

use crate::error::{AppError, AppResult};
use crate::hmac;
use crate::keyring_store::{self, KEYRING_SERVICE};
use crate::python_backend::{self, BackendHealth};
use crate::state::{AppInfo, AppState};

// ----- 应用信息 -----------------------------------------------------------

/// 返回版本号、uptime、db 状态、tailscale 状态等。
///
/// db_status / tailscale_status 是给前端展示用，真实数据由 Python 后端在
/// `/api/v1/health` 返回；这里为减少前端→Python 的握手，先尝试一次轻量探活。
#[tauri::command]
pub async fn get_app_info(state: State<'_, AppState>) -> AppResult<AppInfo> {
    let cfg = python_backend::BackendConfig::from_env();
    let h: BackendHealth = python_backend::probe_health(&cfg).await;

    let (db_status, tailscale_status, backend_version) = if h.reachable {
        if let Some(body) = h.body.as_ref() {
            (
                body.get("db")
                    .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string(),
                body.get("tailscale")
                    .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string(),
                body.get("version")
                    .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            )
        } else {
            ("unknown".to_string(), "unknown".to_string(), String::new())
        }
    } else {
        (
            "down".to_string(),
            "unknown".to_string(),
            String::new(),
        )
    };

    let _ = backend_version; // 当前 AppInfo 暂不暴露后端 version，留作扩展
    Ok(AppInfo {
        name: state.name.clone(),
        version: state.version.clone(),
        uptime_sec: state.uptime_seconds(),
        db_status,
        tailscale_status,
    })
}

// ----- Python 后端管理 ----------------------------------------------------

/// 重启 Python 后端（用户在 UI 上触发）。
#[tauri::command]
pub async fn restart_python_backend(state: State<'_, AppState>) -> AppResult<()> {
    python_backend::restart(state.inner()).await
}

/// 主动探活一次。前端用于「刷新状态」按钮。
#[tauri::command]
pub async fn probe_backend() -> AppResult<BackendHealth> {
    let cfg = python_backend::BackendConfig::from_env();
    Ok(python_backend::probe_health(&cfg).await)
}

// ----- HMAC 密钥管理（威胁模型 §6.3）-------------------------------------

const HMAC_KEY_ACCOUNT_PREFIX: &str = "hmac:";

fn account_for(device_id: &str) -> String {
    format!("{}{}", HMAC_KEY_ACCOUNT_PREFIX, device_id)
}

fn validate_device_id(device_id: &str) -> AppResult<()> {
    if device_id.is_empty() {
        return Err(AppError::InvalidInput("device_id is empty".into()));
    }
    if device_id.len() > 128 {
        return Err(AppError::InvalidInput("device_id too long".into()));
    }
    if !device_id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err(AppError::InvalidInput(
            "device_id contains illegal characters".into(),
        ));
    }
    Ok(())
}

/// 生成新的 HMAC 密钥（256 bit），写入 OS keyring。返回 base64 编码的密钥
/// （仅在调用瞬间返回一次，**前端必须立刻把它转交给配对的 APK**，然后丢弃）。
#[tauri::command]
pub async fn generate_hmac_key(device_id: String) -> AppResult<String> {
    validate_device_id(&device_id)?;

    let key = hmac::generate_key();
    let encoded = base64::Engine::encode(&base64::engine::general_purpose::STANDARD, key);

    // 阻塞的 keyring 调用丢到 blocking 池
    let account = account_for(&device_id);
    let encoded_clone = encoded.clone();
    tokio::task::spawn_blocking(move || {
        keyring_store::store_secret(KEYRING_SERVICE, &account, &encoded_clone)
    })
    .await
    .map_err(|e| AppError::Internal(format!("join error: {}", e)))??;

    log::info!("generated hmac key for device={}", device_id);
    Ok(encoded)
}

/// 从 keyring 取出 HMAC 密钥的 base64 字符串。无记录返回 `Ok(None)`。
///
/// 主要供主控后端在配对流程中取出明文下发到 APK——配对结束后建议 `revoke` 并重新生成。
#[tauri::command]
pub async fn get_hmac_key(device_id: String) -> AppResult<Option<String>> {
    validate_device_id(&device_id)?;
    let account = account_for(&device_id);
    tokio::task::spawn_blocking(move || keyring_store::load_secret(KEYRING_SERVICE, &account))
        .await
        .map_err(|e| AppError::Internal(format!("join error: {}", e)))?
}

/// 撤销（删除）一个设备的 HMAC 密钥。
#[tauri::command]
pub async fn revoke_hmac_key(device_id: String) -> AppResult<()> {
    validate_device_id(&device_id)?;
    let account = account_for(&device_id);
    tokio::task::spawn_blocking(move || keyring_store::delete_secret(KEYRING_SERVICE, &account))
        .await
        .map_err(|e| AppError::Internal(format!("join error: {}", e)))??;
    log::info!("revoked hmac key for device={}", device_id);
    Ok(())
}

/// 轮换：删除旧密钥 + 生成新密钥。返回新的 base64 编码。
#[tauri::command]
pub async fn rotate_hmac_key(device_id: String) -> AppResult<String> {
    revoke_hmac_key(device_id.clone()).await?;
    generate_hmac_key(device_id).await
}

// ----- 系统集成 -----------------------------------------------------------

/// 在系统默认浏览器中打开 URL。仅允许 http / https。
#[tauri::command]
pub async fn open_external_url(app: AppHandle, url: String) -> AppResult<()> {
    use tauri_plugin_opener::OpenerExt;

    let parsed = url::Url::parse(&url)
        .map_err(|e| AppError::InvalidInput(format!("invalid url: {}", e)))?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(AppError::InvalidInput(format!(
            "unsupported scheme {}; only http/https allowed",
            parsed.scheme()
        )));
    }

    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|e| AppError::Command(format!("opener: {}", e)))?;
    Ok(())
}

/// 显示系统通知。
#[tauri::command]
pub async fn show_notification(
    app: AppHandle,
    title: String,
    body: String,
) -> AppResult<()> {
    crate::notifications::show_notification(&app, &title, &body)
}
