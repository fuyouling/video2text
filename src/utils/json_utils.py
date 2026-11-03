"""JSON 文件读写工具 — 统一错误处理与原子写入"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, TypeVar

from src.i18n import t
from src.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def safe_read_json(file_path: Path, default: T = None) -> T:
    """安全读取 JSON 文件，失败时返回默认值并记录 warning。

    遇到 JSONDecodeError 时：
    1. 自动将损坏文件隔离备份至 `<file>.corrupted.<timestamp>.bak`；
    2. 尝试从已有的 `<file>.bak` 或同名 `.bak` 备份恢复数据，彻底避免静默抹除历史记录。

    Args:
        file_path: JSON 文件路径
        default: 读取失败且无可用备份时返回的默认值

    Returns:
        解析后的数据，失败时返回 default
    """
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        logger.warning(t("storage.json.read_fail", name=file_path.name, error=exc))
        # 1. 自动隔离备份损坏文件
        try:
            timestamp = int(time.time())
            corrupted_backup = file_path.with_name(f"{file_path.name}.corrupted.{timestamp}.bak")
            shutil.copy2(file_path, corrupted_backup)
            logger.warning(f"Corrupted JSON file backed up to: {corrupted_backup.name}")
        except Exception as backup_err:
            logger.warning(f"Failed to isolate corrupted JSON file {file_path.name}: {backup_err}")

        # 2. 尝试从 .bak 备份文件恢复
        bak_candidates = [
            file_path.with_name(f"{file_path.name}.bak"),
            file_path.with_suffix(".bak"),
        ]
        for bak_path in bak_candidates:
            if bak_path.exists() and bak_path != file_path:
                try:
                    data = json.loads(bak_path.read_text(encoding="utf-8"))
                    logger.info(f"Recovered JSON data for {file_path.name} from backup {bak_path.name}")
                    try:
                        atomic_write_json(file_path, data)
                    except Exception:
                        pass
                    return data
                except Exception:
                    continue

        return default
    except OSError as exc:
        logger.warning(t("storage.json.read_fail", name=file_path.name, error=exc))
        return default


def atomic_write_json(file_path: Path, data: Any) -> None:
    """原子写入 JSON 文件，防止崩溃或磁盘满导致部分写入。

    Args:
        file_path: 目标文件路径（父目录须已存在）
        data: 可 JSON 序列化的数据
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=file_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
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
