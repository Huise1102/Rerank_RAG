"""结构化日志（logging.py）：用 loguru 统一控制台与文件输出。

设计要点：
- 通过 :class:`InterceptHandler` 把标准库 ``logging``（demo 复制过来的 retrieval/ingest
  模块用的是 stdlib logger）统一转发到 loguru，避免两套日志系统各自为政。
- 每个请求注入 ``request_id``：HTTP 中间件在 :data:`request_id_var` 里设置，loguru 的
  ``patch`` 在每条日志上补上该字段，便于按请求串联日志。
- 文件落 ``logs/app.log``，按大小轮转，保留 ``log.backup_count`` 份。

用法::

    from src.core.logging import setup_logging, get_logger, request_id_var
    setup_logging()
    logger = get_logger(__name__)
    logger.info("...")
"""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid

from loguru import logger as _loguru_logger

from src.core.config import settings

#: 当前请求的 request_id；未处于请求上下文时为 "-"
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

#: 级别名 -> loguru 级别
_LEVEL_MAP = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}

_configured = False


def new_request_id() -> str:
    """生成一个短的 request_id（8 位十六进制）。"""
    return uuid.uuid4().hex[:8]


class InterceptHandler(logging.Handler):
    """把标准库 logging 的记录转发到 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - 运行时路径
        try:
            level = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _format_record(record: dict) -> str:
    """为每条日志补上 request_id 与模块名。"""
    record["extra"].setdefault("request_id", request_id_var.get())
    record["extra"].setdefault("module", "-")
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[module]}</cyan> | "
        "<yellow>{extra[request_id]}</yellow> | "
        "<level>{message}</level>\n{exception}"
    )


def setup_logging(level: str | None = None) -> None:
    """配置 loguru（幂等，可重复调用）。"""
    global _configured

    cfg = settings.log
    lvl = _LEVEL_MAP.get((level or cfg.level).upper(), "INFO")

    _loguru_logger.remove()
    _loguru_logger.add(
        sys.stderr,
        level=lvl,
        format=_format_record,
        colorize=True,
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )

    cfg.dir.mkdir(parents=True, exist_ok=True)
    _loguru_logger.add(
        str(cfg.dir / cfg.file_name),
        level=lvl,
        format=_format_record,
        rotation=f"{cfg.max_bytes}B",
        retention=cfg.backup_count,
        encoding="utf-8",
        colorize=False,
        backtrace=False,
        diagnose=False,
        enqueue=True,
    )

    # 标准库 logging 一并转发，避免 demo 复制模块的日志丢失
    logging.basicConfig(handlers=[InterceptHandler()], level=lvl, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine", "chromadb"):
        std = logging.getLogger(name)
        std.handlers = [InterceptHandler()]
        std.propagate = False

    _configured = True


def get_logger(name: str | None = None):
    """获取绑定了模块名的 loguru logger。"""
    if not _configured:
        setup_logging()
    return _loguru_logger.bind(module=name or "-")
