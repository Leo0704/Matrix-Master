//! HMAC 密钥生成。签名/校验在 APK 侧（`HmacVerifier.kt`）和 Python 后端
//! （`backend/matrix/device/hmac.py`）各自实现，本模块不参与运行时路径。

use rand::RngCore;

/// 生成 32 字节随机密钥。
pub fn generate_key() -> [u8; 32] {
    let mut key = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut key);
    key
}
