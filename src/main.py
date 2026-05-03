"""Video2Text 主程序入口"""

from dotenv import load_dotenv

load_dotenv()

import sys
import traceback
from pathlib import Path


def main():
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
