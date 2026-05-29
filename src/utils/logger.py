"""日志工具"""

import logging
import os
import threading
from pathlib import Path
from logging.handlers import RotatingFileHandler

_CONFIGURED_LOGGERS: set[str] = set()
_CONFIGURE_LOCK = threading.Lock()

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)


class _ShortPathFormatter(logging.Formatter):
    """自动缩短日志中的文件路径和 logger 名称。"""

    def format(self, record):
        if record.name:
            parts = record.name.split(".")
            if len(parts) > 1 and parts[0] == "src":
                record = logging.makeLogRecord(record.__dict__)
                record.name = parts[-1]

        msg = super().format(record)

        if _PROJECT_ROOT:
            for sep in (os.sep, "/", "\\"):
                prefix = _PROJECT_ROOT + sep
                if prefix in msg:
                    msg = msg.replace(prefix, "")
                    break
        return msg


def setup_logger(
    name: str,
    log_dir: str = "logs",
    level: str = "INFO",
    log_to_file: bool = True,
    log_to_console: bool = True,
) -> logging.Logger:
    """设置日志记录器

    注意：只会清除 *指定名称* 的 logger 的 handlers，不会影响其他 logger。
    同名 logger 只会配置一次，后续调用直接返回已配置的实例。

    Args:
        name: 日志记录器名称
        log_dir: 日志目录
        level: 日志级别
        log_to_file: 是否记录到文件
        log_to_console: 是否输出到控制台

    Returns:
        配置好的日志记录器

    Raises:
        ValueError: level 不是有效的日志级别名称
    """
    level_upper = level.upper()
    level_int = logging.getLevelName(level_upper)
    if not isinstance(level_int, int):
        raise ValueError(f"无效的日志级别: {level}")

    logger = logging.getLogger(name)

    with _CONFIGURE_LOCK:
        if name in _CONFIGURED_LOGGERS:
            logger.setLevel(level_int)
            return logger

        logger.setLevel(level_int)
        external_handlers = [
            h
            for h in logger.handlers
            if not isinstance(h, (RotatingFileHandler, logging.StreamHandler))
        ]
        logger.handlers.clear()
        for h in external_handlers:
            logger.addHandler(h)

        formatter = _ShortPathFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        if log_to_file:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)

            app_handler = RotatingFileHandler(
                log_path / "app.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=7,
                encoding="utf-8",
            )
            app_handler.setLevel(logging.INFO)
            app_handler.setFormatter(formatter)
            logger.addHandler(app_handler)

            debug_handler = RotatingFileHandler(
                log_path / "debug.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            debug_handler.setLevel(logging.DEBUG)
            debug_handler.setFormatter(formatter)
            logger.addHandler(debug_handler)

            error_handler = RotatingFileHandler(
                log_path / "error.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=30,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            logger.addHandler(error_handler)

        if log_to_console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level_int)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

        _CONFIGURED_LOGGERS.add(name)
    return logger


def get_logger(name: str) -> logging.Logger:
    """获取日志记录器

    Args:
        name: 日志记录器名称

    Returns:
        日志记录器
    """
    return logging.getLogger(name)


def log_error_with_context(
    logger_name: str,
    step_name: str,
    error: Exception,
    video_path: str = "",
) -> None:
    """记录带上下文信息的错误日志。

    输出格式与 log_panel.py 的 _RE_STEP 正则兼容，可被正确着色。

    Args:
        logger_name: 日志记录器名称
        step_name: 失败步骤名称
        error: 异常对象
        video_path: 相关文件路径
    """
    log = logging.getLogger(logger_name)
    log.error("  └─ %s ✗ 失败", step_name)
    # if video_path:
    #     log.error("  ├─ 文件: %s", Path(video_path).name)
    # log.error("  └─ 错误: %s", error)
