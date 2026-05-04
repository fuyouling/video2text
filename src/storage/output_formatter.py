"""输出格式化器"""

from typing import List
from src.transcription.transcriber import TranscriptSegment
from src.text_processing.segment_merger import MergedSegment
from src.utils.logger import get_logger
from src.utils.time_format import format_time_hms, format_time_srt, format_time_vtt

logger = get_logger(__name__)


class OutputFormatter:
    """输出格式化器"""

    @staticmethod
    def format_transcript(
        segments: List[TranscriptSegment], include_timestamps: bool = True
    ) -> str:
        """格式化转写文本

        Args:
            segments: 转写段列表
            include_timestamps: 是否包含时间戳

        Returns:
            格式化后的文本
        """
        lines = []

        for segment in segments:
            if include_timestamps:
                timestamp = f"[{format_time_hms(segment.start)} - {format_time_hms(segment.end)}] "
                lines.append(f"{timestamp}{segment.text}")
            else:
                lines.append(segment.text)

        return "\n".join(lines)

    @staticmethod
    def format_merged_transcript(
        segments: List[MergedSegment], include_timestamps: bool = True
    ) -> str:
        """格式化合并后的转写文本

        Args:
            segments: 合并后的段落列表
            include_timestamps: 是否包含时间戳

        Returns:
            格式化后的文本
        """
        lines = []

        for segment in segments:
            if include_timestamps:
                timestamp = f"[{format_time_hms(segment.start)} - {format_time_hms(segment.end)}] "
                lines.append(f"{timestamp}{segment.text}")
            else:
                lines.append(segment.text)

        return "\n\n".join(lines)

    @staticmethod
    def format_summary(summary: str) -> str:
        """格式化摘要

        Args:
            summary: 摘要文本

        Returns:
            格式化后的摘要
        """
        return summary

    @staticmethod
    def format_srt(segments: List[TranscriptSegment]) -> str:
        """格式化为SRT字幕格式

        Args:
            segments: 转写段列表

        Returns:
            SRT格式文本
        """
        lines = []

        for i, segment in enumerate(segments, 1):
            start_time = format_time_srt(segment.start)
            end_time = format_time_srt(segment.end)

            lines.append(str(i))
            lines.append(f"{start_time} --> {end_time}")
            lines.append(segment.text)
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def format_vtt(segments: List[TranscriptSegment]) -> str:
        """格式化为VTT字幕格式

        Args:
            segments: 转写段列表

        Returns:
            VTT格式文本
        """
        lines = ["WEBVTT", ""]

        for segment in segments:
            start_time = format_time_vtt(segment.start)
            end_time = format_time_vtt(segment.end)

            lines.append(f"{start_time} --> {end_time}")
            lines.append(segment.text)
            lines.append("")

        return "\n".join(lines)
