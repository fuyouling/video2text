"""Video2Text - 音视频转文本工具"""

from src.utils.compat import apply_all_patches

# 应用第三方库运行时兼容性补丁（如 PyAV >= 19 与 faster-whisper 兼容）
apply_all_patches()
