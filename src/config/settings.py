"""配置管理 - 支持绿色版"""

import sys
import os
import configparser
from pathlib import Path
from typing import Any, Optional
from src.utils.exceptions import ConfigurationError
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CONFIG = {
    "app": {"name": "video2text", "version": "1.0.0", "log_level": "INFO"},
    "transcription": {
        "model_path": "large-v3",
        "device": "cuda",
        "language": "zh",
        "beam_size": 5,
        "best_of": 5,
        "temperature": 0.0,
        "compute_type": "float16",
        "num_workers": 1,
        "vad_filter": True,
    },
    "summarization": {
        "ollama_url": "http://127.0.0.1:11434",
        "model_name": "qwen2.5:7b-instruct-q4_K_M",
        "max_length": 500,
        "temperature": 0.7,
    },
    "preprocessing": {
        "ffmpeg_path": "ffmpeg",
        "audio_sample_rate": 16000,
        "audio_channels": 1,
        "max_chunk_duration": 300,
        "supported_video_formats": ".mp4,.avi,.mov,.mkv,.flv,.wmv,.webm",
    },
    "output": {
        "output_dir": "output",
        "transcript_format": "txt,srt,vtt",
        "summary_format": "txt",
        "json_output": True,
    },
    "paths": {"models_dir": "models", "logs_dir": "logs", "video_dir": "video"},
    "network": {"proxy": ""},
}


class Settings:
    """应用程序配置类 - 支持绿色版（便携版）"""

    def __init__(self, config_path: Optional[str] = None):
        """初始化配置"""
        self.config = configparser.ConfigParser()

        # 确定程序基目录
        if getattr(sys, "frozen", False):
            # 打包后的exe环境
            self._base_dir = Path(sys.executable).parent
        else:
            # 开发环境
            self._base_dir = Path(__file__).resolve().parent.parent.parent

        # 确定配置文件路径
        if config_path:
            self.config_path = config_path
        else:
            self.config_path = self._get_default_config_path()

        self._load_default_config()

        if Path(self.config_path).exists():
            self.load()
        else:
            logger.info(f"配置文件不存在，使用默认配置: {self.config_path}")
            # 如果是打包环境且配置文件不存在，保存默认配置
            if getattr(sys, "frozen", False):
                try:
                    self.save()
                    logger.info(f"已创建默认配置文件: {self.config_path}")
                except Exception as e:
                    logger.warning(f"无法创建默认配置文件: {e}")

    def _get_default_config_path(self) -> str:
        """获取默认配置文件路径 - 支持绿色版"""
        # 1. 优先使用环境变量
        env_config = os.environ.get("VIDEO2TEXT_CONFIG")
        if env_config:
            return env_config

        # 2. 使用程序目录下的config.ini
        config_path = self._base_dir / "config.ini"

        # 3. 如果不存在，尝试当前工作目录
        if not config_path.exists():
            cwd_config = Path.cwd() / "config.ini"
            if cwd_config.exists():
                return str(cwd_config)

        return str(config_path)

    def _resolve_path(self, path_str: str) -> str:
        """解析路径，如果是相对路径则基于程序目录"""
        if not path_str or path_str.strip() == "":
            return path_str

        p = Path(path_str)
        if not p.is_absolute():
            return str(self._base_dir / path_str)
        return path_str

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
            value = self.config.get(section, option)

            # 对特定配置项解析路径（绿色版支持）
            path_keys = [
                "paths.models_dir",
                "paths.logs_dir",
                "paths.video_dir",
                "output.output_dir",
            ]

            if key in path_keys:
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
