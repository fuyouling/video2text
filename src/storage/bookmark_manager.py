"""书签数据管理 — 零 Qt 依赖，纯 Python 标准库"""

import threading
from pathlib import Path
from typing import Optional

from src.i18n import t
from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BookmarkItem:
    """书签数据项（纯数据类，不依赖 Qt）"""

    def __init__(
        self,
        video_name: str,
        content_type: str,
        position: int,
        text: str,
        file_path: str = "",
        relative_path: str = "",
        created_at: str = "",
        note: str = "",
    ):
        """初始化书签项。

        Args:
            video_name: 视频名称
            content_type: 内容类型（'transcript' 或 'summary'）
            position: 文本位置（字符偏移）
            text: 书签处的文本片段（自动截取前 100 字符）
            file_path: 完整文件路径
            relative_path: 相对路径
            created_at: 创建时间
            note: 用户备注
        """
        self.video_name = video_name
        self.content_type = content_type  # 'transcript' or 'summary'
        self.position = position
        self.text = text[:100]
        self.file_path = file_path
        self.relative_path = relative_path
        self.created_at = created_at
        self.note = note

    def to_dict(self) -> dict:
        """将书签项序列化为字典。"""
        return {
            "video_name": self.video_name,
            "content_type": self.content_type,
            "position": self.position,
            "text": self.text,
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "created_at": self.created_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BookmarkItem":
        """从字典反序列化为书签项。"""
        return cls(
            video_name=data.get("video_name", ""),
            content_type=data.get("content_type", "transcript"),
            position=data.get("position", 0),
            text=data.get("text", ""),
            file_path=data.get("file_path", ""),
            relative_path=data.get("relative_path", ""),
            created_at=data.get("created_at", ""),
            note=data.get("note", ""),
        )


class BookmarkManager:
    """书签持久化管理器 —— 使用 JSON 文件存储，线程安全，原子写入。"""

    _global_lock = threading.RLock()

    def __init__(self, file_path: Path):
        """初始化书签管理器。

        Args:
            file_path: 书签 JSON 文件路径
        """
        self._file_path = Path(file_path)
        self._lock = BookmarkManager._global_lock
        self._cache: Optional[list[BookmarkItem]] = None
        self._last_mtime: float = -1.0

    def _get_disk_mtime(self) -> float:
        try:
            return self._file_path.stat().st_mtime
        except OSError:
            return 0.0

    def load(self) -> list[BookmarkItem]:
        """从 JSON 文件加载全部书签列表（读前校验磁盘最新状态）。"""
        with self._lock:
            if not self._file_path.exists():
                self._cache = []
                self._last_mtime = 0.0
                return []

            disk_mtime = self._get_disk_mtime()
            if self._cache is not None and disk_mtime == self._last_mtime:
                return list(self._cache)

            data = safe_read_json(self._file_path)
            if data is None:
                logger.warning(t("storage.log.load_fail"), self._file_path.name)
                return []
            items = data.get("bookmarks", [])
            self._cache = [BookmarkItem.from_dict(d) for d in items]
            self._last_mtime = disk_mtime
            return list(self._cache)

    def save(self, bookmarks: list[BookmarkItem]) -> None:
        """将书签列表原子写入 JSON 文件（写前确保父目录存在并更新磁盘状态）。"""
        with self._lock:
            try:
                self._file_path.parent.mkdir(parents=True, exist_ok=True)
                data = {"bookmarks": [b.to_dict() for b in bookmarks]}
                atomic_write_json(self._file_path, data)
                self._cache = list(bookmarks)
                self._last_mtime = self._get_disk_mtime()
            except OSError as exc:
                logger.error(t("storage.log.save_fail"), exc)

    def add(self, bookmark: BookmarkItem) -> None:
        """添加一个书签并持久化。"""
        with self._lock:
            bookmarks = self.load()
            bookmarks.append(bookmark)
            self.save(bookmarks)

    def remove(self, indices: list[int]) -> None:
        """按索引列表移除书签（从大到小删除避免偏移）。"""
        with self._lock:
            bookmarks = self.load()
            for idx in sorted(indices, reverse=True):
                if 0 <= idx < len(bookmarks):
                    del bookmarks[idx]
            self.save(bookmarks)

    def clear(self) -> None:
        """清空所有书签。"""
        with self._lock:
            self.save([])

    def get_all(self) -> list[BookmarkItem]:
        """获取全部书签列表。"""
        with self._lock:
            return self.load()
