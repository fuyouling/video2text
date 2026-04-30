"""日志工具"""

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

_CONFIGURED_LOGGERS: set[str] = set()


def setup_logger(
    name: str,
    log_dir: str = "logs",
    level: str = "INFO",
    log_to_file: bool = True,
    log_to_console: bool = True,
) -> logging.Logger:
    """设置日志记录器

    注意：只会清除 *指定名称* 的 logger 的 handlers，不会影响其他 logger。

    Args:
        name: 日志记录器名称
        log_dir: 日志目录
        level: 日志级别
        log_to_file: 是否记录到文件
        log_to_console: 是否输出到控制台

    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # 只清除当前 logger 的 handlers，不影响 root 或其他 logger
    logger.handlers.clear()

    formatter = logging.Formatter(
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
        console_handler.setLevel(getattr(logging, level.upper()))
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


@contextmanager
def log_step(
    step_name: str,
    logger_name: str = "video2text",
    level: str = "INFO",
):
    """步骤级日志上下文管理器，自动记录步骤开始、完成和失败。

    Args:
        step_name: 步骤名称
        logger_name: 日志记录器名称
        level: 日志级别

    Usage:
        with log_step("音频提取"):
            do_something()
    """
    log = logging.getLogger(logger_name)
    log_level = getattr(logging, level.upper())
    start_ts = time.time()

    log.log(log_level, "▶ 步骤开始: %s", step_name)
    try:
        yield
        elapsed = time.time() - start_ts
        log.log(log_level, "✔ 步骤完成: %s (%.2fs)", step_name, elapsed)
    except Exception as e:
        elapsed = time.time() - start_ts
        log.error("✘ 步骤失败: %s (%.2fs) - %s", step_name, elapsed, e)
        raise


def log_error_with_context(
    logger_name: str,
    step_name: str,
    error: Exception,
    video_path: str = "",
) -> None:
    """记录带上下文信息的错误日志。

    Args:
        logger_name: 日志记录器名称
        step_name: 失败步骤名称
        error: 异常对象
        video_path: 相关视频路径
    """
    log = logging.getLogger(logger_name)
    context_parts = [f"步骤: {step_name}"]
    if video_path:
        context_parts.append(f"文件: {video_path}")
    context_parts.append(f"错误: {error}")
    log.error(" | ".join(context_parts))
