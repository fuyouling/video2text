"""Video2Text 主程序入口"""

from pathlib import Path

import sys
import traceback

from dotenv import load_dotenv
from src.utils.paths import ensure_cuda_libs, get_base_dir


def main():
    """应用程序主入口。

    打包模式（frozen）下无参数启动时打开 GUI，否则启动 CLI（Typer）。
    启动异常时在打包模式下将 traceback 写入 logs/error_startup.log。
    """
    ensure_cuda_libs()
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
