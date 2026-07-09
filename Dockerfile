# syntax=docker/dockerfile:1.7
#
# Matrix Master 后端测试镜像
#
# 设计要点：
# - 基础镜像 python:3.11-slim（与 pyproject.toml requires-python = ">=3.11" 对齐）
# - 包管理用 uv（仓库根已有 uv.lock，缓存友好）
# - 依赖层与源码层分离：仅 pyproject.toml / uv.lock / README.md 变化才重装依赖
# - PYTHONPATH=/app/backend：让 `from matrix.xxx import yyy` 正常工作
# - PATH 指向 /app/.venv/bin：pytest / alembic 直接可用
# - 默认 CMD = pytest tests；在 docker-compose 里 working_dir=/app/backend，所以路径是 backend 内的相对路径
# - 开发态 backend/ 会被 bind mount 覆盖，源码改动立即生效，无需 rebuild
#
# 使用：
#   docker compose run --rm test                          # 跑全部测试
#   docker compose run --rm test pytest tests/test_x.py   # 单文件
#   docker compose run --rm test pytest tests -k agent    # 按关键字
#   docker compose run --rm test alembic upgrade head     # 跑迁移

FROM python:3.11-slim AS base

# uv 装到独立层（COPY --from 比 RUN curl 稳定）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 依赖层：仅当 pyproject.toml / uv.lock / README.md 变化才重装
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --extra dev --no-install-project

# 业务代码（开发态会被 docker-compose volumes 覆盖）
COPY backend ./backend

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/backend" \
    # cache_dir=/tmp/pytest-cache：避免 pytest 在 bind mount 工作目录写 .pytest_cache
    PYTEST_ADDOPTS="--override-ini=cache_dir=/tmp/pytest-cache"

CMD ["pytest", "tests"]