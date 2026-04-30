"""验证器"""

import os
import re
import shutil
from pathlib import Path
from typing import Optional, List
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
        raise VideoFileError(f"文件不存在: {file_path}")

    if not path.is_file():
        raise VideoFileError(f"路径不是文件: {file_path}")

    if allowed_extensions:
        if path.suffix.lower() not in [ext.lower() for ext in allowed_extensions]:
            raise VideoFileError(
                f"不支持的文件格式: {path.suffix}. "
                f"支持的格式: {', '.join(allowed_extensions)}"
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
            raise ConfigurationError(f"目录不存在: {dir_path}")

    if not path.is_dir():
        raise ConfigurationError(f"路径不是目录: {dir_path}")

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

    if language not in supported_languages:
        raise ConfigurationError(
            f"不支持的语言: {language}. 支持的语言: {', '.join(supported_languages)}"
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
    valid_devices = ["auto", "cpu", "cuda"]

    if device not in valid_devices:
        raise ConfigurationError(
            f"不支持的设备类型: {device}. 支持的设备: {', '.join(valid_devices)}"
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
    if not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{name}必须是正整数: {value}")

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
        raise ConfigurationError(f"{name}必须是数字: {value}")

    if not (min_val <= value <= max_val):
        raise ConfigurationError(f"{name}必须在{min_val}和{max_val}之间: {value}")

    return float(value)


def validate_executable_path(path: str, name: str = "executable") -> str:
    """验证可执行文件路径的安全性

    检查路径中是否包含危险字符，确保指向真实的可执行文件。

    Args:
        path: 可执行文件路径
        name: 文件名称（用于错误消息）

    Returns:
        解析后的绝对路径

    Raises:
        ConfigurationError: 路径不安全或文件不存在
    """
    if not path or not path.strip():
        raise ConfigurationError(f"{name}路径不能为空")

    path = path.strip()

    # 检查是否包含 shell 注入风险字符
    dangerous_chars = ["&", "|", ";", "$", "`", "\n", "\r", "(", ")"]
    for ch in dangerous_chars:
        if ch in path:
            raise ConfigurationError(f"{name}路径包含不安全字符 '{ch}': {path}")

    # 通过 shutil.which 解析（自动检查 PATH 和可执行权限）
    resolved = shutil.which(path)
    if resolved:
        return resolved

    # 如果 which 找不到，检查文件是否实际存在
    p = Path(path)
    if p.exists() and p.is_file():
        return str(p.resolve())

    raise ConfigurationError(f"{name}不可用: {path}")
