"""常用目录管理器 — 零 Qt 依赖，纯 Python 标准库

数据存储在 favorite_dirs.json 文件中（与 config.ini 同目录），格式：
{
    "input_dirs": ["path1", "path2"],
    "output_dirs": ["path1", "path2"]
}

第一次运行时文件不存在，目录列表为空。
"""

import json
import os
import tempfile
import threading
from pathlib import Path

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
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            self._input_dirs = list(data.get("input_dirs", []))
            self._output_dirs = list(data.get("output_dirs", []))
            logger.info("常用目录加载成功: %s", self._file_path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("常用目录加载失败: %s", exc)

    def _save(self) -> None:
        """原子写入 JSON 文件"""
        data = {
            "input_dirs": self._input_dirs,
            "output_dirs": self._output_dirs,
        }
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=self._file_path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                try:
                    os.replace(tmp_path, str(self._file_path))
                except OSError:
                    import shutil

                    shutil.move(tmp_path, str(self._file_path))
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.error("常用目录保存失败: %s", exc)

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
