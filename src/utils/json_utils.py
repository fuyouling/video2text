"""JSON 文件读写工具 — 统一错误处理与原子写入"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from src.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def safe_read_json(file_path: Path, default: T = None) -> T:
    """安全读取 JSON 文件，失败时返回默认值并记录 warning。

    Args:
        file_path: JSON 文件路径
        default: 读取失败时返回的默认值

    Returns:
        解析后的数据，失败时返回 default
    """
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("读取 JSON 文件失败 %s: %s", file_path.name, exc)
        return default


def atomic_write_json(file_path: Path, data: Any) -> None:
    """原子写入 JSON 文件，防止崩溃或磁盘满导致部分写入。

    Args:
        file_path: 目标文件路径（父目录须已存在）
        data: 可 JSON 序列化的数据
    """
    fd, tmp_path = tempfile.mkstemp(dir=file_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.replace(tmp_path, str(file_path))
        except OSError:
            shutil.move(tmp_path, str(file_path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
