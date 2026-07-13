"""Matrix Master 内部 REST API（Web frontend 调用）。

公开入口：
- ``app`` — 默认构造的 FastAPI 实例（用于 ``uvicorn matrix.api.app:app``）
- ``create_app`` — 工厂函数，方便测试 / 嵌入式启动
- ``schemas`` — Pydantic schema 集合
- ``deps`` — FastAPI 依赖（DB session / 鉴权）
"""
from matrix import __version__

from matrix.api.app import app, create_app
from matrix.api import deps, schemas

__all__ = [
    "app",
    "create_app",
    "deps",
    "schemas",
    "__version__",
]
