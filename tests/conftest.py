"""Pytest 全局配置与无头环境适配。"""

import os

# 在无实体显示器的环境（如 GitHub Actions CI / 无头测试）下，强制 PySide6 使用 offscreen 插件，
# 规避 Qt 尝试访问 Windows GDI/D3D 时崩溃 (0xC0000409 / STATUS_STACK_BUFFER_OVERRUN)。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
