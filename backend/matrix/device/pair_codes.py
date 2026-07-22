"""配对码持久化存储（跨进程 / 跨 worker 共享）。

v0.7 Phase 6：Pull 模型下，配对码由 admin 接口（/devices/{id}/issue_pair）生成，
由 APK 接口（/api/v1/devices/{id}/pair）消费。两个接口可能跑在不同 uvicorn worker
甚至不同进程里，因此配对码必须落盘共享，不能仅存于进程内存。

存储：JSON 文件（默认 /app/backend/.pair_codes.json），每次读写原子重命名。
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

PAIR_CODE_TTL_SECONDS = 600

_PAIR_CODES_PATH = Path(
    os.environ.get("MATRIX_PAIR_CODES_PATH", "/app/backend/.pair_codes.json")
)


def _load_pair_codes() -> dict[str, tuple[str, float, Optional[float]]]:
    """Load from disk.

    Returns mapping code → (device_id_str, expires_at_wall, consumed_at_wall|None)。
    """
    if not _PAIR_CODES_PATH.exists():
        return {}
    try:
        raw = json.loads(_PAIR_CODES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, tuple[str, float, Optional[float]]] = {}
    for code, v in raw.items():
        if isinstance(v, list) and len(v) >= 3:
            cons = v[2]
            out[code] = (v[0], float(v[1]), float(cons) if cons is not None else None)
        else:
            out[code] = (v[0], float(v[1]), None)
    return out


def _save_pair_codes(
    codes: dict[str, tuple[uuid.UUID, float, Optional[float]]],
) -> None:
    """Atomic write: write to temp file, then rename."""
    serializable = {
        code: (
            str(device_id),
            float(expires_at_wall),
            float(consumed_at) if consumed_at is not None else None,
        )
        for code, (device_id, expires_at_wall, consumed_at) in codes.items()
    }
    try:
        _PAIR_CODES_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".pair_codes.", dir=str(_PAIR_CODES_PATH.parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(serializable, f)
            os.replace(tmp, _PAIR_CODES_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning(f"pair_codes persist failed: {e}")


def _purge_expired(
    codes: dict[str, tuple[uuid.UUID, float, Optional[float]]],
) -> dict[str, tuple[uuid.UUID, float, Optional[float]]]:
    now = time.time()
    return {c: v for c, v in codes.items() if v[1] > now}


def _codes() -> dict[str, tuple[uuid.UUID, float, Optional[float]]]:
    """每次都从磁盘重读——文件是唯一可信源。"""
    loaded = _load_pair_codes()
    return {c: (uuid.UUID(d), e, cons) for c, (d, e, cons) in loaded.items()}


def issue_pair_code(device_id: uuid.UUID) -> str:
    """生成并持久化一个配对码，返回 6 位数字字符串。"""
    codes = _purge_expired(_codes())
    expires_at_wall = time.time() + PAIR_CODE_TTL_SECONDS
    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code not in codes:
            codes[code] = (device_id, expires_at_wall, None)
            _save_pair_codes(codes)
            return code


def claim_pair_code(pair_code: str) -> Optional[uuid.UUID]:
    """原子领用配对码：验证有效后标记 consumed_at，返回 device_id；无效返回 None。"""
    codes = _purge_expired(_codes())
    entry = codes.get(pair_code)
    if entry is None:
        return None
    device_id, expires_at_wall, consumed_at = entry
    now = time.time()
    if consumed_at is not None and (now - consumed_at) < 60:
        return None
    codes[pair_code] = (device_id, expires_at_wall, now)
    _save_pair_codes(codes)
    return device_id


def finalize_pair_code(pair_code: str) -> None:
    """配对成功、DB commit 后调用：真正删除配对码条目。"""
    codes = _codes()
    if pair_code in codes:
        codes.pop(pair_code, None)
        _save_pair_codes(codes)


def restore_pair_code(pair_code: str) -> None:
    """领用后事务失败时调用：清掉 consumed_at，让码可重新领用。"""
    codes = _codes()
    entry = codes.get(pair_code)
    if entry is None:
        return
    device_id, expires_at_wall, _ = entry
    codes[pair_code] = (device_id, expires_at_wall, None)
    _save_pair_codes(codes)


__all__ = [
    "issue_pair_code",
    "claim_pair_code",
    "finalize_pair_code",
    "restore_pair_code",
    "PAIR_CODE_TTL_SECONDS",
]
