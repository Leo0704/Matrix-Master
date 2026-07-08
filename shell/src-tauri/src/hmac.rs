//! HMAC-SHA256 工具，与 Python 后端（参见 [`/Users/lylyyds/Desktop/矩阵/docs/api/master-rest.openapi.yaml`]
//! 的「鉴权：Tauri 通过 Unix socket 或共享 secret 鉴权」）保持一致：
//!
//! ```text
//! canonical = "{timestamp}\n{request_id}\n{body_sha256_hex}"
//! signature = HEX(HMAC-SHA256(secret, canonical))
//! ```
//!
//! `body_sha256_hex` 是请求 body 的 SHA-256 十六进制哈希。

use std::time::{SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use rand::RngCore;
use sha2::{Digest, Sha256};

use crate::error::{AppError, AppResult};

/// 当前 Unix 时间戳（秒）。解析失败回退 0，Tauri 启动时 NTP 通常已同步。
fn now_ts() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// HMAC-SHA256 算法标识。
pub type HmacSha256 = Hmac<Sha256>;

/// 生成 32 字节随机密钥。
pub fn generate_key() -> [u8; 32] {
    let mut key = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut key);
    key
}

/// 计算 body 的 SHA-256，输出小写 hex。
pub fn sha256_hex(body: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(body);
    hex::encode(hasher.finalize())
}

/// 计算 HMAC 签名的 canonical 串。
///
/// 格式：`{timestamp}\n{request_id}\n{body_sha256_hex}`
pub fn canonical_string(timestamp: i64, request_id: &str, body: &[u8]) -> String {
    format!("{}\n{}\n{}", timestamp, request_id, sha256_hex(body))
}

/// 计算 HMAC 签名（小写 hex）。
pub fn compute_signature(secret: &[u8], timestamp: i64, request_id: &str, body: &[u8]) -> String {
    let canonical = canonical_string(timestamp, request_id, body);
    let mut mac = HmacSha256::new_from_slice(secret)
        .expect("HMAC accepts any key length; keyring secret is 32 bytes");
    mac.update(canonical.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// 校验 HMAC 签名；同时检查时间戳是否在 `ttl_seconds` 窗口内。
///
/// 用 `subtle::ConstantTimeEq` 防止时序攻击，这里用 `hmac` crate 提供的
/// `verify_slice`，已采用常数时间比较。
pub fn verify_signature(
    secret: &[u8],
    timestamp: i64,
    request_id: &str,
    body: &[u8],
    sig: &str,
    ttl_seconds: i64,
) -> AppResult<bool> {
    let now = now_ts();
    let skew = (now - timestamp).abs();
    if skew > ttl_seconds {
        return Ok(false);
    }

    let mut mac = HmacSha256::new_from_slice(secret).map_err(|e| AppError::Hmac(e.to_string()))?;
    mac.update(canonical_string(timestamp, request_id, body).as_bytes());

    let sig_bytes = hex::decode(sig.trim()).map_err(|e| AppError::Hmac(e.to_string()))?;
    Ok(mac.verify_slice(&sig_bytes).is_ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signature_roundtrip() {
        let key = generate_key();
        let body = br#"{"hello":"world"}"#;
        let ts = 1_700_000_000;
        let req_id = "req-123";

        let sig = compute_signature(&key, ts, req_id, body);
        assert!(verify_signature(&key, ts, req_id, body, &sig, 60).unwrap());
    }

    #[test]
    fn signature_rejects_tampered_body() {
        let key = generate_key();
        let body = br#"{"hello":"world"}"#;
        let ts = 1_700_000_000;
        let req_id = "req-123";

        let sig = compute_signature(&key, ts, req_id, body);
        let tampered = br#"{"hello":"WORLD"}"#;
        assert!(!verify_signature(&key, ts, req_id, tampered, &sig, 60).unwrap());
    }

    #[test]
    fn signature_rejects_expired_timestamp() {
        let key = generate_key();
        let body = b"{}";
        let past_ts = 1_500_000_000;
        let req_id = "req-123";

        let sig = compute_signature(&key, past_ts, req_id, body);
        assert!(!verify_signature(&key, past_ts, req_id, body, &sig, 60).unwrap());
    }
}
