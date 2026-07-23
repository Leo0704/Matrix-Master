"""设备 HMAC secret 的信封加密（Fernet）。

背景：验签必须使用原始 secret（HMAC 机制固有），历史上以 base64 明文存
``app_config`` 表（``hmac_secret:{key_id}``），数据库泄露即全部设备沦陷。
现改为 Fernet 加密存储：主密钥不进数据库（env 或本地文件），DB 泄露不再
直接暴露设备密钥。

主密钥来源（优先级从高到低）：
1. ``MATRIX_SECRET_BOX_KEY`` 环境变量（``Fernet.generate_key()`` 产物）
2. 持久化文件（默认 ``/app/backend/.secret_box_key``，可用
   ``MATRIX_SECRET_BOX_KEY_PATH`` 覆盖）；首次启动自动生成并 chmod 600

注意：主密钥文件务必纳入备份、且不要提交 git；丢失 = 已加密 secret 不可
恢复，对应设备走一遍重新配对即可自愈（APK 侧无感）。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_DEFAULT_KEY_PATH = "/app/backend/.secret_box_key"


def _key_path() -> Path:
    return Path(os.environ.get("MATRIX_SECRET_BOX_KEY_PATH", _DEFAULT_KEY_PATH))


def _validate_key(key: bytes) -> None:
    """key 格式非法时给出可操作的报错（而不是在深处炸 InvalidToken）。"""
    try:
        Fernet(key)
    except (ValueError, InvalidToken) as e:
        raise RuntimeError(
            "MATRIX_SECRET_BOX_KEY / .secret_box_key 格式非法：必须是 "
            "Fernet.generate_key() 生成的 44 字符 urlsafe-b64 串。删掉非法值后 "
            "重启可自动生成新 key（已加密的旧 secret 无法恢复，设备需重新配对）。"
        ) from e


def get_master_key() -> bytes:
    """获取 Fernet 主密钥：env → 持久化文件 → 自动生成并落盘（chmod 600）。

    与 pair_codes 同样的"文件是唯一可信源"风格：每次调用现读，不做进程内缓存，
    换 key 只需替换文件或 env，无需清缓存。
    """
    env_key = os.environ.get("MATRIX_SECRET_BOX_KEY")
    if env_key:
        key = env_key.strip().encode("ascii")
        _validate_key(key)
        return key

    path = _key_path()
    if path.exists():
        key = path.read_text().strip().encode("ascii")
        _validate_key(key)
        return key

    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600：仅属主可读写
    return key


def encrypt_secret(secret: bytes) -> str:
    """明文 secret → Fernet token 字符串（落 ``app_config.value["enc_secret"]``）。"""
    return Fernet(get_master_key()).encrypt(secret).decode("ascii")


def decrypt_secret(token: str) -> bytes | None:
    """Fernet token → 明文 secret；密文损坏 / key 不匹配返回 None（按不可验签处理）。"""
    try:
        return Fernet(get_master_key()).decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError):
        return None
