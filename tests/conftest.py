"""Pytest 全局配置与无头环境适配。"""

import os
import sys

# 在无实体显示器的环境（如 GitHub Actions CI / 无头测试）下，强制 PySide6 使用 offscreen 插件，
# 规避 Qt 尝试访问 Windows GDI/D3D 时崩溃 (0xC0000409 / STATUS_STACK_BUFFER_OVERRUN)。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_session_exitstatus = 0


def pytest_sessionfinish(session, exitstatus):
    """记录最终退出状态，并尝试安全退出 Qt 事件循环。"""
    global _session_exitstatus
    _session_exitstatus = exitstatus
    try:
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app is not None:
            app.quit()
            app.processEvents()
    except Exception:
        pass


def pytest_unconfigure(config):
    """在 CI 无头环境下，规避 Windows 解释器退出时 C++ 模块析构冲突 (0xC0000409)。"""
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_session_exitstatus)

