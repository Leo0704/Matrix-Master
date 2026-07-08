//! OS Keyring 封装。HMAC 共享密钥一律落系统钥匙串，**不写明文到配置文件**。
//!
//! - macOS   → Keychain
//! - Windows → Credential Manager
//! - Linux   → Secret Service (gnome-keyring / kwallet)
//!
//! 服务名固定为 `"com.matrix.master"`，account 是上层传入的键（通常为 `device_id`）。

use keyring::Entry;

use crate::error::{AppError, AppResult};

/// 全局 service identifier，威胁模型 §6.2 / §6.3 要求的服务名。
pub const KEYRING_SERVICE: &str = "com.matrix.master";

/// 写入 / 覆盖一个 secret。
pub fn store_secret(service: &str, account: &str, secret: &str) -> AppResult<()> {
    let entry = Entry::new(service, account)?;
    entry.set_password(secret)?;
    Ok(())
}

/// 读取一个 secret。无记录时返回 `Ok(None)`（不报错）。
pub fn load_secret(service: &str, account: &str) -> AppResult<Option<String>> {
    let entry = Entry::new(service, account)?;
    match entry.get_password() {
        Ok(secret) => Ok(Some(secret)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(AppError::from(e)),
    }
}

/// 删除一个 secret。无记录时也返回成功（幂等）。
pub fn delete_secret(service: &str, account: &str) -> AppResult<()> {
    let entry = Entry::new(service, account)?;
    match entry.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(AppError::from(e)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn store_load_delete_roundtrip() {
        let account = "test-device-roundtrip";
        store_secret(KEYRING_SERVICE, account, "deadbeef").unwrap();
        let got = load_secret(KEYRING_SERVICE, account).unwrap();
        assert_eq!(got.as_deref(), Some("deadbeef"));

        delete_secret(KEYRING_SERVICE, account).unwrap();
        let got = load_secret(KEYRING_SERVICE, account).unwrap();
        assert_eq!(got, None);
    }

    #[test]
    fn delete_is_idempotent() {
        let account = "test-device-idem";
        delete_secret(KEYRING_SERVICE, account).unwrap();
        delete_secret(KEYRING_SERVICE, account).unwrap();
    }
}
