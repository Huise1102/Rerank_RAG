"""结构化日志，用 loguru 统一控制台与文件输出。

标准库 logging 的记录经 InterceptHandler 转发到 loguru。
每个请求的请求 ID 由中间件写入上下文变量，输出日志时补上该字段。
日志文件按大小轮转，保留份数取自日志配置。
"""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid
from typing import Any

from loguru import logger as _loguru_logger

from src.core.config import settings

# 当前请求的请求 ID，不在请求上下文时为空占位
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# 级别名到 loguru 级别的映射
_LEVEL_MAP = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}

_configured = False


def new_request_id() -> str:
    """生成 8 位十六进制的短请求 ID。"""
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
    """为每条日志补上请求 ID 与模块名。"""
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

    # 标准库 logging 一并转发到 loguru
    logging.basicConfig(handlers=[InterceptHandler()], level=lvl, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine", "chromadb"):
        std = logging.getLogger(name)
        std.handlers = [InterceptHandler()]
        std.propagate = False

    _configured = True


def clear_log_file() -> dict[str, Any]:
    """清空应用日志文件。

    先移除 loguru 输出端以释放文件句柄，再清空内容并重新初始化日志。
    返回文件路径与清空的字节数。
    """
    path = settings.log.dir / settings.log.file_name
    size = path.stat().st_size if path.exists() else 0
    _loguru_logger.remove()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    setup_logging()
    return {"path": str(path), "bytes_cleared": size}


class PercentStyleLogger:
    """把百分号风格的调用适配到 loguru。

    先尝试用百分号预格式化参数，成功时把结果作为纯文本交给 loguru；
    失败说明不是百分号风格，原样传入，由 loguru 按花括号占位处理。
    """

    __slots__ = ("_logger",)

    def __init__(self, logger: Any) -> None:
        self._logger = logger

    @staticmethod
    def _prepare(message: Any, args: tuple, kwargs: dict) -> tuple[Any, tuple, dict]:
        if args or kwargs:
            try:
                return message % (args if args else kwargs), (), {}
            except Exception:  # noqa: BLE001 - 不是 %-风格就原样交给 loguru
                pass
        return message, args, kwargs

    def debug(self, message: Any, *args: Any, **kwargs: Any) -> None:
        msg, a, kw = self._prepare(message, args, kwargs)
        self._logger.debug(msg, *a, **kw)

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        msg, a, kw = self._prepare(message, args, kwargs)
        self._logger.info(msg, *a, **kw)

    def success(self, message: Any, *args: Any, **kwargs: Any) -> None:
        msg, a, kw = self._prepare(message, args, kwargs)
        self._logger.success(msg, *a, **kw)

    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        msg, a, kw = self._prepare(message, args, kwargs)
        self._logger.warning(msg, *a, **kw)

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        msg, a, kw = self._prepare(message, args, kwargs)
        self._logger.error(msg, *a, **kw)

    def critical(self, message: Any, *args: Any, **kwargs: Any) -> None:
        msg, a, kw = self._prepare(message, args, kwargs)
        self._logger.critical(msg, *a, **kw)

    def exception(self, message: Any, *args: Any, **kwargs: Any) -> None:
        msg, a, kw = self._prepare(message, args, kwargs)
        self._logger.exception(msg, *a, **kw)

    def log(self, level: Any, message: Any, *args: Any, **kwargs: Any) -> None:
        msg, a, kw = self._prepare(message, args, kwargs)
        self._logger.log(level, msg, *a, **kw)

    def bind(self, **kwargs: Any) -> "PercentStyleLogger":
        return PercentStyleLogger(self._logger.bind(**kwargs))

    def opt(self, *args: Any, **kwargs: Any) -> "PercentStyleLogger":
        return PercentStyleLogger(self._logger.opt(*args, **kwargs))

    def patch(self, patcher: Any) -> "PercentStyleLogger":
        return PercentStyleLogger(self._logger.patch(patcher))


def get_logger(name: str | None = None) -> PercentStyleLogger:
    """获取绑定了模块名的日志器，支持百分号与花括号两种占位符风格。"""
    if not _configured:
        setup_logging()
    return PercentStyleLogger(_loguru_logger.bind(module=name or "-"))
