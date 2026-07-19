"""路径工具 —— 项目基目录等常用路径的统一来源"""

import os
import sys
from pathlib import Path


def get_base_dir() -> Path:
    """获取项目基目录（支持 frozen / 绿色版打包）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


def ensure_cuda_libs() -> None:
    """将 libs/ 加入 DLL 搜索路径，供 ctranslate2 加载 CUDA/cuDNN（本地与打包通用）。

    同时使用两种机制，覆盖不同 DLL 加载场景：
      1. os.add_dll_directory  —— 对 LOAD_LIBRARY_SEARCH_DEFAULT_DIRS 标志的 LoadLibraryEx 生效
      2. PATH 环境变量         —— 对所有 LoadLibrary / LoadLibraryEx 变体均生效（CUDA Runtime 内部加载必需）
    """
    libs_dir = get_base_dir() / "libs"
    if libs_dir.is_dir():
        libs_str = str(libs_dir)
        try:
            os.add_dll_directory(libs_str)
        except (OSError, AttributeError):
            pass
        # 修改 PATH：CUDA Runtime 内部使用 LoadLibrary 加载 cublas/cudnn，
        # 该方式不识别 os.add_dll_directory，因此必须通过 PATH 确保找到。
        if libs_str not in os.environ.get("PATH", ""):
            os.environ["PATH"] = libs_str + os.pathsep + os.environ.get("PATH", "")
