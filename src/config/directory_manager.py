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

from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DirectoryManager:
    """常用目录管理器 — 零 Qt 依赖，纯 Python 标准库

    非单例，由调用方持有实例。线程安全（内部加锁）。
    原子写入，防止崩溃损坏 JSON 文件。
    """

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
            logger.warning("DirectoryManager: ✗ 加载失败 (%s)", self._file_path)
            return
        input_dirs = data.get("input_dirs", [])
        output_dirs = data.get("output_dirs", [])
        if not isinstance(input_dirs, list):
            logger.warning("DirectoryManager: ⚠ input_dirs 格式异常，忽略")
            input_dirs = []
        if not isinstance(output_dirs, list):
            logger.warning("DirectoryManager: ⚠ output_dirs 格式异常，忽略")
            output_dirs = []
        self._input_dirs = list(input_dirs)
        self._output_dirs = list(output_dirs)
        logger.info("DirectoryManager: ✓ 加载 (%s)", self._file_path)

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
            logger.error("DirectoryManager: ✗ 保存失败 (%s)", exc)
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
        with self._lock:
            norm = self._normalize(path)
            self._input_dirs = [
                d for d in self._input_dirs if self._normalize(d) != norm
            ]
            self._input_dirs.insert(0, path)
            self._save()

    def add_output_dir(self, path: str) -> None:
        """添加常用输出目录（去重，添加到列表头部）"""
        with self._lock:
            norm = self._normalize(path)
            self._output_dirs = [
                d for d in self._output_dirs if self._normalize(d) != norm
            ]
            self._output_dirs.insert(0, path)
            self._save()

    def remove_input_dir(self, path: str) -> None:
        """从常用输入目录中移除指定路径"""
        with self._lock:
            norm = self._normalize(path)
            self._input_dirs = [
                d for d in self._input_dirs if self._normalize(d) != norm
            ]
            self._save()

    def remove_output_dir(self, path: str) -> None:
        """从常用输出目录中移除指定路径"""
        with self._lock:
            norm = self._normalize(path)
            self._output_dirs = [
                d for d in self._output_dirs if self._normalize(d) != norm
            ]
            self._save()

    def clear_input_dirs(self) -> None:
        """清空常用输入目录"""
        with self._lock:
            self._input_dirs.clear()
            self._save()

    def clear_output_dirs(self) -> None:
        """清空常用输出目录"""
        with self._lock:
            self._output_dirs.clear()
            self._save()
