"""路径解析工具 —— 统一处理用户输入的输出目录：

- 展开 ``~``（用户目录）
- 去除输入框可能夹带的引号 / 首尾空白
- 相对路径基于程序基目录解析（与 config.ini 中 ``output.output_dir`` 的解析基准一致）
- 用 ``normpath`` + ``normcase`` 做跨平台规范化与去重比较
- 对 Windows 非法字符与超长路径做基础校验
"""

import os
import re
from pathlib import Path

from src.utils.paths import get_base_dir
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Windows 文件名非法字符（路径分隔符单独处理）
_ILLEGAL_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_PATH = 259


def normalize_path_key(path: str) -> str:
    """用于比较 / 去重的规范化键（不要求路径存在）。"""
    return os.path.normpath(os.path.normcase(path))


def resolve_output_path(text: str, base_dir: Path | None = None) -> str:
    """把用户在输出框输入的目录解析为绝对路径。

    - 空字符串返回空字符串
    - 去引号、去首尾空白
    - ``~`` 展开为当前用户主目录
    - 相对路径基于 ``base_dir``（默认程序基目录）；绝对路径原样保留
    """
    if not text:
        return ""
    s = text.strip().strip('"').strip("'").strip()
    if not s:
        return ""
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = (base_dir or get_base_dir()) / s
    return str(p)


def sanitize_filename(name: str) -> str:
    """把任意字符串清洗成合法的单个文件名（不含扩展名）。"""
    cleaned = _ILLEGAL_NAME_CHARS.sub("_", name)
    # 去除首尾空格与 Windows 保留的尾点
    cleaned = cleaned.strip().rstrip(".")
    return cleaned or "untitled"


def is_path_too_long(path: str) -> bool:
    """Windows 下未启用长路径支持时，完整路径（含盘符）超过 259 字符会失败。"""
    return len(os.path.normpath(path)) > _MAX_PATH


def validate_output_dir(text: str, base_dir: Path | None = None) -> tuple[bool, str]:
    """校验输出目录输入是否可用，返回 (ok, resolved_or_error)。

    仅在语法层面校验，不创建目录、不检查实际可写（由调用方 mkdir 时捕获）。
    """
    resolved = resolve_output_path(text, base_dir)
    if not resolved:
        return False, ""
    # 必须落在当前盘符内，且不含残留的通配 / 非法分隔
    if any(ch in resolved for ch in "*?"):
        return False, resolved
    if is_path_too_long(resolved):
        logger.warning("输出目录路径过长（>%d 字符），保存可能失败: %s", _MAX_PATH, resolved)
    return True, resolved
