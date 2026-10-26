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
    """将 libs/ 及 site-packages/nvidia/ 下的 bin 加入 DLL 搜索路径，供 ctranslate2 和 onnxruntime 加载 CUDA/cuDNN。

    同时使用两种机制，覆盖不同 DLL 加载场景：
      1. os.add_dll_directory  —— 对 LOAD_LIBRARY_SEARCH_DEFAULT_DIRS 标志的 LoadLibraryEx 生效
      2. PATH 环境变量         —— 对所有 LoadLibrary / LoadLibraryEx 变体均生效（CUDA Runtime 内部加载必需）
    """
    dirs_to_add: list[str] = []

    libs_dir = get_base_dir() / "libs"
    if libs_dir.is_dir():
        dirs_to_add.append(str(libs_dir))

    # 在打包单目录或单文件模式下，兼容 _internal/libs 与 sys._MEIPASS/libs
    if getattr(sys, "frozen", False):
        internal_libs = Path(sys.executable).parent / "_internal" / "libs"
        if internal_libs.is_dir():
            dirs_to_add.append(str(internal_libs))
        meipass_libs = Path(getattr(sys, "_MEIPASS", "")) / "libs"
        if meipass_libs.is_dir():
            dirs_to_add.append(str(meipass_libs))

    # 在非打包或开发环境中，自动发现 site-packages/nvidia/*/bin 并加入搜索路径
    try:
        import site

        for sp in site.getsitepackages():
            nvidia_root = Path(sp) / "nvidia"
            if nvidia_root.is_dir():
                for bin_dir in nvidia_root.glob("*/bin"):
                    if bin_dir.is_dir():
                        dirs_to_add.append(str(bin_dir))
    except Exception:
        pass

    for d in dirs_to_add:
        try:
            os.add_dll_directory(d)
        except (OSError, AttributeError):
            pass
        if d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + d

