"""常用目录管理器 — 零 Qt 依赖，纯 Python 标准库

数据存储在 favorite_dirs.json 文件中（与 config.ini 同目录），格式：
{
    "input_dirs": ["path1", "path2"],
    "output_dirs": ["path1", "path2"]
}

第一次运行时文件不存在，目录列表为空。
"""

import os
import re
import threading
from pathlib import Path

from src.i18n import t
from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


_SELECTION_SUFFIX_PATTERN = re.compile(
    r"^(.*?)\s*[\(（]\s*(?:"
    r"(?:已选择|已選擇)\s*\d+\s*[个個]?文件"
    r"|\d+\s*(?:file\(s\)|files?|Datei\(en\)|archivo\(s\)|fichier\(s\)|ファイル|개\s*파일|файл(?:\(ов\))?)\s*(?:selected|ausgewählt|seleccionado\(s\)|sélectionné\(s\)|選択|선택됨|выбрано)?"
    r")\s*[\)）]\s*$",
    re.IGNORECASE,
)


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

    @staticmethod
    def clean_dir_path(path: str) -> str:
        """从路径中剥离形如 (1 file(s) selected)、(已选择 3 个文件) 等末尾附带信息，只保留纯路径。"""
        if not isinstance(path, str):
            return ""
        raw = path.strip()
        m = _SELECTION_SUFFIX_PATTERN.match(raw)
        if m:
            candidate = m.group(1).strip()
            if candidate:
                return candidate
        return path

    def _load(self) -> None:
        """从 JSON 文件加载常用目录列表，并自动清洗历史脏数据"""
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

        if not isinstance(output_dirs, list):
            logger.warning(t("config_manager.dm_output_dirs_format_error"))
            output_dirs = []

        dirty_detected = False
        cleaned_inputs: list[str] = []
        seen_inputs: set[str] = set()
        for d in input_dirs:
            if isinstance(d, str) and d.strip():
                clean_d = self.clean_dir_path(d)
                if clean_d != d:
                    dirty_detected = True
                norm = self._normalize(clean_d)
                if norm not in seen_inputs:
                    seen_inputs.add(norm)
                    cleaned_inputs.append(clean_d)
                else:
                    dirty_detected = True

        cleaned_outputs: list[str] = []
        seen_outputs: set[str] = set()
        for d in output_dirs:
            if isinstance(d, str) and d.strip():
                clean_d = self.clean_dir_path(d)
                if clean_d != d:
                    dirty_detected = True
                norm = self._normalize(clean_d)
                if norm not in seen_outputs:
                    seen_outputs.add(norm)
                    cleaned_outputs.append(clean_d)
                else:
                    dirty_detected = True

        self._input_dirs = list(cleaned_inputs[: self.MAX_ENTRIES])
        self._output_dirs = list(cleaned_outputs[: self.MAX_ENTRIES])

        if dirty_detected:
            try:
                self._save()
                logger.info(t("config_manager.dm_cleaned_saved", default="常用目录历史脏数据已自动清洗并更新"))
            except Exception as e:
                logger.warning(f"Failed to auto-save cleaned directories: {e}")

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
        clean_path = self.clean_dir_path(path)
        if not clean_path:
            return
        with self._lock:
            old_dirs = list(self._input_dirs)
            norm = self._normalize(clean_path)
            new_dirs = [
                d for d in self._input_dirs if self._normalize(d) != norm
            ]
            new_dirs.insert(0, clean_path)
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
        clean_path = self.clean_dir_path(path)
        if not clean_path:
            return
        with self._lock:
            old_dirs = list(self._output_dirs)
            norm = self._normalize(clean_path)
            new_dirs = [
                d for d in self._output_dirs if self._normalize(d) != norm
            ]
            new_dirs.insert(0, clean_path)
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
        clean_path = self.clean_dir_path(path)
        with self._lock:
            old_dirs = list(self._input_dirs)
            norm = self._normalize(clean_path)
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
        clean_path = self.clean_dir_path(path)
        with self._lock:
            old_dirs = list(self._output_dirs)
            norm = self._normalize(clean_path)
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
