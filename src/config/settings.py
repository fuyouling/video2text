"""配置管理 - 以 config.ini 为唯一版本源，支持绿色版

所有配置项的默认值由调用方（CLI / GUI / 服务层）在调用 get/get_int/... 时通过
default 参数传入。本模块不内置任何默认配置值。
"""

import configparser
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from src.utils.exceptions import ConfigurationError
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_TIMEOUT = 300
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b-instruct-q4_K_M"

DEFAULT_NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_NVIDIA_MODEL = "openai/gpt-oss-120b"
DEFAULT_NVIDIA_MAX_TOKENS = 100000
DEFAULT_NVIDIA_TEMPERATURE = 1.0
DEFAULT_NVIDIA_TOP_P = 1.0

APP_NAME = "video2text"
APP_VERSION = "1.6.0"


def _get_base_dir() -> Path:
    """获取程序基础目录 - 支持 frozen（打包）和开发环境"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


class Settings:
    """应用程序配置类 - 以 config.ini 为唯一版本源，支持绿色版（便携版）

    单例模式：同一进程内只加载一次配置文件，避免重复日志输出。
    GUI 通过 set() + save() 修改配置，所有引用同一实例的地方自动生效。
    """

    _instance: Optional["Settings"] = None
    _lock = threading.Lock()

    PATH_KEYS: frozenset[str] = frozenset(
        [
            "paths.models_dir",
            "paths.logs_dir",
            "paths.video_dir",
            "output.output_dir",
        ]
    )

    def __new__(cls, config_path: Optional[str] = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        """初始化配置

        如果 config.ini 不存在，config 对象保持空状态，
        所有 get 调用将返回调用方提供的 default 值。

        注意：单例模式下，仅首次实例化时加载配置文件。
        后续调用 Settings() 不会重新加载，修改配置请使用 set() + save()。
        """
        if self._initialized:
            if config_path and config_path != self.config_path:
                logger.warning(
                    "Settings 单例已初始化，忽略新的 config_path: %s (当前: %s)",
                    config_path,
                    self.config_path,
                )
            return

        self.config = configparser.ConfigParser(interpolation=None)
        self._base_dir = _get_base_dir()

        if config_path:
            self.config_path = config_path
        else:
            self.config_path = self._get_default_config_path()

        if Path(self.config_path).exists():
            self._load()
        else:
            logger.info(f"配置文件不存在: {self.config_path}，使用调用方默认值")

        self._initialized = True

    @classmethod
    def _reset(cls) -> None:
        """重置单例（仅供测试使用）"""
        cls._instance = None

    def _get_default_config_path(self) -> str:
        """获取默认配置文件路径 - 支持绿色版"""
        env_config = os.environ.get("VIDEO2TEXT_CONFIG")
        if env_config:
            return env_config

        config_path = self._base_dir / "config.ini"
        if not config_path.exists():
            cwd_config = Path.cwd() / "config.ini"
            if cwd_config.exists():
                return str(cwd_config)

        return str(config_path)

    def _resolve_path(self, path_str: str) -> str:
        """解析路径，如果是相对路径则基于程序目录，并规范化"""
        if not path_str or path_str.strip() == "":
            return path_str

        p = Path(path_str)
        if not p.is_absolute():
            p = self._base_dir / path_str
        return str(p.resolve())

    def _load(self) -> None:
        """内部加载，首次初始化时调用，输出日志"""
        try:
            self.config.read(self.config_path, encoding="utf-8")
            logger.info(f"配置文件加载成功: {self.config_path}")
        except Exception as e:
            raise ConfigurationError(f"加载配置文件失败: {e}")

    def reload(self) -> None:
        """从磁盘重新加载配置文件（不输出日志，供 GUI 刷新用）"""
        try:
            new_config = configparser.ConfigParser(interpolation=None)
            new_config.read(self.config_path, encoding="utf-8")
            with self._lock:
                self.config = new_config
        except Exception as e:
            raise ConfigurationError(f"重新加载配置文件失败: {e}")

    def save(self) -> None:
        """保存配置文件（原子写入，防止崩溃损坏）"""
        try:
            config_path = Path(self.config_path)
            config_path.parent.mkdir(parents=True, exist_ok=True)

            with self._lock:
                fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        self.config.write(f)
                    os.replace(tmp_path, config_path)
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise

            logger.info(f"配置文件保存成功: {self.config_path}")
        except ConfigurationError:
            raise
        except Exception as e:
            raise ConfigurationError(f"保存配置文件失败: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项

        Args:
            key: 配置键，格式为"section.key"
            default: 默认值

        Returns:
            配置值
        """
        try:
            section, option = key.split(".", 1)
            value = self.config.get(section, option)

            if key in self.PATH_KEYS:
                return self._resolve_path(value)

            return value
        except (ValueError, configparser.NoSectionError, configparser.NoOptionError):
            return default

    def set(self, key: str, value: Any) -> None:
        """设置配置项

        Args:
            key: 配置键，格式为"section.key"
            value: 配置值
        """
        try:
            section, option = key.split(".", 1)

            with self._lock:
                if not self.config.has_section(section):
                    self.config.add_section(section)

                self.config.set(section, option, str(value))
            logger.debug(f"配置项已更新: {key} = {value}")
        except ValueError:
            raise ConfigurationError(f"无效的配置键格式: {key}")

    def get_int(self, key: str, default: int = 0) -> int:
        """获取整数配置项"""
        try:
            section, option = key.split(".", 1)
            return self.config.getint(section, option)
        except (ValueError, configparser.NoSectionError, configparser.NoOptionError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """获取浮点数配置项"""
        try:
            section, option = key.split(".", 1)
            return self.config.getfloat(section, option)
        except (ValueError, configparser.NoSectionError, configparser.NoOptionError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """获取布尔配置项"""
        try:
            section, option = key.split(".", 1)
            return self.config.getboolean(section, option)
        except (ValueError, configparser.NoSectionError, configparser.NoOptionError):
            return default

    def get_list(
        self, key: str, default: Optional[list[str]] = None, separator: str = ","
    ) -> list[str]:
        """获取列表配置项"""
        value = self.get(key)
        if value is None:
            return default.copy() if default is not None else []

        items = [item.strip() for item in value.split(separator)]
        return [item for item in items if item] or (
            default.copy() if default is not None else []
        )

    def _resolve_section_paths(self, section: str, items: dict) -> dict:
        """对 section 中的 PATH_KEYS 条目自动解析路径"""
        prefix = f"{section}."
        for k, v in items.items():
            if f"{prefix}{k}" in self.PATH_KEYS:
                items[k] = self._resolve_path(v)
        return items

    def get_section(self, section: str) -> dict:
        """获取配置节，PATH_KEYS 中的路径会自动解析"""
        if not self.config.has_section(section):
            return {}

        return self._resolve_section_paths(section, dict(self.config.items(section)))

    def update_from_dict(self, config_dict: dict) -> None:
        """从字典更新配置"""
        with self._lock:
            for section, values in config_dict.items():
                if not self.config.has_section(section):
                    self.config.add_section(section)

                for key, value in values.items():
                    self.config.set(section, key, str(value))

        logger.info("配置已从字典更新")

    def to_dict(self) -> dict:
        """转换为字典，PATH_KEYS 中的路径会自动解析"""
        result = {}
        for section in self.config.sections():
            result[section] = self._resolve_section_paths(
                section, dict(self.config.items(section))
            )
        return result


class PromptManager:
    """提示词模板管理器 - 将用户自定义提示词保存为命名模板，支持持久化

    数据存储在 prompts.json 文件中（与 config.ini 同目录），格式：
    {
        "templates": {"模板名": "提示词内容", ...},
        "last_used": "模板名"
    }

    第一次运行时文件不存在，模板列表为空。
    """

    _instance: Optional["PromptManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        base_dir = Path(Settings().config_path).parent
        self._file_path = base_dir / "prompts.json"
        self._templates: dict[str, str] = {}
        self._last_used: str = ""
        self._load()
        self._initialized = True

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            self._templates = data.get("templates", {})
            self._last_used = data.get("last_used", "")
            logger.info(f"提示词模板加载成功: {self._file_path}")
        except Exception as e:
            logger.warning(f"提示词模板加载失败: {e}")

    def save(self) -> None:
        try:
            data = {
                "templates": self._templates,
                "last_used": self._last_used,
            }
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=self._file_path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._file_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except ConfigurationError:
            raise
        except Exception as e:
            logger.error(f"提示词模板保存失败: {e}")
            raise ConfigurationError(f"提示词模板保存失败: {e}")

    def get_names(self) -> list[str]:
        return list(self._templates.keys())

    def get_content(self, name: str) -> str:
        return self._templates.get(name, "")

    def set_template(self, name: str, content: str) -> None:
        self._templates[name] = content
        self.save()

    def delete_template(self, name: str) -> None:
        self._templates.pop(name, None)
        if self._last_used == name:
            self._last_used = ""
        self.save()

    def get_last_used(self) -> str:
        return self._last_used

    def set_last_used(self, name: str) -> None:
        self._last_used = name
        self.save()

    def get_last_used_content(self) -> str:
        if self._last_used and self._last_used in self._templates:
            return self._templates[self._last_used]
        return ""
