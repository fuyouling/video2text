"""文件写入器"""

import json
from pathlib import Path
from typing import List, Optional
from src.transcription.transcriber import TranscriptSegment
from src.text_processing.segment_merger import MergedSegment
from src.storage.output_formatter import OutputFormatter, OutputData
from src.utils.logger import get_logger

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

    def write_transcript(
        self,
        segments: List[TranscriptSegment],
        filename: str,
        format: str = "txt",
        include_timestamps: bool = True,
    ) -> str:
        """写入转写文本

        Args:
            segments: 转写段列表
            filename: 文件名
            format: 文件格式 (txt, srt, vtt)
            include_timestamps: 是否包含时间戳

        Returns:
            输出文件路径
        """
        output_path = self.output_dir / f"{filename}.{format}"

        if format == "txt":
            content = self.formatter.format_transcript(segments, include_timestamps)
        elif format == "srt":
            content = self.formatter.format_srt(segments)
        elif format == "vtt":
            content = self.formatter.format_vtt(segments)
        else:
            raise ValueError(f"不支持的格式: {format}")

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"转写文本写入成功: {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"写入转写文本失败: {e}")
            raise

    def write_merged_transcript(
        self,
        segments: List[MergedSegment],
        filename: str,
        include_timestamps: bool = True,
    ) -> str:
        """写入合并后的转写文本

        Args:
            segments: 合并后的段落列表
            filename: 文件名
            include_timestamps: 是否包含时间戳

        Returns:
            输出文件路径
        """
        output_path = self.output_dir / f"{filename}.txt"
        content = self.formatter.format_merged_transcript(segments, include_timestamps)

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"合并转写文本写入成功: {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"写入合并转写文本失败: {e}")
            raise

    def write_summary(self, summary: str, filename: str, title: str = "摘要") -> str:
        """写入摘要

        Args:
            summary: 摘要文本
            filename: 文件名
            title: 标题

        Returns:
            输出文件路径
        """
        output_path = self.output_dir / f"{filename}_summary.txt"
        content = self.formatter.format_summary(summary, title)

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"摘要写入成功: {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"写入摘要失败: {e}")
            raise

    def write_json(self, data: dict, filename: str) -> str:
        """写入JSON文件

        Args:
            data: 数据字典
            filename: 文件名

        Returns:
            输出文件路径
        """
        output_path = self.output_dir / f"{filename}.json"

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"JSON文件写入成功: {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"写入JSON文件失败: {e}")
            raise

    def write_output_data(self, output_data: OutputData, filename: str) -> str:
        """写入完整输出数据

        Args:
            output_data: 输出数据结构
            filename: 文件名

        Returns:
            输出文件路径
        """
        output_path = self.output_dir / f"{filename}_full.json"
        content = self.formatter.to_json(output_data)

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"完整输出数据写入成功: {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"写入完整输出数据失败: {e}")
            raise

    def write_text(self, text: str, filename: str) -> str:
        """写入纯文本

        Args:
            text: 文本内容
            filename: 文件名

        Returns:
            输出文件路径
        """
        output_path = self.output_dir / f"{filename}.txt"

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(text)

            logger.info(f"文本写入成功: {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"写入文本失败: {e}")
            raise

    def write_keywords(self, keywords: List[str], filename: str) -> str:
        """写入关键词

        Args:
            keywords: 关键词列表
            filename: 文件名

        Returns:
            输出文件路径
        """
        output_path = self.output_dir / f"{filename}_keywords.txt"
        content = "\n".join(keywords)

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"关键词写入成功: {output_path}")
            return str(output_path)
        except Exception as e:
            logger.error(f"写入关键词失败: {e}")
            raise

    def write_all_formats(
        self, segments: List[TranscriptSegment], summary: str, filename: str
    ) -> dict:
        """写入所有格式

        Args:
            segments: 转写段列表
            summary: 摘要
            filename: 文件名

        Returns:
            各格式文件路径字典
        """
        paths = {}

        try:
            paths["txt"] = self.write_transcript(segments, filename, "txt")
            paths["srt"] = self.write_transcript(segments, filename, "srt")
            paths["vtt"] = self.write_transcript(segments, filename, "vtt")
            paths["summary"] = self.write_summary(summary, filename)

            logger.info(f"所有格式写入成功: {filename}")
            return paths
        except Exception as e:
            logger.error(f"写入所有格式失败: {e}")
            raise
