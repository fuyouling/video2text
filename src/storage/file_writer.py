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

logger = get_logger(__name__)


class FileWriter:
    """文件写入器"""

    def __init__(self, output_dir: str):
        """初始化文件写入器

        Args:
            output_dir: 输出目录
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.formatter = OutputFormatter()

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

    SUPPORTED_SUMMARY_FORMATS = ("txt", "md")

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
        if fmt_clean not in self.SUPPORTED_SUMMARY_FORMATS:
            raise ValueError(
                t("storage.file_writer.unsupported_summary_format", fmt=fmt, formats=", ".join(self.SUPPORTED_SUMMARY_FORMATS))
            )
        output_path = self.output_dir / f"{filename}_summary.{fmt_clean}"
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
        """查找已存在的摘要文件（支持 txt/md）

        Args:
            filename: 文件名（不含 _summary 后缀）

        Returns:
            摘要文件路径，未找到返回 None
        """
        for fmt in self.SUPPORTED_SUMMARY_FORMATS:
            candidate = self.output_dir / f"{filename}_summary.{fmt}"
            if candidate.exists():
                return candidate
        return None

    SUPPORTED_TRANSCRIPT_FORMATS = ("txt", "srt", "vtt", "json")

    def find_transcript_file(self, video_name: str) -> Optional[Path]:
        """查找已存在的转写文件（支持 txt/srt/vtt/json）

        Args:
            video_name: 视频文件名（不含扩展名）

        Returns:
            转写文件路径，未找到返回 None
        """
        for ext in self.SUPPORTED_TRANSCRIPT_FORMATS:
            candidate = self.output_dir / f"{video_name}.{ext}"
            if candidate.exists():
                return candidate
        return None

    def write_json(self, data: dict, filename: str, validate: bool = True) -> str:
        """写入JSON文件

        Args:
            data: 数据字典
            filename: 文件名
            validate: 是否校验输出文件

        Returns:
            输出文件路径
        """
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

        output_path = self.output_dir / f"{filename}_keywords.txt"
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
