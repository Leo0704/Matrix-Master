"""结构化日志配置（structlog → JSON）。

写入 ``~/.matrix/logs/{date}.jsonl``，单文件 100MB 滚动，保留 7 天。
字段：ts / level / run_id / device_id / account_id / action / latency_ms / error_code。

设计要点：
- structlog 处理格式化（JSON 序列化）
- 标准 logging 处理器负责文件 IO + 滚动（自定义 SizeTimedRotatingFileHandler）
- 提供 ``get_logger(name)`` 给业务代码使用，``bind_context()`` 注入请求级上下文
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from logging.handlers import BaseRotatingHandler
from pathlib import Path
from typing import Any

import structlog

DEFAULT_LOG_DIR = Path.home() / ".matrix" / "logs"
DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100MB
DEFAULT_BACKUP_DAYS = 7

# structlog 上下文字段名（业务代码应使用这些 key，便于 dashboard 聚合）
LOG_FIELDS = (
    "ts",
    "level",
    "run_id",
    "device_id",
    "account_id",
    "action",
    "latency_ms",
    "error_code",
)


class SizeTimedRotatingFileHandler(BaseRotatingHandler):
    """按天分割 + 单文件大小上限的文件 handler。

    行为：
    - 文件名 ``{YYYY-MM-DD}.jsonl``
    - 当天日期切换 → 切到新文件
    - 当前文件超过 ``maxBytes`` → rotate 成 ``{date}.jsonl.1``，新文件从 0 开始
    - 启动时清理 ``backup_days`` 天之前的旧文件
    """

    def __init__(
        self,
        log_dir: Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_days: int = DEFAULT_BACKUP_DAYS,
        encoding: str = "utf-8",
    ) -> None:
        self.log_dir = log_dir
        self.max_bytes = max_bytes
        self.backup_days = backup_days
        self._current_date: str | None = None
        self._current_path: Path | None = None
        log_dir.mkdir(parents=True, exist_ok=True)
        BaseRotatingHandler.__init__(
            self,
            filename=str(log_dir / "placeholder.log"),
            mode="a",
            encoding=encoding,
            delay=True,
        )

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _open_for(self, date_str: str) -> None:
        self._current_date = date_str
        self._current_path = self.log_dir / f"{date_str}.jsonl"
        self.baseFilename = str(self._current_path)
        if self.stream is None:
            self.stream = self._open()
        else:
            self.stream.close()
            self.stream = self._open()

    def _open(self):  # type: ignore[override]
        return open(self.baseFilename, self.mode, encoding=self.encoding)

    def emit(self, record: logging.LogRecord) -> None:
        today = self._today()
        if today != self._current_date:
            self._open_for(today)
        super().emit(record)

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if self.stream is None or self._current_path is None:
            return False
        try:
            pos = self.stream.tell()
        except (ValueError, OSError):
            return False
        msg = self.format(record)
        return pos + len(msg.encode(self.encoding or "utf-8")) >= self.max_bytes

    def doRollover(self) -> None:  # type: ignore[override]
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self._current_path is None:
            return
        rotated = self._current_path.with_suffix(".jsonl.1")
        if rotated.exists():
            rotated.unlink()
        self._current_path.rename(rotated)
        self._open_for(self._current_date or self._today())

    def cleanup_old(self) -> None:
        """清理 backup_days 天之前的文件。"""
        if self.backup_days <= 0:
            return
        cutoff = time.time() - self.backup_days * 86400
        for path in self.log_dir.glob("*.jsonl*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:  # pragma: no cover - 防御性
                continue


def configure_logging(
    log_dir: Path | None = None,
    level: str = "INFO",
    console: bool = True,
) -> None:
    """初始化 structlog + 标准 logging。

    Args:
        log_dir: 日志目录；默认 ``~/.matrix/logs``。
        level: 根日志级别。
        console: 是否同时输出到 stdout（开发模式）。
    """
    log_dir = log_dir or DEFAULT_LOG_DIR
    root = logging.getLogger()

    for h in list(root.handlers):
        root.removeHandler(h)

    root.setLevel(level.upper())

    formatter = logging.Formatter("%(message)s")
    file_handler = SizeTimedRotatingFileHandler(log_dir)
    file_handler.setFormatter(formatter)
    file_handler.cleanup_old()
    root.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _add_timestamp,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _normalize_record,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _add_timestamp(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    """注入 ``ts`` 字段（ISO8601 UTC，毫秒精度）。"""
    event_dict["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return event_dict


def _normalize_record(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    """保证白名单字段始终存在（缺失置 None），保留其余字段。"""
    for k in LOG_FIELDS:
        event_dict.setdefault(k, None)
    return event_dict


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取绑定到模块名的 logger。

    用法::

        log = get_logger(__name__)
        log.info("agent.run.start", run_id=run_id)
    """
    return structlog.get_logger(name) if name else structlog.get_logger()


def bind_context(**kwargs: Any) -> None:
    """绑定上下文（request 级 / run 级）。在 middleware / run 入口调用。"""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """清空 structlog 上下文。请求结束 / run 结束时调用。"""
    structlog.contextvars.clear_contextvars()
