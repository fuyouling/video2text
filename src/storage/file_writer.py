"""文件写入器"""

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from src.transcription.transcriber import TranscriptSegment
from src.text_processing.segment_merger import MergedSegment
from src.storage.output_formatter import OutputFormatter
from src.i18n import t
from src.utils.exceptions import TranscriptionError, OutputError
from src.utils.json_utils import atomic_write_json
from src.utils.logger import get_logger
from src.utils.output_validator import (
    validate_output_file,
    validate_output_content,
)
from src.utils.path_resolver import normalize_path_key

logger = get_logger(__name__)

# ── 集中管理的格式常量（避免各模块硬编码不一致） ──
TRANSCRIPT_FORMATS = ("txt", "srt", "vtt", "json")
SUMMARY_FORMATS = ("txt", "md")
KEYWORD_SUFFIX = "_keywords"
SUMMARY_SUFFIX = "_summary"
# 扫描时应跳过的派生文件后缀
SKIP_SUFFIXES = ("_summary.txt", "_summary.md", "_keywords.txt")
# 输出索引（manifest）所在子目录，以 '.' 开头，扫描时统一忽略
OUTPUT_INDEX_DIR = ".v2t"


class FileLocator:
    """只读文件定位器 —— 不实例化 FileWriter，避免创建输出目录的副作用。"""

    @staticmethod
    def find_transcript(output_dir: str, video_name: str) -> Optional[Path]:
        """查找已存在的转写文件（支持 txt/srt/vtt/json），未找到返回 None"""
        for ext in TRANSCRIPT_FORMATS:
            candidate = Path(output_dir) / f"{video_name}.{ext}"
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def find_summary(output_dir: str, video_name: str) -> Optional[Path]:
        """查找已存在的摘要文件（支持 txt/md），未找到返回 None"""
        for fmt in SUMMARY_FORMATS:
            candidate = Path(output_dir) / f"{video_name}{SUMMARY_SUFFIX}.{fmt}"
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def iter_output_files(output_dir: str):
        """递归遍历输出目录下的转写/摘要文件。

        跳过任何父级路径以 '.' 开头的目录（如 ``.checkpoint`` / ``.v2t``），
        避免把断点文件、输出索引等中间产物当成结果列出。
        """
        root = Path(output_dir)
        if not root.exists():
            return
        for ext in TRANSCRIPT_FORMATS:
            for p in root.rglob(f"*.{ext}"):
                if any(part.startswith(".") for part in p.relative_to(root).parts):
                    continue
                yield p
        for fmt in SUMMARY_FORMATS:
            for p in root.rglob(f"{SUMMARY_SUFFIX}.{fmt}"):
                if any(part.startswith(".") for part in p.relative_to(root).parts):
                    continue
                yield p


class FileWriter:
    """文件写入器"""

    # 保持向后兼容的类属性
    SUPPORTED_TRANSCRIPT_FORMATS = TRANSCRIPT_FORMATS
    SUPPORTED_SUMMARY_FORMATS = SUMMARY_FORMATS

    def __init__(self, output_dir: str):
        """初始化文件写入器

        Args:
            output_dir: 输出目录（仅在使用时才创建，构造期不 mkdir）
        """
        self.output_dir = Path(output_dir)
        self.formatter = OutputFormatter()
        self._ensured = False

    def _ensure_dir(self) -> None:
        """惰性创建输出目录（仅在真正写入时调用一次）。"""
        if not self._ensured:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._ensured = True

    @staticmethod
    def _atomic_write(file_path: Path, content: str, encoding: str = "utf-8") -> None:
        """原子写入文本文件，防止崩溃或磁盘满导致部分写入。"""
        fd, tmp_path = tempfile.mkstemp(dir=file_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
            try:
                os.replace(tmp_path, str(file_path))
            except OSError:
                import shutil

                shutil.move(tmp_path, str(file_path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def write_transcript(
        self,
        segments: List[TranscriptSegment],
        filename: str,
        fmt: str = "txt",
        include_timestamps: bool = True,
        validate: bool = True,
    ) -> str:
        """写入转写文本

        Args:
            segments: 转写段列表
            filename: 文件名
            fmt: 文件格式 (txt, srt, vtt, json)
            include_timestamps: 是否包含时间戳
            validate: 是否校验输出文件

        Returns:
            输出文件路径
        """
        self._ensure_dir()
        output_path = self.output_dir / f"{filename}.{fmt}"

        if not segments:
            raise TranscriptionError(
                t("storage.file_writer.empty_transcript", fmt=fmt.upper(), filename=filename)
            )

        if fmt == "txt":
            content = self.formatter.format_transcript(segments, include_timestamps)
        elif fmt == "srt":
            content = self.formatter.format_srt(segments)
        elif fmt == "vtt":
            content = self.formatter.format_vtt(segments)
        elif fmt == "json":
            content = json.dumps(
                [asdict(segment) for segment in segments], ensure_ascii=False, indent=2
            )
        else:
            raise ValueError(t("storage.file_writer.unsupported_format", fmt=fmt))

        if not content or not content.strip():
            raise TranscriptionError(
                t("storage.file_writer.empty_content", fmt=fmt.upper(), filename=filename)
            )

        try:
            self._atomic_write(output_path, content)

            if validate:
                validate_output_file(str(output_path))
                validate_output_content(str(output_path), fmt)

            logger.debug(t("storage.file_writer.tx_success"), output_path.name)
            return str(output_path)
        except Exception as e:
            logger.error(t("storage.file_writer.tx_fail"), output_path.name, e)
            raise

    def write_merged_transcript(
        self,
        segments: List[MergedSegment],
        filename: str,
        include_timestamps: bool = True,
        validate: bool = True,
    ) -> str:
        """写入合并后的转写文本

        Args:
            segments: 合并后的段落列表
            filename: 文件名
            include_timestamps: 是否包含时间戳
            validate: 是否校验输出文件

        Returns:
            输出文件路径
        """
        if not segments:
            raise OutputError(t("storage.file_writer.empty_merged", filename=filename))

        self._ensure_dir()
        output_path = self.output_dir / f"{filename}.txt"
        content = self.formatter.format_merged_transcript(segments, include_timestamps)

        try:
            self._atomic_write(output_path, content)

            if validate:
                validate_output_file(str(output_path))

            logger.debug(t("storage.file_writer.merge_success"), output_path.name)
            return str(output_path)
        except Exception as e:
            logger.error(t("storage.file_writer.merge_fail"), output_path.name, e)
            raise

    def write_summary(
        self,
        summary: str,
        filename: str,
        fmt: str = "txt",
        validate: bool = True,
    ) -> str:
        """写入摘要

        Args:
            summary: 摘要文本
            filename: 文件名
            fmt: 文件格式 (txt, md)
            validate: 是否校验输出文件

        Returns:
            输出文件路径
        """
        fmt_clean = fmt.lower().strip()
        if fmt_clean not in SUMMARY_FORMATS:
            raise ValueError(
                t("storage.file_writer.unsupported_summary_format", fmt=fmt, formats=", ".join(SUMMARY_FORMATS))
            )
        self._ensure_dir()
        output_path = self.output_dir / f"{filename}{SUMMARY_SUFFIX}.{fmt_clean}"
        content = self.formatter.format_summary(summary)

        try:
            self._atomic_write(output_path, content)

            if validate:
                validate_output_file(str(output_path))

            logger.debug(t("storage.file_writer.summary_success"), output_path.name)
            return str(output_path)
        except Exception as e:
            logger.error(t("storage.file_writer.summary_fail"), output_path.name, e)
            raise

    def find_summary_file(self, filename: str) -> Optional[Path]:
        """查找已存在的摘要文件（支持 txt/md）"""
        return FileLocator.find_summary(str(self.output_dir), filename)

    def find_transcript_file(self, video_name: str) -> Optional[Path]:
        """查找已存在的转写文件（支持 txt/srt/vtt/json）"""
        return FileLocator.find_transcript(str(self.output_dir), video_name)

    def write_json(self, data: dict, filename: str, validate: bool = True) -> str:
        """写入JSON文件

        Args:
            data: 数据字典
            filename: 文件名
            validate: 是否校验输出文件

        Returns:
            输出文件路径
        """
        self._ensure_dir()
        output_path = self.output_dir / f"{filename}.json"

        try:
            atomic_write_json(output_path, data)

            if validate:
                validate_output_file(str(output_path))
                validate_output_content(str(output_path), "json")

            logger.info(t("storage.file_writer.json_success"), output_path.name)
            return str(output_path)
        except Exception as e:
            logger.error(t("storage.file_writer.json_fail"), output_path.name, e)
            raise

    def write_text(self, text: str, filename: str, validate: bool = True) -> str:
        """写入纯文本

        Args:
            text: 文本内容
            filename: 文件名
            validate: 是否校验输出文件

        Returns:
            输出文件路径
        """
        self._ensure_dir()
        output_path = self.output_dir / f"{filename}.txt"

        try:
            self._atomic_write(output_path, text)

            if validate:
                validate_output_file(str(output_path))

            logger.info(t("storage.file_writer.text_success"), output_path.name)
            return str(output_path)
        except Exception as e:
            logger.error(t("storage.file_writer.text_fail"), output_path.name, e)
            raise

    def write_keywords(
        self, keywords: List[str], filename: str, validate: bool = True
    ) -> str:
        """写入关键词

        Args:
            keywords: 关键词列表
            filename: 文件名
            validate: 是否校验输出文件

        Returns:
            输出文件路径
        """
        if not keywords:
            raise OutputError(t("storage.file_writer.empty_keywords", filename=filename))

        self._ensure_dir()
        output_path = self.output_dir / f"{filename}{KEYWORD_SUFFIX}.txt"
        content = "\n".join(keywords)

        try:
            self._atomic_write(output_path, content)

            if validate:
                validate_output_file(str(output_path))

            logger.info(t("storage.file_writer.keywords_success"), output_path.name)
            return str(output_path)
        except Exception as e:
            logger.error(t("storage.file_writer.keywords_fail"), output_path.name, e)
            raise
