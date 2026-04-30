"""输出文件校验器"""

import json
import re
from pathlib import Path
from typing import List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

SRT_TIMESTAMP_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})$"
)
VTT_TIMESTAMP_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})$"
)


class OutputValidationError(Exception):
    """输出校验错误"""

    def __init__(self, message: str, step: str = "", file_path: str = ""):
        self.step = step
        self.file_path = file_path
        super().__init__(message)


def validate_output_file(
    file_path: str,
    *,
    min_size: int = 0,
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
            f"输出文件未生成: {file_path}",
            step="output_file_check",
            file_path=file_path,
        )

    file_size = path.stat().st_size
    if file_size <= min_size:
        raise OutputValidationError(
            f"输出文件为空或过小 ({file_size} bytes): {file_path}",
            step="output_file_check",
            file_path=file_path,
        )

    try:
        path.read_text(encoding=encoding)
    except UnicodeDecodeError as e:
        raise OutputValidationError(
            f"输出文件编码错误 (预期 {encoding}): {file_path} - {e}",
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
    if not content.strip():
        raise OutputValidationError(
            "SRT 内容为空",
            step="srt_content_check",
        )

    blocks = re.split(r"\n\s*\n", content.strip())
    errors: List[str] = []

    for block_idx, block in enumerate(blocks, 1):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            errors.append(f"块 {block_idx}: 行数不足 (期望 >=3, 实际 {len(lines)})")
            continue

        expected_seq = str(block_idx)
        if lines[0].strip() != expected_seq:
            errors.append(
                f"块 {block_idx}: 序号不连续 (期望 {expected_seq}, 实际 '{lines[0].strip()}')"
            )

        match = SRT_TIMESTAMP_RE.match(lines[1].strip())
        if not match:
            errors.append(f"块 {block_idx}: 时间戳格式错误 '{lines[1].strip()}'")
            continue

        start_str, end_str = match.group(1), match.group(2)
        start_sec = _parse_srt_timestamp(start_str)
        end_sec = _parse_srt_timestamp(end_str)

        if start_sec >= end_sec:
            errors.append(f"块 {block_idx}: start ({start_str}) >= end ({end_str})")

    if errors:
        raise OutputValidationError(
            f"SRT 格式校验失败 ({len(errors)} 处错误):\n" + "\n".join(errors[:10]),
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
    if not content.strip():
        raise OutputValidationError("VTT 内容为空", step="vtt_content_check")

    if not content.strip().startswith("WEBVTT"):
        raise OutputValidationError("VTT 文件缺少 WEBVTT 头", step="vtt_content_check")

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
            errors.append(f"Cue {idx}: 缺少时间戳行")
            continue

        match = VTT_TIMESTAMP_RE.match(timestamp_line)
        if not match:
            errors.append(f"Cue {idx}: 时间戳格式错误 '{timestamp_line}'")
            continue

        start_sec = _parse_vtt_timestamp(match.group(1))
        end_sec = _parse_vtt_timestamp(match.group(2))
        if start_sec >= end_sec:
            errors.append(
                f"Cue {idx}: start ({match.group(1)}) >= end ({match.group(2)})"
            )

    if errors:
        raise OutputValidationError(
            f"VTT 格式校验失败 ({len(errors)} 处错误):\n" + "\n".join(errors[:10]),
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
        raise OutputValidationError("JSON 内容为空", step="json_content_check")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise OutputValidationError(f"JSON 解析失败: {e}", step="json_content_check")

    if isinstance(data, list):
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                raise OutputValidationError(
                    f"JSON 数组元素 [{idx}] 不是对象",
                    step="json_content_check",
                )
            for field in ("start", "end", "text"):
                if field not in item:
                    raise OutputValidationError(
                        f"JSON 数组元素 [{idx}] 缺少字段 '{field}'",
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
                warnings.append(f"段 {idx}: start ({seg.start}) >= end ({seg.end})")
            if seg.start < 0:
                warnings.append(f"段 {idx}: start 为负值 ({seg.start})")

        if hasattr(seg, "text") and not seg.text.strip():
            warnings.append(f"段 {idx}: 文本为空")

        if hasattr(seg, "confidence"):
            if not (0 <= seg.confidence <= 100):
                warnings.append(f"段 {idx}: confidence 超出范围 ({seg.confidence})")

    if warnings:
        logger.warning(
            "转写段数据校验发现 %d 处问题:\n%s", len(warnings), "\n".join(warnings[:10])
        )

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
            f"文件不存在: {file_path}",
            step="content_check",
            file_path=file_path,
        )

    content = path.read_text(encoding="utf-8")

    if fmt == "srt":
        validate_srt_content(content)
    elif fmt == "vtt":
        validate_vtt_content(content)
    elif fmt == "json":
        validate_json_content(content)
    elif fmt == "txt":
        if not content.strip():
            raise OutputValidationError(
                f"TXT 文件内容为空: {file_path}",
                step="content_check",
                file_path=file_path,
            )


def _parse_srt_timestamp(ts: str) -> float:
    """解析 SRT 时间戳 HH:MM:SS,mmm 为秒数。"""
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _parse_vtt_timestamp(ts: str) -> float:
    """解析 VTT 时间戳 HH:MM:SS.mmm 为秒数。"""
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
