"""段落合并器"""

from typing import List
from dataclasses import dataclass
from src.transcription.transcriber import TranscriptSegment
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MergedSegment:
    """合并后的段落"""

    start: float
    end: float
    text: str
    language: str


class SegmentMerger:
    """段落合并器"""

    def __init__(self, max_gap: float = 2.0, min_length: int = 50):
        """初始化段落合并器

        Args:
            max_gap: 最大时间间隔（秒），超过此间隔不合并
            min_length: 最小文本长度，短于此长度尝试合并
        """
        self.max_gap = max_gap
        self.min_length = min_length

    def merge_segments(self, segments: List[TranscriptSegment]) -> List[MergedSegment]:
        """合并段落

        Args:
            segments: 转写段列表

        Returns:
            合并后的段落列表
        """
        if not segments:
            return []

        logger.info(f"开始合并段落，原始段落数: {len(segments)}")

        merged = []
        current_segment = None

        for segment in segments:
            if current_segment is None:
                current_segment = MergedSegment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    language=segment.language,
                )
            else:
                gap = segment.start - current_segment.end

                if gap <= self.max_gap and segment.language == current_segment.language:
                    current_segment.end = segment.end
                    current_segment.text += " " + segment.text
                else:
                    merged.append(current_segment)
                    current_segment = MergedSegment(
                        start=segment.start,
                        end=segment.end,
                        text=segment.text,
                        language=segment.language,
                    )

        if current_segment:
            merged.append(current_segment)

        logger.info(f"段落合并完成，合并后段落数: {len(merged)}")
        return merged

    def merge_by_length(
        self, segments: List[TranscriptSegment], target_length: int = 200
    ) -> List[MergedSegment]:
        """按长度合并段落

        Args:
            segments: 转写段列表
            target_length: 目标长度

        Returns:
            合并后的段落列表
        """
        if not segments:
            return []

        logger.info(f"开始按长度合并段落，目标长度: {target_length}")

        merged = []
        current_segment = None

        for segment in segments:
            if current_segment is None:
                current_segment = MergedSegment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    language=segment.language,
                )
            else:
                if (
                    len(current_segment.text) < target_length
                    and segment.language == current_segment.language
                ):
                    current_segment.end = segment.end
                    current_segment.text += " " + segment.text
                else:
                    merged.append(current_segment)
                    current_segment = MergedSegment(
                        start=segment.start,
                        end=segment.end,
                        text=segment.text,
                        language=segment.language,
                    )

        if current_segment:
            merged.append(current_segment)

        logger.info(f"按长度合并完成，合并后段落数: {len(merged)}")
        return merged

    def merge_by_time(
        self, segments: List[TranscriptSegment], interval: float = 30.0
    ) -> List[MergedSegment]:
        """按时间间隔合并段落

        Args:
            segments: 转写段列表
            interval: 时间间隔（秒）

        Returns:
            合并后的段落列表
        """
        if not segments:
            return []

        logger.info(f"开始按时间合并段落，时间间隔: {interval}秒")

        merged = []
        current_segment = None
        current_interval_start = segments[0].start

        for segment in segments:
            if current_segment is None:
                current_segment = MergedSegment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    language=segment.language,
                )
            else:
                if segment.start - current_interval_start < interval:
                    current_segment.end = segment.end
                    current_segment.text += " " + segment.text
                else:
                    merged.append(current_segment)
                    current_interval_start = segment.start
                    current_segment = MergedSegment(
                        start=segment.start,
                        end=segment.end,
                        text=segment.text,
                        language=segment.language,
                    )

        if current_segment:
            merged.append(current_segment)

        logger.info(f"按时间合并完成，合并后段落数: {len(merged)}")
        return merged

    def filter_short_segments(
        self, segments: List[MergedSegment], min_length: int = 10
    ) -> List[MergedSegment]:
        """过滤短段落

        Args:
            segments: 合并后的段落列表
            min_length: 最小长度

        Returns:
            过滤后的段落列表
        """
        filtered = [seg for seg in segments if len(seg.text.strip()) >= min_length]

        logger.info(f"过滤短段落，保留: {len(filtered)}/{len(segments)}")
        return filtered

    def format_segments_as_text(
        self, segments: List[MergedSegment], include_timestamps: bool = False
    ) -> str:
        """格式化段落为文本

        Args:
            segments: 合并后的段落列表
            include_timestamps: 是否包含时间戳

        Returns:
            格式化后的文本
        """
        lines = []

        for segment in segments:
            if include_timestamps:
                timestamp = f"[{self._format_time(segment.start)} - {self._format_time(segment.end)}] "
                lines.append(f"{timestamp}{segment.text}")
            else:
                lines.append(segment.text)

        return "\n\n".join(lines)

    def _format_time(self, seconds: float) -> str:
        """格式化时间

        Args:
            seconds: 秒数

        Returns:
            格式化后的时间字符串 (HH:MM:SS)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
