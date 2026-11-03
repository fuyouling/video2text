"""日志工具"""

import logging
import os
import re
import threading
from pathlib import Path
from logging.handlers import RotatingFileHandler

from src.i18n import t

_CONFIGURED_LOGGERS: set[str] = set()
_CONFIGURE_LOCK = threading.Lock()

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)

_RE_BEARER = re.compile(r'(Bearer\s+)([a-zA-Z0-9_\-\.]{8,})', re.IGNORECASE)
_RE_SK_KEY = re.compile(r'\b(sk-[a-zA-Z0-9_\-]{6,})\b')
_RE_GENERIC_KEY = re.compile(
    r'(["\']?(?:api[_-]?key|access[_-]?token|secret[_-]?key|auth[_-]?token)["\']?\s*[:=]\s*["\']?)([^"\'\s,\r\n]{8,})(["\']?)',
    re.IGNORECASE,
)


def mask_sensitive_data(text: str) -> str:
    """对日志中的敏感凭据（sk-*, Bearer *, API Key）进行掩码脱敏。"""
    if not text:
        return text

    # 1. Bearer <token>
    text = _RE_BEARER.sub(r'\1***MASKED***', text)

    # 2. sk-* (OpenAI, DeepSeek, Anthropic 等)
    def _mask_sk(m: re.Match) -> str:
        val = m.group(1)
        if len(val) > 12:
            return f"{val[:4]}...{val[-4:]}"
        return f"{val[:3]}****"

    text = _RE_SK_KEY.sub(_mask_sk, text)

    # 3. 通用 api_key / secret_key / access_token
    def _mask_generic(m: re.Match) -> str:
        prefix = m.group(1)
        val = m.group(2)
        suffix = m.group(3)
        if len(val) > 8:
            masked = f"{val[:2]}...{val[-2:]}"
        else:
            masked = "****"
        return f"{prefix}{masked}{suffix}"

    text = _RE_GENERIC_KEY.sub(_mask_generic, text)
    return text


class _ShortPathFormatter(logging.Formatter):
    """自动缩短日志中的文件路径和 logger 名称，并集成敏感凭据脱敏。"""

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

        return mask_sensitive_data(msg)


class SafeRotatingFileHandler(RotatingFileHandler):
    """Windows 下安全的 RotatingFileHandler，防止 rollover 期间发生 WinError 32 崩溃。"""

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            # 在 Windows 上若文件被其他句柄持有，重命名会引发 PermissionError (WinError 32)
            # 捕获并安全忽略，避免程序因日志轮转失败而崩溃
            pass
        except OSError:
            pass


_SHARED_FILE_HANDLERS: dict[str, SafeRotatingFileHandler] = {}
_HANDLER_POOL_LOCK = threading.Lock()


def get_shared_rotating_handler(
    file_path: Path | str,
    maxBytes: int = 10 * 1024 * 1024,
    backupCount: int = 5,
    encoding: str = "utf-8",
) -> SafeRotatingFileHandler:
    """获取全局共享的 SafeRotatingFileHandler 单例，按文件绝对路径复用。"""
    resolved_path = str(Path(file_path).resolve())
    with _HANDLER_POOL_LOCK:
        if resolved_path not in _SHARED_FILE_HANDLERS:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            handler = SafeRotatingFileHandler(
                resolved_path,
                maxBytes=maxBytes,
                backupCount=backupCount,
                encoding=encoding,
            )
            _SHARED_FILE_HANDLERS[resolved_path] = handler
        return _SHARED_FILE_HANDLERS[resolved_path]


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
        raise ValueError(t("logger.invalid_level", level=level))

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

            app_handler = get_shared_rotating_handler(
                log_path / "app.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=7,
                encoding="utf-8",
            )
            app_handler.setLevel(logging.INFO)
            app_handler.setFormatter(formatter)
            if app_handler not in logger.handlers:
                logger.addHandler(app_handler)

            debug_handler = get_shared_rotating_handler(
                log_path / "debug.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            debug_handler.setLevel(logging.DEBUG)
            debug_handler.setFormatter(formatter)
            if debug_handler not in logger.handlers:
                logger.addHandler(debug_handler)

            error_handler = get_shared_rotating_handler(
                log_path / "error.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=30,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            if error_handler not in logger.handlers:
                logger.addHandler(error_handler)

        if log_to_console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level_int)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

        _CONFIGURED_LOGGERS.add(name)
    return logger


def setup_dependency_logger(
    name: str,
    log_dir: str = "logs",
    level: str = "INFO",
    log_to_file: bool = True,
    log_to_console: bool = True,
) -> logging.Logger:
    """配置「启动依赖检测」专用 logger，输出干净、带对齐树状的日志。"""
    level_upper = level.upper()
    level_int = logging.getLevelName(level_upper)
    if not isinstance(level_int, int):
        raise ValueError(t("logger.invalid_level", level=level))

    logger = logging.getLogger(name)

    with _CONFIGURE_LOCK:
        if name in _CONFIGURED_LOGGERS:
            logger.setLevel(level_int)
            return logger

        logger.setLevel(level_int)
        logger.propagate = False
        logger.handlers.clear()

        # 干净格式：仅时间 + 级别 + 消息，无 logger 名与冗余分隔，同样经过脱敏
        class _CleanFormatter(_ShortPathFormatter):
            pass

        formatter = _CleanFormatter(
            "%(asctime)s  %(levelname)-5s  %(message)s",
            datefmt="%H:%M:%S",
        )

        if log_to_file:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)
            dep_handler = get_shared_rotating_handler(
                log_path / "dependency.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            dep_handler.setLevel(logging.INFO)
            dep_handler.setFormatter(formatter)
            if dep_handler not in logger.handlers:
                logger.addHandler(dep_handler)

        if log_to_console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level_int)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

        _CONFIGURED_LOGGERS.add(name)
    return logger


def get_logger(name: str) -> logging.Logger:
    """获取日志记录器"""
    return logging.getLogger(name)
