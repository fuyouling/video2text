"""输出索引（manifest + 兜底扫描）

每次任务完成后把真实结果路径写入 ``<output_dir>/.v2t/index.json``，
之后"加载历史 / 结果查看 / 增量模式 / 书签定位"都优先读索引，
缺失（旧版本未生成索引）时回退到目录扫描。

扫描与索引都会：
- 跳过以 '.' 开头的目录（``.checkpoint`` / ``.v2t`` 等中间产物）
- 按文件真实父目录记录 name -> dir，避免镜像子目录里的文件读不到
"""

import threading
from pathlib import Path
from typing import Dict, List, Optional

from src.storage.file_writer import (
    SKIP_SUFFIXES,
    SUMMARY_FORMATS,
    SUMMARY_SUFFIX,
    TRANSCRIPT_FORMATS,
)
from src.utils.json_utils import atomic_write_json, safe_read_json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OutputIndex:
    """单个输出目录的结果索引。"""

    def __init__(self, output_dir: str):
        self.output_dir = str(Path(output_dir).resolve())
        self._lock = threading.Lock()
        self._cache: Optional[Dict[str, dict]] = None

    # ── manifest 读写 ──

    def _manifest_path(self) -> Path:
        from src.storage.file_writer import OUTPUT_INDEX_DIR

        return Path(self.output_dir) / OUTPUT_INDEX_DIR / "index.json"

    def _ensure_cache(self) -> Dict[str, dict]:
        if self._cache is None:
            self._cache = self.load_manifest()
        return self._cache

    def load_manifest(self) -> Dict[str, dict]:
        """读取 manifest，返回 name -> info 字典。文件不存在/损坏返回空字典。"""
        data = safe_read_json(self._manifest_path())
        if isinstance(data, dict):
            entries = data.get("entries", {})
            if isinstance(entries, dict):
                return entries
        return {}

    def save_manifest(self, entries: Dict[str, dict]) -> None:
        path = self._manifest_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, {"entries": entries})
        except OSError as exc:
            logger.warning("保存输出索引失败: %s", exc)

    def record(
        self,
        video_name: str,
        transcript_paths: Optional[List[str]] = None,
        summary_path: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> None:
        """记录（或更新）一个视频的结果路径。线程安全，带内存缓存。"""
        with self._lock:
            entries = self._ensure_cache()
            info = dict(entries.get(video_name, {}))
            if transcript_paths:
                info["transcripts"] = [str(p) for p in transcript_paths]
            elif "transcripts" not in info:
                info["transcripts"] = []
            if summary_path:
                info["summary"] = str(summary_path)
            if source_path:
                info["source"] = str(source_path)
            if info.get("transcripts") or info.get("summary"):
                entries[video_name] = info
                self.save_manifest(entries)
                self._cache = entries

    # ── 兜底扫描 ──

    def scan(self) -> Dict[str, dict]:
        """目录扫描兜底：返回 name -> {dir, transcripts[], summary}。

        跳过以 '.' 开头的目录（中间产物），按真实父目录记录 dir。
        """
        root = Path(self.output_dir)
        result: Dict[str, dict] = {}

        def _add(name: str, parent: Path, kind: str, path: Path) -> None:
            e = result.setdefault(
                name, {"dir": str(parent), "transcripts": [], "summary": None}
            )
            if kind == "transcript":
                sp = str(path)
                if sp not in e["transcripts"]:
                    e["transcripts"].append(sp)
            else:
                e["summary"] = str(path)

        if root.exists():
            for ext in TRANSCRIPT_FORMATS:
                for p in root.rglob(f"*.{ext}"):
                    if any(part.startswith(".") for part in p.relative_to(root).parts):
                        continue
                    if p.name.endswith(SKIP_SUFFIXES):
                        continue
                    _add(p.stem, p.parent, "transcript", p)
            for fmt in SUMMARY_FORMATS:
                for p in root.rglob(f"{SUMMARY_SUFFIX}.{fmt}"):
                    if any(part.startswith(".") for part in p.relative_to(root).parts):
                        continue
                    name = p.stem[: -len(SUMMARY_SUFFIX)]
                    if not name:
                        continue
                    _add(name, p.parent, "summary", p)

        return result

    def build_name_map(self) -> Dict[str, str]:
        """构建 name -> 实际输出目录 的映射（优先索引，回退扫描）。"""
        entries = self.load_manifest()
        if entries:
            return {
                name: info.get("dir", self.output_dir)
                for name, info in entries.items()
                if info.get("transcripts") or info.get("summary")
            }
        return {name: info["dir"] for name, info in self.scan().items()}
