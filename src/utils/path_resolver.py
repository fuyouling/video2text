"""路径解析工具 —— 统一处理用户输入的输出目录：

- 展开 ``~``（用户目录）
- 去除输入框可能夹带的引号 / 首尾空白
- 相对路径基于程序基目录解析（与 config.ini 中 ``output.output_dir`` 的解析基准一致）
- 用 ``normpath`` + ``normcase`` 做跨平台规范化与去重比较
- 对 Windows 非法字符、保留设备名与超长路径做安全处理
"""

import os
import re
from pathlib import Path

from src.utils.paths import get_base_dir
from src.utils.logger import get_logger
from src.i18n import t

logger = get_logger(__name__)

# Windows 文件名非法字符（路径分隔符单独处理）
_ILLEGAL_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_PATH = 259

# Windows 保留设备名称
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def normalize_path_key(path: str) -> str:
    """用于比较 / 去重的规范化键（不要求路径存在）。"""
    return os.path.normpath(os.path.normcase(path))


def to_long_path(path: str | Path) -> str:
    """Windows 下超过 259 字符时添加 \\\\?\\ 前缀以支持长路径。"""
    path_str = str(path)
    if os.name != "nt":
        return path_str
    if path_str.startswith("\\\\?\\"):
        return path_str
    norm = os.path.normpath(path_str)
    if len(norm) > _MAX_PATH:
        abs_path = os.path.abspath(norm)
        if abs_path.startswith(r"\\"):
            return "\\\\?\\UNC\\" + abs_path[2:]
        return "\\\\?\\" + abs_path
    return path_str


def resolve_output_path(text: str, base_dir: Path | None = None) -> str:
    """把用户在输出框输入的目录解析为绝对路径。

    - 空字符串返回空字符串
    - 去引号、去首尾空白
    - ``~`` 展开为当前用户主目录
    - 相对路径基于 ``base_dir``（默认程序基目录）；绝对路径原样保留
    - 超过 259 字符的路径自动转换长路径前缀
    """
    if not text:
        return ""
    s = text.strip().strip('"').strip("'").strip()
    if not s:
        return ""
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = (base_dir or get_base_dir()) / s
    res = str(p)
    if is_path_too_long(res):
        res = to_long_path(res)
    return res


_MAX_NAME_STEM_LEN = 200


def sanitize_filename(name: str) -> str:
    """把任意字符串清洗成合法的单个文件名（不含扩展名）。

    对 Windows 保留设备名（CON, PRN, AUX, NUL, COM1-9, LPT1-9）进行安全前缀转换，
    并限制最大单分量长度，防止追加后缀后超出 NTFS 255 字符上限。
    """
    cleaned = _ILLEGAL_NAME_CHARS.sub("_", name)
    # 去除首尾空格与 Windows 保留的尾点
    cleaned = cleaned.strip().rstrip(".")
    if not cleaned:
        return "untitled"

    # Windows 保留设备名安全前缀转换
    stem = cleaned.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    # 截断单分量长度，保留后缀空间
    if len(cleaned) > _MAX_NAME_STEM_LEN:
        cleaned = cleaned[:_MAX_NAME_STEM_LEN].rstrip(".")
    return cleaned


def is_path_too_long(path: str) -> bool:
    """Windows 下未启用长路径支持时，完整路径（含盘符）超过 259 字符会失败。"""
    if str(path).startswith("\\\\?\\"):
        return False
    return len(os.path.normpath(path)) > _MAX_PATH


def validate_output_dir(text: str, base_dir: Path | None = None) -> tuple[bool, str]:
    """校验输出目录输入是否可用，返回 (ok, resolved_or_error)。

    仅在语法层面校验，不创建目录、不检查实际可写（由调用方 mkdir 时捕获）。
    """
    resolved = resolve_output_path(text, base_dir)
    if not resolved:
        return False, ""
    # 剥离 Windows 扩展长路径前缀 \\?\ 或 \\?\UNC\ 后再检验非法通配符
    check_target = resolved
    if check_target.startswith("\\\\?\\UNC\\"):
        check_target = "\\\\" + check_target[8:]
    elif check_target.startswith("\\\\?\\"):
        check_target = check_target[4:]

    if any(ch in check_target for ch in "*?"):
        return False, resolved
    if is_path_too_long(resolved):
        logger.warning(t("path_resolver.path_too_long", max=_MAX_PATH, path=resolved))
        resolved = to_long_path(resolved)
    return True, resolved
