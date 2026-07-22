"""集中配置（pydantic-settings）。

设计：
- ``Settings`` 本体宽松（允许缺字段），方便 alembic / 测试 / dev 工具加载。
- 生产入口用 ``require_settings()`` 做必填校验，未设必填字段直接 raise（fail-fast）。
- 所有原 ``os.environ.get(...)`` 调用点改为 ``get_settings()`` / ``require_settings()``，
  由 ``.env`` 文件 / docker env_file / 系统环境变量注入。

不在本模块做的事：
- 不直接调 provider client；这里只暴露字段。
- 不缓存数据库 URL 解析结果（由 ``matrix.db.engine`` 自己做）。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中配置。所有字段可由同名环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ===== 必填（生产 fail-fast 由 require_settings() 校验）=====
    database_url: Optional[str] = Field(
        default=None,
        description="asyncpg / aiosqlite URL；alembic.ini 不再有 fallback。",
    )
    matrix_api_secret: Optional[str] = Field(
        default=None,
        description="主 API Bearer token；>=32 字符随机串。",
    )

    # ===== LLM providers（按需选填，未填的 provider 不可用）=====
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None  # OpenAI 兼容端点（硅基流动等）
    embedding_base_url: Optional[str] = None  # embedder 自定义 base_url（硅基流动等）
    anthropic_api_key: Optional[str] = None
    dashscope_api_key: Optional[str] = None
    zhipuai_api_key: Optional[str] = None
    doubao_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    minimax_api_key: Optional[str] = None

    # ===== LLM 路由默认值 =====
    matrix_llm_provider: str = Field(default="tongyi")
    matrix_llm_model: Optional[str] = Field(
        default=None,
        description="覆盖 provider 默认模型；未设则用 _default_model_for(provider)。",
    )

    # ===== 图像生成 =====
    matrix_image_provider: str = Field(default="in_memory")
    dashscope_base_url: Optional[str] = None
    zhipuai_base_url: Optional[str] = None
    doubao_base_url: Optional[str] = None
    minimax_base_url: Optional[str] = None

    # ===== Tailscale / Headscale =====
    ts_api_url: Optional[str] = None
    ts_api_key: Optional[str] = None

    # ===== 监控 =====
    otlp_endpoint: Optional[str] = None


@lru_cache
def get_settings() -> Settings:
    """进程内缓存的 Settings 实例。"""
    return Settings()


def require_settings() -> Settings:
    """生产入口：必填校验，缺则 raise（fail-fast）。

    校验：
    - ``database_url`` 非空
    - ``matrix_api_secret`` 非空且 >=32 字符

    调用点：
    - ``matrix.api.app:create_app()`` 启动时
    - 其他不允许裸奔的入口

    alembic / 测试用 ``get_settings()`` 即可，不强制。
    """
    s = get_settings()
    missing: list[str] = []
    if not s.database_url:
        missing.append("DATABASE_URL")
    if not s.matrix_api_secret:
        missing.append("MATRIX_API_SECRET")
    elif len(s.matrix_api_secret) < 32:
        raise RuntimeError(
            f"MATRIX_API_SECRET must be at least 32 characters "
            f"(got {len(s.matrix_api_secret)})"
        )
    if missing:
        raise RuntimeError(
            "required env vars not set: " + ", ".join(missing) +
            "; copy .env.example to .env and fill them in"
        )
    return s


def reset_settings_cache() -> None:
    """清 Settings 缓存（测试用）。"""
    get_settings.cache_clear()


__all__ = [
    "Settings",
    "get_settings",
    "require_settings",
    "reset_settings_cache",
]