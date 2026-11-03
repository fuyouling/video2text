"""常用目录管理器 — 零 Qt 依赖，纯 Python 标准库

数据存储在 favorite_dirs.json 文件中（与 config.ini 同目录），格式：
{
    "input_dirs": ["path1", "path2"],
    "output_dirs": ["path1", "path2"]
}

第一次运行时文件不存在，目录列表为空。
"""

import os
import threading
from pathlib import Path

from src.i18n import t
from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DirectoryManager:
    """常用目录管理器 — 零 Qt 依赖，纯 Python 标准库

    非单例，由调用方持有实例。线程安全（内部加锁）。
    原子写入，防止崩溃损坏 JSON 文件。
    """

    MAX_ENTRIES = 20

    def __init__(self, file_path: Path):
        self._file_path = file_path
        self._lock = threading.Lock()
        self._input_dirs: list[str] = []
        self._output_dirs: list[str] = []
        self._load()

    def _load(self) -> None:
        """从 JSON 文件加载常用目录列表"""
        if not self._file_path.exists():
            return
        data = safe_read_json(self._file_path)
        if data is None:
            logger.warning(t("config_manager.dm_load_fail", name=self._file_path.name))
            return
        input_dirs = data.get("input_dirs", [])
        output_dirs = data.get("output_dirs", [])
        if not isinstance(input_dirs, list):
            logger.warning(t("config_manager.dm_input_dirs_format_error"))
            input_dirs = []
        else:
            input_dirs = [d for d in input_dirs if isinstance(d, str) and d.strip()]

        if not isinstance(output_dirs, list):
            logger.warning(t("config_manager.dm_output_dirs_format_error"))
            output_dirs = []
        else:
            output_dirs = [d for d in output_dirs if isinstance(d, str) and d.strip()]

        self._input_dirs = list(input_dirs[: self.MAX_ENTRIES])
        self._output_dirs = list(output_dirs[: self.MAX_ENTRIES])
        logger.info(t("config_manager.dm_load_success", name=self._file_path.name))

    def _save(self) -> None:
        """原子写入 JSON 文件"""
        data = {
            "input_dirs": self._input_dirs,
            "output_dirs": self._output_dirs,
        }
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._file_path, data)
        except OSError as exc:
            logger.error(t("config_manager.dm_save_fail", error=exc))
            raise

    def get_input_dirs(self) -> list[str]:
        """获取常用输入目录列表"""
        with self._lock:
            return list(self._input_dirs)

    def get_output_dirs(self) -> list[str]:
        """获取常用输出目录列表"""
        with self._lock:
            return list(self._output_dirs)

    def _normalize(self, path: str) -> str:
        """规范化路径用于去重比较（不改变存储格式）"""
        return os.path.normpath(os.path.normcase(path))

    def add_input_dir(self, path: str) -> None:
        """添加常用输入目录（去重，添加到列表头部）"""
        if not isinstance(path, str) or not path.strip():
            return
        with self._lock:
            old_dirs = list(self._input_dirs)
            norm = self._normalize(path)
            new_dirs = [
                d for d in self._input_dirs if self._normalize(d) != norm
            ]
            new_dirs.insert(0, path)
            self._input_dirs = new_dirs[: self.MAX_ENTRIES]
            try:
                self._save()
            except Exception:
                self._input_dirs = old_dirs
                raise

    def add_output_dir(self, path: str) -> None:
        """添加常用输出目录（去重，添加到列表头部）"""
        if not isinstance(path, str) or not path.strip():
            return
        with self._lock:
            old_dirs = list(self._output_dirs)
            norm = self._normalize(path)
            new_dirs = [
                d for d in self._output_dirs if self._normalize(d) != norm
            ]
            new_dirs.insert(0, path)
            self._output_dirs = new_dirs[: self.MAX_ENTRIES]
            try:
                self._save()
            except Exception:
                self._output_dirs = old_dirs
                raise

    def remove_input_dir(self, path: str) -> None:
        """从常用输入目录中移除指定路径"""
        if not isinstance(path, str) or not path.strip():
            return
        with self._lock:
            old_dirs = list(self._input_dirs)
            norm = self._normalize(path)
            self._input_dirs = [
                d for d in self._input_dirs if self._normalize(d) != norm
            ]
            try:
                self._save()
            except Exception:
                self._input_dirs = old_dirs
                raise

    def remove_output_dir(self, path: str) -> None:
        """从常用输出目录中移除指定路径"""
        if not isinstance(path, str) or not path.strip():
            return
        with self._lock:
            old_dirs = list(self._output_dirs)
            norm = self._normalize(path)
            self._output_dirs = [
                d for d in self._output_dirs if self._normalize(d) != norm
            ]
            try:
                self._save()
            except Exception:
                self._output_dirs = old_dirs
                raise

    def clear_input_dirs(self) -> None:
        """清空常用输入目录"""
        with self._lock:
            old_dirs = list(self._input_dirs)
            self._input_dirs = []
            try:
                self._save()
            except Exception:
                self._input_dirs = old_dirs
                raise

    def clear_output_dirs(self) -> None:
        """清空常用输出目录"""
        with self._lock:
            old_dirs = list(self._output_dirs)
            self._output_dirs = []
            try:
                self._save()
            except Exception:
                self._output_dirs = old_dirs
                raise
