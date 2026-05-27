"""路径工具 —— 项目基目录等常用路径的统一来源"""

import sys
from pathlib import Path


def get_base_dir() -> Path:
    """获取项目基目录（支持 frozen / 绿色版打包）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent
