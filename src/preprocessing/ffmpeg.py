"""FFmpeg 全局管理 —— 路径解析 + 可用性检查，相同路径仅执行一次

用法：
    from src.preprocessing.ffmpeg import ensure_ffmpeg
    ffmpeg_path = ensure_ffmpeg("ffmpeg")          # 首次调用：解析 + 检查 + 日志
    ffmpeg_path = ensure_ffmpeg("ffmpeg")          # 后续调用：直接返回缓存结果
    ffmpeg_path = ensure_ffmpeg("/other/ffmpeg")   # 不同路径：重新解析 + 检查
"""

import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict

from src.utils.exceptions import VideoFileError
from src.utils.logger import get_logger

logger = get_logger(__name__)

if sys.platform == "win32":
    _CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    _CREATE_NO_WINDOW = 0

_cache: Dict[str, str] = {}
_ffprobe_cache: Dict[str, str] = {}
_lock = threading.Lock()


def ensure_ffmpeg(ffmpeg_path: str = "ffmpeg") -> str:
    """确保 FFmpeg 可用，返回解析后的绝对路径。

    相同的 ffmpeg_path 参数仅执行一次实际的版本检查，后续调用直接返回缓存路径。
    传入不同的 ffmpeg_path 参数会重新解析和检查。

    Args:
        ffmpeg_path: FFmpeg 可执行文件路径或名称

    Returns:
        解析后的 FFmpeg 绝对路径

    Raises:
        VideoFileError: FFmpeg 未找到或不可用
    """
    with _lock:
        if ffmpeg_path in _cache:
            return _cache[ffmpeg_path]

    resolved = shutil.which(ffmpeg_path)
    if not resolved:
        resolved = shutil.which("ffmpeg")
    if not resolved:
        common_paths = [
            Path.home() / "ffmpeg" / "bin" / "ffmpeg.exe",
            Path("C:/") / "ffmpeg" / "bin" / "ffmpeg.exe",
        ]
        for p in common_paths:
            if p.exists():
                resolved = str(p)
                break
    if not resolved:
        raise VideoFileError(
            "FFmpeg未找到。请安装FFmpeg并添加到系统PATH环境变量，"
            "或在config.ini的[preprocessing]节中设置ffmpeg_path为FFmpeg的完整路径。"
        )

    with _lock:
        if resolved in _cache:
            _cache[ffmpeg_path] = _cache[resolved]
            return _cache[ffmpeg_path]

    try:
        result = subprocess.run(
            [resolved, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
            encoding="utf-8",
            errors="ignore",
        )
        if result.returncode != 0:
            raise VideoFileError("FFmpeg不可用")
        logger.info("FFmpeg检查通过: %s", resolved)
    except subprocess.TimeoutExpired:
        raise VideoFileError("FFmpeg检查超时")

    with _lock:
        _cache[ffmpeg_path] = resolved
        _cache[resolved] = resolved
    return resolved


def ensure_ffprobe(ffmpeg_path: str = "ffmpeg") -> str:
    """确保 ffprobe 可用，返回解析后的绝对路径。

    从 ffmpeg 路径推导 ffprobe 路径（同一目录），或在 PATH 中查找。
    相同的 ffmpeg_path 参数仅执行一次实际的版本检查，后续调用直接返回缓存路径。

    Args:
        ffmpeg_path: FFmpeg 可执行文件路径或名称（用于推导 ffprobe 位置）

    Returns:
        解析后的 ffprobe 绝对路径

    Raises:
        VideoFileError: ffprobe 未找到或不可用
    """
    with _lock:
        if ffmpeg_path in _ffprobe_cache:
            return _ffprobe_cache[ffmpeg_path]

    resolved_ffmpeg = ensure_ffmpeg(ffmpeg_path)

    ffmpeg_p = Path(resolved_ffmpeg)
    ffprobe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    ffprobe_candidate = ffmpeg_p.parent / ffprobe_name

    resolved = None
    if ffprobe_candidate.exists():
        resolved = str(ffprobe_candidate)
    else:
        resolved = shutil.which("ffprobe")

    if not resolved:
        raise VideoFileError(
            "ffprobe 未找到。ffprobe 通常与 ffmpeg 位于同一目录。"
            "请确保 ffprobe 与 ffmpeg 在同一目录下。"
        )

    with _lock:
        if resolved in _ffprobe_cache:
            _ffprobe_cache[ffmpeg_path] = _ffprobe_cache[resolved]
            return _ffprobe_cache[ffmpeg_path]

    try:
        result = subprocess.run(
            [resolved, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
            encoding="utf-8",
            errors="ignore",
        )
        if result.returncode != 0:
            raise VideoFileError("ffprobe 不可用")
        logger.info("ffprobe 检查通过: %s", resolved)
    except subprocess.TimeoutExpired:
        raise VideoFileError("ffprobe 检查超时")

    with _lock:
        _ffprobe_cache[ffmpeg_path] = resolved
        _ffprobe_cache[resolved] = resolved
    return resolved
