//! 统一错误类型，实现 `serde::Serialize` 以便 IPC 直接返回到前端。

use serde::Serialize;
use std::fmt;

#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("Python backend error: {0}")]
    PythonBackend(String),

    #[error("Keyring error: {0}")]
    Keyring(String),

    #[error("HMAC error: {0}")]
    Hmac(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("HTTP error: {0}")]
    Http(String),

    #[error("Serialization error: {0}")]
    Serde(#[from] serde_json::Error),

    #[error("Command execution error: {0}")]
    Command(String),

    #[error("Configuration error: {0}")]
    Config(String),

    #[error("Invalid input: {0}")]
    InvalidInput(String),

    #[error("IPC error: {0}")]
    Ipc(String),

    #[error("Internal error: {0}")]
    Internal(String),
}

impl From<reqwest::Error> for AppError {
    fn from(err: reqwest::Error) -> Self {
        AppError::Http(err.to_string())
    }
}

impl From<tauri::Error> for AppError {
    fn from(err: tauri::Error) -> Self {
        AppError::Ipc(err.to_string())
    }
}

impl From<tauri_plugin_notification::Error> for AppError {
    fn from(err: tauri_plugin_notification::Error) -> Self {
        AppError::Command(err.to_string())
    }
}

impl From<keyring::Error> for AppError {
    fn from(err: keyring::Error) -> Self {
        AppError::Keyring(err.to_string())
    }
}

pub type AppResult<T> = std::result::Result<T, AppError>;

/// IPC 可见的错误载荷。所有变体序列化为 `{ code, message }`。
#[derive(Debug, Serialize)]
pub struct AppErrorPayload {
    pub code: &'static str,
    pub message: String,
}

impl Serialize for AppError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        let code = match self {
            AppError::PythonBackend(_) => "PYTHON_BACKEND",
            AppError::Keyring(_) => "KEYRING",
            AppError::Hmac(_) => "HMAC",
            AppError::Io(_) => "IO",
            AppError::Http(_) => "HTTP",
            AppError::Serde(_) => "SERDE",
            AppError::Command(_) => "COMMAND",
            AppError::Config(_) => "CONFIG",
            AppError::InvalidInput(_) => "INVALID_INPUT",
            AppError::Ipc(_) => "IPC",
            AppError::Internal(_) => "INTERNAL",
        };
        AppErrorPayload {
            code,
            message: self.to_string(),
        }
        .serialize(serializer)
    }
}

/// 提供一个简短的格式化形式，便于日志输出。
impl fmt::Display for AppErrorPayload {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.code, self.message)
    }
}
