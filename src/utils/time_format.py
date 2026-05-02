"""时间格式化工具"""

import math

_MAX_SECONDS = 99 * 3600 + 59 * 60 + 59


def _clamp(seconds: float) -> float:
    """将秒数钳位到 [0, 99:59:59] 范围，处理 inf/NaN/负数。"""
    if not math.isfinite(seconds) or seconds < 0:
        return 0.0
    return min(seconds, _MAX_SECONDS)


def format_time_hms(seconds: float) -> str:
    """格式化秒数为 HH:MM:SS

    Args:
        seconds: 秒数

    Returns:
        格式化后的时间字符串 (HH:MM:SS)
    """
    seconds = _clamp(seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_time_srt(seconds: float) -> str:
    """格式化秒数为 SRT 时间格式 HH:MM:SS,mmm

    Args:
        seconds: 秒数

    Returns:
        格式化后的时间字符串 (HH:MM:SS,mmm)
    """
    seconds = _clamp(seconds)
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_time_vtt(seconds: float) -> str:
    """格式化秒数为 VTT 时间格式 HH:MM:SS.mmm

    Args:
        seconds: 秒数

    Returns:
        格式化后的时间字符串 (HH:MM:SS.mmm)
    """
    seconds = _clamp(seconds)
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
