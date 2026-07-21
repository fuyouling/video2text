"""输出文件校验器"""

import json
import re
from pathlib import Path
from typing import List

from src.i18n import t
from src.utils.exceptions import OutputError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OutputValidationError(OutputError):
    """输出校验错误"""

    def __init__(self, message: str, step: str = "", file_path: str = ""):
        self.step = step
        self.file_path = file_path
        super().__init__(message)


SRT_TIMESTAMP_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2},\d{1,3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{1,3})$"
)
VTT_TIMESTAMP_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{1,3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{1,3})(?:\s|$)"
)


def validate_output_file(
    file_path: str,
    *,
    min_size: int = 1,
    encoding: str = "utf-8",
) -> None:
    """校验输出文件是否成功生成且非空。

    Args:
        file_path: 文件路径
        min_size: 最小字节数（默认 0，即非空即可）
        encoding: 预期编码

    Raises:
        OutputValidationError: 文件不存在、为空或编码错误
    """
    path = Path(file_path)

    if not path.exists():
        raise OutputValidationError(
            t("output_validator.file.not_generated", path=file_path),
            step="output_file_check",
            file_path=file_path,
        )

    file_size = path.stat().st_size
    if file_size < min_size:
        raise OutputValidationError(
            t("output_validator.file.too_small", size=file_size, path=file_path),
            step="output_file_check",
            file_path=file_path,
        )

    try:
        with open(path, "r", encoding=encoding) as f:
            f.read(8192)
    except UnicodeDecodeError as e:
        raise OutputValidationError(
            t("output_validator.file.encoding_error", encoding=encoding, path=file_path, error=e),
            step="encoding_check",
            file_path=file_path,
        )


def validate_srt_content(content: str) -> List[str]:
    """校验 SRT 字幕内容的格式规范性。

    检查项：
    1. 序号从 1 开始连续递增
    2. 时间戳格式 HH:MM:SS,mmm --> HH:MM:SS,mmm
    3. start < end
    4. 每个块之间有空行分隔

    Args:
        content: SRT 文件内容

    Returns:
        校验通过的段数信息列表（空列表表示无内容）

    Raises:
        OutputValidationError: 格式不合规
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    if not content.strip():
        raise OutputValidationError(
            t("output_validator.srt.empty"),
            step="srt_content_check",
        )

    blocks = re.split(r"\n\s*\n", content.strip())
    errors: List[str] = []

    for block_idx, block in enumerate(blocks, 1):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            errors.append(t("output_validator.srt.block_lines", index=block_idx, expected=3, actual=len(lines)))
            continue

        expected_seq = str(block_idx)
        if lines[0].strip() != expected_seq:
            errors.append(t("output_validator.srt.block_seq", index=block_idx, expected=expected_seq, actual=lines[0].strip()))

        match = SRT_TIMESTAMP_RE.match(lines[1].strip())
        if not match:
            errors.append(t("output_validator.srt.block_timestamp", index=block_idx, timestamp=lines[1].strip()))
            continue

        start_str, end_str = match.group(1), match.group(2)
        try:
            start_sec = _parse_srt_timestamp(start_str)
            end_sec = _parse_srt_timestamp(end_str)
        except ValueError as e:
            errors.append(t("output_validator.srt.block_error", index=block_idx, error=e))
            continue

        if start_sec >= end_sec:
            errors.append(t("output_validator.srt.block_start_end", index=block_idx, start=start_str, end=end_str))

    if errors:
        raise OutputValidationError(
            t("output_validator.srt.check_failed", count=len(errors), details="\n".join(errors[:10])),
            step="srt_content_check",
        )

    return [b.strip() for b in blocks]


def validate_vtt_content(content: str) -> List[str]:
    """校验 VTT 字幕内容的格式规范性。

    Args:
        content: VTT 文件内容

    Returns:
        校验通过的 cue 列表

    Raises:
        OutputValidationError: 格式不合规
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    if not content.strip():
        raise OutputValidationError(t("output_validator.vtt.empty"), step="vtt_content_check")

    if not content.strip().startswith("WEBVTT"):
        raise OutputValidationError(t("output_validator.vtt.no_header"), step="vtt_content_check")

    cue_blocks = re.split(r"\n\s*\n", content.strip())
    cue_blocks = [
        b for b in cue_blocks if b.strip() and not b.strip().startswith("WEBVTT")
    ]

    errors: List[str] = []
    for idx, block in enumerate(cue_blocks, 1):
        lines = block.strip().split("\n")
        timestamp_line = None
        for line in lines:
            if "-->" in line:
                timestamp_line = line.strip()
                break

        if not timestamp_line:
            errors.append(t("output_validator.vtt.cue_no_timestamp", index=idx))
            continue

        match = VTT_TIMESTAMP_RE.match(timestamp_line)
        if not match:
            errors.append(t("output_validator.vtt.cue_timestamp_error", index=idx, timestamp=timestamp_line))
            continue

        try:
            start_sec = _parse_vtt_timestamp(match.group(1))
            end_sec = _parse_vtt_timestamp(match.group(2))
        except ValueError as e:
            errors.append(t("output_validator.vtt.cue_error", index=idx, error=e))
            continue
        if start_sec >= end_sec:
            errors.append(t("output_validator.vtt.cue_start_end", index=idx, start=match.group(1), end=match.group(2)))

    if errors:
        raise OutputValidationError(
            t("output_validator.vtt.check_failed", count=len(errors), details="\n".join(errors[:10])),
            step="vtt_content_check",
        )

    return cue_blocks


def validate_json_content(content: str) -> list:
    """校验 JSON 内容是否可解析且结构正确。

    Args:
        content: JSON 文件内容

    Returns:
        解析后的数据

    Raises:
        OutputValidationError: JSON 解析失败或结构错误
    """
    if not content.strip():
        raise OutputValidationError(t("output_validator.json.empty"), step="json_content_check")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise OutputValidationError(t("output_validator.json.parse_failed", error=e), step="json_content_check")

    if isinstance(data, list):
        sample_size = min(len(data), 10)
        for idx in range(sample_size):
            item = data[idx]
            if not isinstance(item, dict):
                raise OutputValidationError(
                    t("output_validator.json.not_object", index=idx),
                    step="json_content_check",
                )
            for field in ("start", "end", "text"):
                if field not in item:
                    raise OutputValidationError(
                        t("output_validator.json.missing_field", index=idx, field=field),
                        step="json_content_check",
                    )

    return data


def validate_transcript_segments(segments: list) -> List[str]:
    """校验转写段数据的完整性。

    Args:
        segments: TranscriptSegment 列表

    Returns:
        警告信息列表
    """
    warnings: List[str] = []

    for idx, seg in enumerate(segments):
        if hasattr(seg, "start") and hasattr(seg, "end"):
            if seg.start >= seg.end:
                warnings.append(t("output_validator.segment.start_ge_end", index=idx, start=seg.start, end=seg.end))
            if seg.start < 0:
                warnings.append(t("output_validator.segment.start_negative", index=idx, start=seg.start))

        if hasattr(seg, "text") and not seg.text.strip():
            warnings.append(t("output_validator.segment.text_empty", index=idx))

        if hasattr(seg, "confidence"):
            if not (0 <= seg.confidence <= 100):
                warnings.append(t("output_validator.segment.confidence_range", index=idx, confidence=seg.confidence))

    if warnings:
        logger.warning(t("output_validator.segment.warnings", count=len(warnings), details="\n".join(warnings[:10])))

    return warnings


def validate_output_content(file_path: str, fmt: str) -> None:
    """根据格式校验输出文件内容。

    Args:
        file_path: 文件路径
        fmt: 文件格式 (txt/srt/vtt/json)

    Raises:
        OutputValidationError: 校验失败
    """
    path = Path(file_path)
    if not path.exists():
        raise OutputValidationError(
            t("output_validator.content.file_not_found", path=file_path),
            step="content_check",
            file_path=file_path,
        )

    content = path.read_text(encoding="utf-8-sig")

    if fmt == "srt":
        validate_srt_content(content)
    elif fmt == "vtt":
        validate_vtt_content(content)
    elif fmt == "json":
        validate_json_content(content)
    elif fmt == "txt":
        if not content.strip():
            raise OutputValidationError(
                t("output_validator.content.txt_empty", path=file_path),
                step="content_check",
                file_path=file_path,
            )
    else:
        raise OutputValidationError(
            t("output_validator.content.unsupported_format", fmt=fmt),
            step="content_check",
            file_path=file_path,
        )


def _parse_srt_timestamp(ts: str) -> float:
    """解析 SRT 时间戳 HH:MM:SS,mmm 为秒数。"""
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    h, m, s, ms = int(h), int(m), int(s), int(ms)
    if h >= 100 or m >= 60 or s >= 60 or ms >= 1000:
        raise ValueError(t("output_validator.timestamp.out_of_range", timestamp=ts))
    return h * 3600 + m * 60 + s + ms / 1000


def _parse_vtt_timestamp(ts: str) -> float:
    """解析 VTT 时间戳 HH:MM:SS.mmm 为秒数。"""
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    h, m, s, ms = int(h), int(m), int(s), int(ms)
    if h >= 100 or m >= 60 or s >= 60 or ms >= 1000:
        raise ValueError(t("output_validator.timestamp.out_of_range", timestamp=ts))
    return h * 3600 + m * 60 + s + ms / 1000
