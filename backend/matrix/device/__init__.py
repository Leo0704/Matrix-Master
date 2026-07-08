"""设备-账号管理子系统（SDD §3.5）。

- HMAC 签名 / 密钥 hash：``matrix.device.hmac``
- 密钥生命周期：``matrix.device.key_manager``
- 配对服务：``matrix.device.pairing``
- 设备注册 / 心跳：``matrix.device.registry``
- 账号-设备亲和：``matrix.device.account_binding``
- Tailscale 客户端：``matrix.device.tailscale_client``
- 登录态监控：``matrix.device.login_state``
- HTTP API：``matrix.device.api``
"""
from matrix.device.account_binding import (
    AccountBinding,
    AccountBindingError,
    BindingResult,
)
from matrix.device.api import router as device_router
from matrix.device.hmac import (
    SIGNATURE_SEP,
    compute_signature,
    generate_key,
    hash_key,
    verify_signature,
)
from matrix.device.key_manager import (
    DEFAULT_ROTATION_DAYS,
    IssuedKey,
    KeyManager,
)
from matrix.device.login_state import (
    VALID_RESULTS,
    LoginStateError,
    LoginStateMonitor,
    LoginStateReport,
)
from matrix.device.pairing import (
    PAIR_CODE_TTL_SECONDS,
    PairingCode,
    PairingError,
    PairingResult,
    PairingService,
)
from matrix.device.registry import (
    DeviceHeartbeatData,
    DeviceNotFound,
    DeviceRegistry,
)
from matrix.device.tailscale_client import (
    TailscaleClient,
    TailscaleError,
    TailscaleNode,
)

__all__ = [
    # hmac
    "SIGNATURE_SEP",
    "generate_key",
    "compute_signature",
    "verify_signature",
    "hash_key",
    # key_manager
    "IssuedKey",
    "KeyManager",
    "DEFAULT_ROTATION_DAYS",
    # pairing
    "PairingService",
    "PairingCode",
    "PairingResult",
    "PairingError",
    "PAIR_CODE_TTL_SECONDS",
    # registry
    "DeviceRegistry",
    "DeviceHeartbeatData",
    "DeviceNotFound",
    # account_binding
    "AccountBinding",
    "AccountBindingError",
    "BindingResult",
    # tailscale
    "TailscaleClient",
    "TailscaleError",
    "TailscaleNode",
    # login_state
    "LoginStateMonitor",
    "LoginStateReport",
    "LoginStateError",
    "VALID_RESULTS",
    # api
    "device_router",
]
