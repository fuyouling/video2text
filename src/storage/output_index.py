"""输出索引（manifest + 兜底扫描）

每次任务完成后把真实结果路径写入 ``<output_dir>/.v2t/index.json``，
之后"加载历史 / 结果查看 / 增量模式 / 书签定位"都优先读索引，
缺失（旧版本未生成索引）时回退到目录扫描。

扫描与索引都会：
- 跳过以 '.' 开头的目录（``.checkpoint`` / ``.v2t`` 等中间产物）
- 按文件真实父目录记录 name -> dir，避免镜像子目录里的文件读不到
"""

import os
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
from src.i18n import t

logger = get_logger(__name__)


class OutputIndex:
    """单个输出目录的结果索引。"""

    _global_lock = threading.Lock()

    def __init__(self, output_dir: str):
        self.output_dir = str(Path(output_dir).resolve())
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
            logger.warning(t("storage.output_index.save_failed", error=exc))

    def record(
        self,
        video_name: str,
        transcript_paths: Optional[List[str]] = None,
        summary_path: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> None:
        """记录（或更新）一个视频的结果路径。线程安全，带内存缓存。"""
        with OutputIndex._global_lock:
            entries = self.load_manifest()
            info = dict(entries.get(video_name, {}))
            if transcript_paths:
                info["transcripts"] = [str(p) for p in transcript_paths]
                info["dir"] = str(Path(transcript_paths[0]).parent)
            elif "transcripts" not in info:
                info["transcripts"] = []
            if summary_path:
                info["summary"] = str(summary_path)
                if "dir" not in info:
                    info["dir"] = str(Path(summary_path).parent)
            if source_path:
                info["source"] = str(source_path)
            if "dir" not in info:
                info["dir"] = self.output_dir
            if info.get("transcripts") or info.get("summary"):
                entries[video_name] = info
                self.save_manifest(entries)
                self._cache = entries

    # ── 兜底扫描 ──

    def scan(self) -> Dict[str, dict]:
        """目录扫描兜底：返回 name -> {dir, transcripts[], summary}。

        跳过以 '.' 开头的目录（中间产物），按真实父目录记录 dir。
        单次 os.walk(topdown=True) 扫描并在顶层对 '.' 开头的隐藏目录进行剪枝。
        """
        root = Path(self.output_dir)
        result: Dict[str, dict] = {}

        if not root.exists():
            return result

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

        for dirpath, dirnames, filenames in os.walk(str(root), topdown=True):
            # 顶层对 '.' 开头的隐藏目录剪枝（例如 .checkpoint / .v2t），彻底杜绝全盘深度扫描与中间产物干扰
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            parent_path = Path(dirpath)

            for fname in filenames:
                if fname.startswith("."):
                    continue

                # 1. 优先匹配摘要文件 (例如 video_summary.txt, video_summary.md)
                is_summary = False
                for fmt in SUMMARY_FORMATS:
                    suffix = f"{SUMMARY_SUFFIX}.{fmt}"
                    if fname.endswith(suffix):
                        name = fname[:-len(suffix)]
                        if name:
                            _add(name, parent_path, "summary", parent_path / fname)
                            is_summary = True
                            break
                if is_summary:
                    continue

                # 2. 检查并过滤派生文件 (如 _keywords.txt)
                if fname.endswith(SKIP_SUFFIXES):
                    continue

                # 3. 匹配转写文件 (txt, srt, vtt, json)
                for ext in TRANSCRIPT_FORMATS:
                    suffix = f".{ext}"
                    if fname.endswith(suffix):
                        stem = fname[:-len(suffix)]
                        if stem:
                            _add(stem, parent_path, "transcript", parent_path / fname)
                        break

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
