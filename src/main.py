"""Video2Text 主程序入口"""

from pathlib import Path

from dotenv import load_dotenv
from src.utils.paths import get_base_dir

import os
import sys
import traceback


def _ensure_cuda_libs():
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


def main():
    """应用程序主入口。

    打包模式（frozen）下无参数启动时打开 GUI，否则启动 CLI（Typer）。
    启动异常时在打包模式下将 traceback 写入 logs/error_startup.log。
    """
    _ensure_cuda_libs()
    # 所有运行模式（GUI / CLI）在入口处统一加载一次项目根目录的 .env，
    # 确保 NVIDIA_API_KEY / ZHIPU_API_KEY 等环境变量在任意工作目录下均可读取。
    load_dotenv(get_base_dir() / ".env", override=True)

    try:
        if getattr(sys, "frozen", False) and len(sys.argv) <= 1:
            from src.ui.gui import main as gui_main

            gui_main()
        else:
            from src.ui.cli import app

            app()
    except Exception:
        if getattr(sys, "frozen", False):
            err_log = Path(sys.executable).parent / "logs" / "error_startup.log"
            err_log.parent.mkdir(parents=True, exist_ok=True)
            with open(err_log, "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        raise


if __name__ == "__main__":
    main()
