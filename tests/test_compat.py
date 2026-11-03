"""测试运行时兼容性补丁"""

import pytest
from unittest.mock import MagicMock
from src.utils.compat import patch_av_open, apply_all_patches


def test_patch_av_open():
    import av

    # 模拟底层的 open 函数（模拟 PyAV 19+ 不接受 metadata_errors）
    mock_orig_open = MagicMock(return_value="mock_container")

    # 包装测试
    def dummy_pyav19_open(*args, **kwargs):
        """Original docstring"""
        if "metadata_errors" in kwargs:
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        if "metadata_encoding" in kwargs:
            raise TypeError("open() got an unexpected keyword argument 'metadata_encoding'")
        return mock_orig_open(*args, **kwargs)

    # 临时替换 av.open
    old_open = av.open
    try:
        av.open = dummy_pyav19_open
        # 应用补丁
        patch_av_open()

        # 验证元数据保全
        assert av.open.__name__ == "dummy_pyav19_open"
        assert av.open.__doc__ == "Original docstring"

        # 调用时携带 metadata_errors 与 metadata_encoding
        result = av.open("test.wav", mode="r", metadata_errors="ignore", metadata_encoding="utf-8")
        assert result == "mock_container"
        mock_orig_open.assert_called_once_with("test.wav", mode="r")
    finally:
        av.open = old_open


def test_apply_all_patches():
    apply_all_patches()

