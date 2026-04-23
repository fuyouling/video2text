"""模型管理器"""

import os
from pathlib import Path
from typing import Optional
from src.utils.exceptions import TranscriptionError
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ModelManager:
    """模型管理器"""

    def __init__(self, models_dir: str = "models"):
        """初始化模型管理器

        Args:
            models_dir: 模型目录
        """
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def get_model_path(self, model_name: str) -> Path:
        """获取模型路径

        Args:
            model_name: 模型名称

        Returns:
            模型路径
        """
        model_path = self.models_dir / model_name

        if model_path.exists():
            return model_path

        return Path(model_name)

    def check_model_exists(self, model_name: str) -> bool:
        """检查模型是否存在

        Args:
            model_name: 模型名称

        Returns:
            模型是否存在
        """
        model_path = self.get_model_path(model_name)
        return model_path.exists()

    def get_available_models(self) -> list:
        """获取可用模型列表

        Returns:
            可用模型列表
        """
        models = []

        if not self.models_dir.exists():
            return models

        for item in self.models_dir.iterdir():
            if item.is_dir():
                models.append(item.name)

        return models

    def get_model_size(self, model_name: str) -> Optional[int]:
        """获取模型大小

        Args:
            model_name: 模型名称

        Returns:
            模型大小（字节），如果模型不存在则返回None
        """
        model_path = self.get_model_path(model_name)

        if not model_path.exists():
            return None

        total_size = 0

        if model_path.is_file():
            total_size = model_path.stat().st_size
        elif model_path.is_dir():
            for item in model_path.rglob("*"):
                if item.is_file():
                    total_size += item.stat().st_size

        return total_size

    def delete_model(self, model_name: str) -> bool:
        """删除模型

        Args:
            model_name: 模型名称

        Returns:
            是否删除成功
        """
        model_path = self.get_model_path(model_name)

        if not model_path.exists():
            logger.warning(f"模型不存在: {model_name}")
            return False

        try:
            if model_path.is_file():
                model_path.unlink()
            elif model_path.is_dir():
                for item in model_path.rglob("*"):
                    if item.is_file():
                        item.unlink()
                model_path.rmdir()

            logger.info(f"模型删除成功: {model_name}")
            return True
        except Exception as e:
            logger.error(f"模型删除失败: {e}")
            return False
