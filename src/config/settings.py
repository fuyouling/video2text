"""配置管理"""

import os
import configparser
from pathlib import Path
from typing import Any, Optional
from src.config.constants import DEFAULT_CONFIG
from src.utils.exceptions import ConfigurationError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class Settings:
    """应用程序配置类"""

    def __init__(self, config_path: Optional[str] = None):
        """初始化配置

        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
        """
        self.config = configparser.ConfigParser()
        self.config_path = config_path or self._get_default_config_path()
        self._load_default_config()

        if Path(self.config_path).exists():
            self.load()
        else:
            logger.info(f"配置文件不存在，使用默认配置: {self.config_path}")

    def _get_default_config_path(self) -> str:
        """获取默认配置文件路径

        Returns:
            配置文件路径
        """
        env_config = os.environ.get("VIDEO2TEXT_CONFIG")
        if env_config:
            return env_config

        return "config.ini"

    def _load_default_config(self) -> None:
        """加载默认配置"""
        for section, values in DEFAULT_CONFIG.items():
            self.config[section] = {}
            for key, value in values.items():
                self.config[section][key] = str(value)

    def load(self) -> None:
        """加载配置文件"""
        try:
            self.config.read(self.config_path, encoding="utf-8")
            logger.info(f"配置文件加载成功: {self.config_path}")
        except Exception as e:
            raise ConfigurationError(f"加载配置文件失败: {e}")

    def save(self) -> None:
        """保存配置文件"""
        try:
            config_path = Path(self.config_path)
            config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(config_path, "w", encoding="utf-8") as f:
                self.config.write(f)

            logger.info(f"配置文件保存成功: {self.config_path}")
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
            return self.config.get(section, option)
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

            if not self.config.has_section(section):
                self.config.add_section(section)

            self.config.set(section, option, str(value))
            logger.debug(f"配置项已更新: {key} = {value}")
        except ValueError as e:
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

    def get_section(self, section: str) -> dict:
        """获取配置节

        Args:
            section: 配置节名称

        Returns:
            配置节字典
        """
        if not self.config.has_section(section):
            return {}

        return dict(self.config.items(section))

    def update_from_dict(self, config_dict: dict) -> None:
        """从字典更新配置

        Args:
            config_dict: 配置字典
        """
        for section, values in config_dict.items():
            if not self.config.has_section(section):
                self.config.add_section(section)

            for key, value in values.items():
                self.config.set(section, key, str(value))

        logger.info("配置已从字典更新")

    def to_dict(self) -> dict:
        """转换为字典

        Returns:
            配置字典
        """
        result = {}
        for section in self.config.sections():
            result[section] = dict(self.config.items(section))
        return result
