"""书签数据管理 — 零 Qt 依赖，纯 Python 标准库"""

import threading
from pathlib import Path

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
        self.video_name = video_name
        self.content_type = content_type  # 'transcript' or 'summary'
        self.position = position
        self.text = text[:100]
        self.file_path = file_path
        self.relative_path = relative_path
        self.created_at = created_at
        self.note = note

    def to_dict(self) -> dict:
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
    """书签持久化管理器（非单例，不持有内存状态）"""

    def __init__(self, file_path: Path):
        self._file_path = file_path
        self._lock = threading.Lock()

    def load(self) -> list[BookmarkItem]:
        if not self._file_path.exists():
            return []
        data = safe_read_json(self._file_path)
        if data is None:
            logger.warning("读取书签文件失败: %s", self._file_path)
            return []
        items = data.get("bookmarks", [])
        return [BookmarkItem.from_dict(d) for d in items]

    def save(self, bookmarks: list[BookmarkItem]) -> None:
        data = {"bookmarks": [b.to_dict() for b in bookmarks]}
        try:
            atomic_write_json(self._file_path, data)
        except OSError as exc:
            logger.error("写入书签文件失败: %s", exc)

    def add(self, bookmark: BookmarkItem) -> None:
        with self._lock:
            bookmarks = self.load()
            bookmarks.append(bookmark)
            self.save(bookmarks)

    def remove(self, indices: list[int]) -> None:
        with self._lock:
            bookmarks = self.load()
            for idx in sorted(indices, reverse=True):
                if 0 <= idx < len(bookmarks):
                    del bookmarks[idx]
            self.save(bookmarks)

    def clear(self) -> None:
        with self._lock:
            self.save([])

    def get_all(self) -> list[BookmarkItem]:
        with self._lock:
            return self.load()
