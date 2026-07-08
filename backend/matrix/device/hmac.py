"""HMAC 签名工具（与 docs/api/apk-http.openapi.yaml 一致）。

签名格式：``{timestamp}\\n{request_id}\\n{body_sha256}``，
密钥 hash 用 SHA-256（存 db 用，明文不落盘）。

参考：
- SDD.md §3.5.2（密钥配对流程）
- threat-model.md §6.3（HMAC 密钥生命周期）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Union

# 签名内容里的换行符：与 APK 端约定保持 \n
SIGNATURE_SEP = "\n"


def generate_key() -> bytes:
    """生成 256 bit（32 字节）随机 HMAC 共享密钥。"""
    return os.urandom(32)


def _body_digest(body: Union[str, bytes, None]) -> str:
    """计算 body 的 SHA-256 摘要（hex 编码），body 为 None/空时用空串。"""
    if body is None:
        data = b""
    elif isinstance(body, str):
        data = body.encode("utf-8")
    else:
        data = body
    return hashlib.sha256(data).hexdigest()


def _build_signing_string(timestamp: Union[int, str], request_id: str, body: Union[str, bytes, None]) -> bytes:
    """构造待签名的字节串：``{timestamp}\\n{request_id}\\n{body_sha256}``。"""
    body_hash = _body_digest(body)
    return f"{timestamp}{SIGNATURE_SEP}{request_id}{SIGNATURE_SEP}{body_hash}".encode("utf-8")


def compute_signature(
    secret: bytes,
    timestamp: Union[int, str],
    request_id: str,
    body: Union[str, bytes, None],
) -> str:
    """计算 HMAC-SHA256 签名，返回 base64 编码字符串。

    Args:
        secret: 共享密钥（明文，仅签名时使用，不入 DB）
        timestamp: Unix 时间戳（秒，int 或 str）
        request_id: 幂等 key
        body: 请求体（str / bytes / None）
    """
    msg = _build_signing_string(timestamp, request_id, body)
    digest = hmac.new(secret, msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_signature(
    secret: bytes,
    timestamp: Union[int, str],
    request_id: str,
    body: Union[str, bytes, None],
    signature: str,
    ttl_seconds: int = 300,
) -> bool:
    """校验 HMAC 签名 + 时间戳新鲜度。

    Args:
        secret: 共享密钥
        timestamp: 请求时间戳
        request_id: 幂等 key
        body: 请求体
        signature: 客户端传来的 base64 签名
        ttl_seconds: 时间戳有效期（秒），默认 300s（5 分钟）

    Returns:
        True 表示签名 + 时间戳均合法；False 表示任意一项不合法。
    """
    if not signature:
        return False

    # 1) 校验时间戳新鲜度
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if abs(now - ts_int) > ttl_seconds:
        return False

    # 2) 校验签名（用 compare_digest 防时序攻击）
    expected = compute_signature(secret, ts_int, request_id, body)
    try:
        return hmac.compare_digest(expected, signature)
    except (TypeError, ValueError):
        return False


def hash_key(secret: bytes) -> bytes:
    """对 HMAC 密钥做 SHA-256 hash（用于持久化到 ``device_hmac_keys.key_hash``）。

    SHA-256 是单向函数；DB 泄漏时无法反推明文。
    """
    return hashlib.sha256(secret).digest()


__all__ = [
    "SIGNATURE_SEP",
    "generate_key",
    "compute_signature",
    "verify_signature",
    "hash_key",
]
