"""进度显示"""

import time
from typing import Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ProgressTracker:
    """进度跟踪器"""

    def __init__(self, total_steps: int, description: str = "处理中"):
        """初始化进度跟踪器

        Args:
            total_steps: 总步骤数
            description: 描述
        """
        self.total_steps = total_steps
        self.current_step = 0
        self.description = description
        self.start_time = time.time()

    def update(self, step: int = 1, message: Optional[str] = None):
        """更新进度

        Args:
            step: 步骤数
            message: 消息
        """
        self.current_step += step
        progress = self.current_step / self.total_steps * 100
        elapsed = time.time() - self.start_time

        if self.current_step > 0:
            eta = elapsed / self.current_step * (self.total_steps - self.current_step)
        else:
            eta = 0

        status = f"{self.description}: {self.current_step}/{self.total_steps} ({progress:.1f}%)"

        if message:
            status += f" - {message}"

        if eta > 0:
            status += f" - ETA: {self._format_time(eta)}"

        logger.info(status)

    def complete(self, message: Optional[str] = None):
        """完成进度

        Args:
            message: 消息
        """
        elapsed = time.time() - self.start_time
        status = f"{self.description}: 完成 (耗时: {self._format_time(elapsed)})"

        if message:
            status += f" - {message}"

        logger.info(status)

    def _format_time(self, seconds: float) -> str:
        """格式化时间

        Args:
            seconds: 秒数

        Returns:
            格式化后的时间字符串
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"


class SimpleProgress:
    """简单进度显示"""

    def __init__(self):
        """初始化简单进度显示"""
        self.last_update = 0

    def show(self, message: str, force: bool = False):
        """显示进度消息

        Args:
            message: 消息
            force: 强制显示
        """
        current_time = time.time()

        if force or current_time - self.last_update >= 1.0:
            logger.info(message)
            self.last_update = current_time
