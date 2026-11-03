"""兼容性补丁模块。

统一处理第三方依赖库版本冲突与历史接口兼容。
"""

import functools
import logging

logger = logging.getLogger("video2text")


def patch_av_open() -> None:
    """兼容 PyAV >= 19.0.0 与 faster-whisper 的参数不匹配问题。

    PyAV 19+ 移除了 av.open() 的 metadata_errors 和 metadata_encoding 参数，
    而 faster-whisper (截至 1.2.1) 仍会在 decode_audio 中传入 metadata_errors='ignore'。
    此补丁自动剥离这两个已被移除的参数，使两者能在各种版本下安全共存。
    """
    try:
        import av

        if hasattr(av, "open") and not getattr(av.open, "_v2t_compat_patched", False):
            orig_open = av.open

            @functools.wraps(orig_open)
            def _safe_av_open(*args, **kwargs):
                kwargs.pop("metadata_errors", None)
                kwargs.pop("metadata_encoding", None)
                return orig_open(*args, **kwargs)

            _safe_av_open._v2t_compat_patched = True
            av.open = _safe_av_open
            logger.debug("已应用 PyAV av.open() 兼容性补丁")
    except ImportError:
        pass


def apply_all_patches() -> None:
    """应用所有运行时兼容性补丁。"""
    patch_av_open()
