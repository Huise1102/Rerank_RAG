"""通用日志配置（src/utils/logger.py）。

API 是常驻服务，日志需落盘。

要点：``setup_logging()`` 幂等，多次调用（uvicorn reload、Streamlit 重跑、
测试导入）不会重复挂 handler 造成同一日志打印多遍；显式 UTF-8 编码，
否则中文与 emoji 在 GBK 控制台抛 UnicodeEncodeError；按大小轮转避免写满磁盘；
httpx/urllib3 等第三方库设为 WARNING，避免淹没关键日志。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from config import settings

#: 本项目的根日志器名（所有模块都是 src.* / config）
_APP_LOGGER = "src"

_configured = False


class _Utf8StreamHandler(logging.StreamHandler):
    """强制 UTF-8 输出的控制台 handler（Windows 下 stderr 默认 GBK）。"""

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
            # 极端情况下的最后兜底：丢掉无法编码的字符而不是让程序崩掉
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
    配置根日志器：控制台 + 轮转文件。

    :param level: 日志级别，默认取 ``settings.log.level``
    :param log_dir: 日志目录，默认取 ``settings.log.dir``
    :param console: 是否输出到控制台，默认取 ``settings.log.console``
    :param force: 为 True 时先清空已有 handler 再配置（测试用）
    :returns: 应用根日志器
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

    # 文件日志：失败也不能影响服务启动（例如目录只读）
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

    # 压制第三方库的噪声日志
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
    """获取模块日志器。

    与 ``src.retrieval.base.get_logger`` 不同：本函数只取日志器、不挂临时
    handler，统一由 ``setup_logging()`` 配置，避免双重 handler 重复输出。
    """
    return logging.getLogger(name)


def setup_api_logging() -> logging.Logger:
    """API 服务启动时的便捷入口。"""
    return setup_logging()
