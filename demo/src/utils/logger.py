"""通用日志配置。

服务是常驻进程，日志需要同时输出到控制台并写入轮转文件。配置函数可重复调用，不会重复挂载处理器导致同一日志打印多遍；输出显式使用 UTF-8，避免中文在 GBK 控制台抛出编码错误；文件按大小轮转；第三方库只保留警告及以上级别。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from config import settings

# 本项目的根日志器名，所有模块都位于该命名空间下
_APP_LOGGER = "src"

_configured = False


class _Utf8StreamHandler(logging.StreamHandler):
    """强制 UTF-8 输出的控制台处理器，Windows 下标准错误默认使用 GBK。"""

    def __init__(self, stream=None) -> None:
        super().__init__(stream or sys.stderr)
        reconfigure = getattr(self.stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except UnicodeEncodeError:
            # 最后兜底，替换无法编码的字符，避免日志导致程序崩溃
            record = logging.makeLogRecord(record.__dict__)
            record.msg = str(record.msg).encode("utf-8", "replace").decode("utf-8", "replace")
            record.args = None
            super().emit(record)


def setup_logging(
    level: str | None = None,
    *,
    log_dir: Path | None = None,
    console: bool | None = None,
    force: bool = False,
) -> logging.Logger:
    """
    配置根日志器，输出到控制台与轮转文件。

    日志级别、目录与控制台开关默认取配置值；强制重建开关为真时先清空已有处理器再配置，供测试使用。返回应用根日志器。
    """
    global _configured

    logger = logging.getLogger()
    if _configured and not force:
        return logging.getLogger(_APP_LOGGER)

    cfg = settings.log
    lvl = (level or cfg.level).upper()
    to_console = cfg.console if console is None else console
    directory = Path(log_dir) if log_dir else cfg.dir

    if force:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass

    logger.setLevel(lvl)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if to_console and not any(isinstance(h, _Utf8StreamHandler) for h in logger.handlers):
        ch = _Utf8StreamHandler()
        ch.setLevel(lvl)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    # 文件日志初始化失败时只告警，不影响服务启动，例如目录只读
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / cfg.file_name
        already = any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            and Path(getattr(h, "baseFilename", "")) == target.resolve()
            for h in logger.handlers
        )
        if not already:
            fh = logging.handlers.RotatingFileHandler(
                target,
                maxBytes=cfg.max_bytes,
                backupCount=cfg.backup_count,
                encoding="utf-8",
            )
            fh.setLevel(lvl)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("文件日志初始化失败（继续使用控制台日志）: %s", exc)

    # 降低第三方库日志级别，避免淹没关键日志
    for noisy, noisy_level in (
        ("httpx", logging.WARNING),
        ("httpcore", logging.WARNING),
        ("urllib3", logging.WARNING),
        ("openai", logging.WARNING),
        ("matplotlib", logging.WARNING),
        ("PIL", logging.WARNING),
        ("chromadb", logging.WARNING),
        ("sentence_transformers", logging.WARNING),
        ("transformers", logging.WARNING),
        ("uvicorn.access", logging.INFO),
    ):
        logging.getLogger(noisy).setLevel(noisy_level)

    _configured = True
    logging.getLogger(_APP_LOGGER).debug(
        "日志已配置: level=%s dir=%s console=%s", lvl, directory, to_console
    )
    return logging.getLogger(_APP_LOGGER)


def get_logger(name: str) -> logging.Logger:
    """获取模块日志器，只返回日志器，不额外挂载处理器，统一由配置函数管理。"""
    return logging.getLogger(name)


def setup_api_logging() -> logging.Logger:
    """服务启动时的便捷入口。"""
    return setup_logging()
