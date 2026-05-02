"""段落合并器"""

from typing import Callable, List, Optional
from dataclasses import dataclass
from src.transcription.transcriber import TranscriptSegment
from src.utils.logger import get_logger
from src.utils.time_format import format_time_hms

logger = get_logger(__name__)


@dataclass
class MergedSegment:
    """合并后的段落"""

    start: float
    end: float
    text: str
    language: str


ShouldMergeFn = Callable[[MergedSegment, TranscriptSegment], bool]


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

    @staticmethod
    def _merge(
        segments: List[TranscriptSegment],
        should_merge: ShouldMergeFn,
        label: str,
    ) -> List[MergedSegment]:
        """通用合并逻辑。

        Args:
            segments: 转写段列表
            should_merge: 判断当前段是否应与前一段合并的回调
            label: 日志标签

        Returns:
            合并后的段落列表
        """
        if not segments:
            return []

        logger.info("开始%s，原始段落数: %d", label, len(segments))

        merged: List[MergedSegment] = []
        current: Optional[MergedSegment] = None

        for segment in segments:
            if current is None:
                current = MergedSegment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    language=segment.language,
                )
            elif should_merge(current, segment):
                current.end = segment.end
                current.text += " " + segment.text
            else:
                merged.append(current)
                current = MergedSegment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    language=segment.language,
                )

        if current:
            merged.append(current)

        logger.info("%s完成，合并后段落数: %d", label, len(merged))
        return merged

    def merge_segments(self, segments: List[TranscriptSegment]) -> List[MergedSegment]:
        """合并段落

        合并策略：相邻段落时间间隔 <= max_gap 且语言相同 → 合并

        Args:
            segments: 转写段列表

        Returns:
            合并后的段落列表
        """

        def _should_merge(cur: MergedSegment, seg: TranscriptSegment) -> bool:
            gap = seg.start - cur.end
            return gap <= self.max_gap and seg.language == cur.language

        return self._merge(segments, _should_merge, "合并段落")

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

        def _should_merge(cur: MergedSegment, seg: TranscriptSegment) -> bool:
            return len(cur.text) < target_length and seg.language == cur.language

        return self._merge(
            segments, _should_merge, f"按长度合并段落(目标{target_length})"
        )

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

        def _should_merge(cur: MergedSegment, seg: TranscriptSegment) -> bool:
            return seg.start - cur.start < interval and seg.language == cur.language

        return self._merge(segments, _should_merge, f"按时间合并段落({interval}s)")

    def filter_short_segments(
        self, segments: List[MergedSegment], min_length: Optional[int] = None
    ) -> List[MergedSegment]:
        """过滤短段落

        Args:
            segments: 合并后的段落列表
            min_length: 最小长度，默认使用构造函数的 self.min_length

        Returns:
            过滤后的段落列表
        """
        threshold = min_length if min_length is not None else self.min_length
        filtered = [seg for seg in segments if len(seg.text.strip()) >= threshold]

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
                timestamp = f"[{format_time_hms(segment.start)} - {format_time_hms(segment.end)}] "
                lines.append(f"{timestamp}{segment.text}")
            else:
                lines.append(segment.text)

        return "\n\n".join(lines)
