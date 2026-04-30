"""时间格式化工具"""


def format_time_hms(seconds: float) -> str:
    """格式化秒数为 HH:MM:SS

    Args:
        seconds: 秒数

    Returns:
        格式化后的时间字符串 (HH:MM:SS)
    """
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
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_time_vtt(seconds: float) -> str:
    """格式化秒数为 VTT 时间格式 HH:MM:SS.mmm

    Args:
        seconds: 秒数

    Returns:
        格式化后的时间字符串 (HH:MM:SS.mmm)
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
