"""验证器"""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional, List
from src.i18n import t
from src.utils.exceptions import VideoFileError, ConfigurationError


def validate_file_path(
    file_path: str, allowed_extensions: Optional[List[str]] = None
) -> Path:
    """验证文件路径

    Args:
        file_path: 文件路径
        allowed_extensions: 允许的文件扩展名列表

    Returns:
        验证后的Path对象

    Raises:
        VideoFileError: 文件不存在或格式不正确
    """
    path = Path(file_path)

    if not path.exists():
        raise VideoFileError(t("errors.file_not_found", path=file_path))

    if not path.is_file():
        raise VideoFileError(t("errors.not_a_file", path=file_path))

    if allowed_extensions:
        if path.suffix.lower() not in [ext.lower() for ext in allowed_extensions]:
            raise VideoFileError(
                t("errors.unsupported_format", suffix=path.suffix, formats=', '.join(allowed_extensions))
            )

    return path


def validate_directory(dir_path: str, create: bool = False) -> Path:
    """验证目录路径

    Args:
        dir_path: 目录路径
        create: 如果目录不存在是否创建

    Returns:
        验证后的Path对象

    Raises:
        ConfigurationError: 目录不存在且不允许创建
    """
    path = Path(dir_path)

    if not path.exists():
        if create:
            path.mkdir(parents=True, exist_ok=True)
        else:
            raise ConfigurationError(t("errors.dir_not_found", path=dir_path))

    if not path.is_dir():
        raise ConfigurationError(t("errors.not_a_dir", path=dir_path))

    return path


def validate_language(language: str, supported_languages: List[str]) -> str:
    """验证语言代码

    Args:
        language: 语言代码
        supported_languages: 支持的语言列表

    Returns:
        验证后的语言代码

    Raises:
        ConfigurationError: 语言不支持
    """
    if language == "auto":
        return language

    language = language.lower()
    supported_lower = {lang.lower() for lang in supported_languages}
    if language not in supported_lower:
        raise ConfigurationError(
            t("errors.unsupported_lang", lang=language, supported=', '.join(supported_languages))
        )

    return language


def validate_device(device: str) -> str:
    """验证设备类型

    Args:
        device: 设备类型

    Returns:
        验证后的设备类型

    Raises:
        ConfigurationError: 设备类型不支持
    """
    valid_devices = ["auto", "cpu", "cuda", "mps"]

    if device not in valid_devices:
        raise ConfigurationError(
            t("errors.unsupported_device", device=device, supported=', '.join(valid_devices))
        )

    return device


def validate_positive_int(value: int, name: str) -> int:
    """验证正整数

    Args:
        value: 整数值
        name: 参数名称

    Returns:
        验证后的整数值

    Raises:
        ConfigurationError: 值不是正整数
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(t("errors.must_be_positive", name=name, value=value))

    return value


def validate_float_range(
    value: float, name: str, min_val: float = 0.0, max_val: float = 1.0
) -> float:
    """验证浮点数范围

    Args:
        value: 浮点数值
        name: 参数名称
        min_val: 最小值
        max_val: 最大值

    Returns:
        验证后的浮点数值

    Raises:
        ConfigurationError: 值超出范围
    """
    if not isinstance(value, (int, float)):
        raise ConfigurationError(t("errors.must_be_number", name=name, value=value))

    if not (min_val <= value <= max_val):
        raise ConfigurationError(t("errors.out_of_range", name=name, min=min_val, max=max_val, value=value))

    return float(value)


def _is_executable_file(p: Path) -> bool:
    """检查文件是否具有真实的可执行属性。"""
    if not p.is_file():
        return False
    if sys.platform == "win32":
        pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        valid_exts = {ext.strip().upper() for ext in pathext if ext.strip()}
        return p.suffix.upper() in valid_exts
    return os.access(p, os.X_OK)


def validate_executable_path(path: str, name: str = "executable") -> str:
    """验证可执行文件路径的安全性

    检查路径中是否包含危险字符，确保指向真实的可执行文件。
    在 Windows 下结合 %PATHEXT% 校验，POSIX 下结合 os.X_OK 权限校验。

    Args:
        path: 可执行文件路径
        name: 文件名称（用于错误消息）

    Returns:
        解析后的绝对路径

    Raises:
        ConfigurationError: 路径不安全、文件不存在或不可执行
    """
    if not path or not path.strip():
        raise ConfigurationError(t("errors.path_empty", name=name))

    path = path.strip()

    # 检查是否包含 shell 注入风险字符（Unix + Windows）
    # 注意：不包含 () 以兼容 Windows 常见路径如 "Program Files (x86)"
    dangerous_chars = [
        "&",
        "|",
        ";",
        "$",
        "`",
        "\n",
        "\r",
        ">",
        "<",
    ]
    for ch in dangerous_chars:
        if ch in path:
            raise ConfigurationError(t("errors.path_unsafe", name=name, ch=ch, path=path))

    # 通过 shutil.which 解析（自动检查 PATH 与可执行扩展名/权限）
    resolved = shutil.which(path)
    if resolved:
        res_p = Path(resolved)
        if _is_executable_file(res_p):
            return str(res_p.resolve())

    # 如果 which 找不到或未通过，检查是否为显式文件路径且具备可执行属性
    p = Path(path)
    if p.exists() and _is_executable_file(p):
        return str(p.resolve())

    raise ConfigurationError(t("errors.path_unavailable", name=name, path=path))
