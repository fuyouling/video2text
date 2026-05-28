"""UI 日志桥接 —— 将 Python logging 转发到 Qt 信号"""

import logging

from PySide6.QtCore import QObject, Signal
from src.utils.logger import _ShortPathFormatter


class UiLogSignal(QObject):
    """Qt 信号对象 —— 用于跨线程传递日志消息。"""

    message = Signal(str)


class UiLogHandler(logging.Handler):
    """Python logging Handler —— 将日志记录转发到 UiLogSignal 的 message 信号。"""

    def __init__(self, signal: UiLogSignal) -> None:
        super().__init__()
        self._signal = signal
        self.setFormatter(_ShortPathFormatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """将日志记录格式化后通过 Qt 信号发送到 UI 线程。"""
        self._signal.message.emit(self.format(record))
