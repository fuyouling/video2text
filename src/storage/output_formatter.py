"""输出格式化器"""

from typing import List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, asdict
from src.transcription.transcriber import TranscriptSegment
from src.text_processing.segment_merger import MergedSegment
from src.utils.logger import get_logger
from src.utils.time_format import format_time_hms, format_time_srt, format_time_vtt

logger = get_logger(__name__)


@dataclass
class OutputData:
    """输出数据结构"""

    video_name: str
    video_path: str
    duration: float
    transcript: List[Dict[str, Any]]
    processed_text: str
    summary: str
    timestamp: str
    processing_time: float


class OutputFormatter:
    """输出格式化器"""

    def __init__(self):
        """初始化输出格式化器"""
        pass

    def format_transcript(
        self, segments: List[TranscriptSegment], include_timestamps: bool = True
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

    def format_merged_transcript(
        self, segments: List[MergedSegment], include_timestamps: bool = True
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

    def format_summary(self, summary: str, title: str = "摘要") -> str:
        """格式化摘要

        Args:
            summary: 摘要文本
            title: 标题（保留参数以保持兼容性，但不再使用）

        Returns:
            格式化后的摘要（直接返回摘要内容）
        """
        return summary

    def format_srt(self, segments: List[TranscriptSegment]) -> str:
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

    def format_vtt(self, segments: List[TranscriptSegment]) -> str:
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

    def create_output_data(
        self,
        video_name: str,
        video_path: str,
        duration: float,
        transcript_segments: List[TranscriptSegment],
        processed_text: str,
        summary: str,
        processing_time: float,
    ) -> OutputData:
        """创建输出数据结构

        Args:
            video_name: 视频名称
            video_path: 视频路径
            duration: 时长
            transcript_segments: 转写段列表
            processed_text: 处理后的文本
            summary: 摘要
            processing_time: 处理时间

        Returns:
            输出数据结构
        """
        transcript_data = [
            {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "confidence": seg.confidence,
                "language": seg.language,
            }
            for seg in transcript_segments
        ]

        return OutputData(
            video_name=video_name,
            video_path=video_path,
            duration=duration,
            transcript=transcript_data,
            processed_text=processed_text,
            summary=summary,
            timestamp=datetime.now().isoformat(),
            processing_time=processing_time,
        )

    def to_json(self, output_data: OutputData) -> str:
        """转换为JSON格式

        Args:
            output_data: 输出数据结构

        Returns:
            JSON字符串
        """
        import json

        return json.dumps(asdict(output_data), ensure_ascii=False, indent=2)
