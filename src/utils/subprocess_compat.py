"""subprocess 兼容常量与工具 —— 跨平台 CREATE_NO_WINDOW 与参数封装"""

import subprocess
import sys

CREATE_NO_WINDOW: int = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    if sys.platform == "win32"
    else 0
)


def get_no_window_kwargs() -> dict:
    """返回用于 subprocess.Popen / subprocess.run 的跨平台隐藏控制台窗口参数字典。

    在 Windows 下返回 {"creationflags": CREATE_NO_WINDOW}；
    在 POSIX / macOS 等非 Windows 系统下返回空字典 {}。
    调用方可直接以 `**get_no_window_kwargs()` 传参。
    """
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}
