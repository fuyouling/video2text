"""日志面板组件 —— 从 MainWindow 提取的日志显示与渲染逻辑"""

import logging
import re
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QGroupBox, QTextEdit, QVBoxLayout, QWidget

from src.ui.ui_log_bridge import UiLogHandler, UiLogSignal

_RE_TAG = re.compile(r"^(\[[\u4e00-\u9fa5\d/]+\])(.*)$")
_RE_TAG_STEP = re.compile(r"^(\[\d+/\d+\])(.*?)( ✓| ✗)(.*)$")
_RE_STEP = re.compile(r"^(  [├└]─ )(.+?)( ✓| ✗)(.*)$")
_RE_PROG = re.compile(r"^(  [├└]─ )(.+?)( …)(.*)$")
_RE_STATE = re.compile(r"^(  [├└]─ )(⏸|▶|⏹)(.*)$")
_RE_TREE = re.compile(r"^(  (?:[├└]─|│)[ ─│]*(?:[├└]─ )?)(.*)$")

_LOG_COLOR_RULES = [
    (re.compile(r"失败|错误|异常|✘|✗"), QColor("#F44336")),
    (re.compile(r"成功|完成|✔|✓"), QColor("#4CAF50")),
    (re.compile(r"回退|降级|重试|取消|不完整|超时|⚠|⏸|⏳"), QColor("#FF9800")),
    (re.compile(r"正在|开始|加载|▶"), QColor("#2196F3")),
    (re.compile(r"\[\d+/\d+\]"), QColor("#00BCD4")),
]

_MAX_LOG_BLOCKS = 5000


class LogPanel(QWidget):
    """独立的日志面板，支持批量刷新和预编译正则匹配。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._log_signal = UiLogSignal()
        self._log_signal.message.connect(self._on_log_message)
        self._ui_handler = UiLogHandler(self._log_signal)

        self._pending_messages: list[str] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(100)
        self._flush_timer.timeout.connect(self._flush_messages)
        self._trim_counter = 0

        self._init_ui()
        self._attach_handlers()

    def _init_ui(self) -> None:
        """初始化日志面板 UI：带标题的分组框内嵌只读文本编辑器。"""
        group = QGroupBox("日志输出")
        layout = QVBoxLayout(group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_text)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(group)

    def _attach_handlers(self) -> None:
        """将 UiLogHandler 附加到 video2text 和 src 命名空间的 logger。"""
        for name in ("video2text", "src", "video2text.dependency"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.INFO)
            if self._ui_handler not in lg.handlers:
                lg.addHandler(self._ui_handler)

    def cleanup(self) -> None:
        """移除日志处理器，应在窗口关闭时调用。"""
        for name in ("video2text", "src", "video2text.dependency"):
            lg = logging.getLogger(name)
            if self._ui_handler in lg.handlers:
                lg.removeHandler(self._ui_handler)

    def clear(self) -> None:
        """清空日志内容。"""
        self.log_text.clear()

    def _on_log_message(self, msg: str) -> None:
        """接收日志消息并加入待刷新队列（由定时器批量刷新到 UI）。"""
        self._pending_messages.append(msg)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_messages(self) -> None:
        if not self._pending_messages:
            return
        messages = self._pending_messages
        self._pending_messages = []
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        for msg in messages:
            self._render_message(cursor, msg)
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()
        self._trim_if_needed()

    def _render_message(self, cursor: QTextCursor, msg: str) -> None:
        m_tag_step = _RE_TAG_STEP.match(msg)
        m_tag = _RE_TAG.match(msg) if not m_tag_step else None
        m_step = _RE_STEP.match(msg) if not m_tag_step else None
        m_prog = _RE_PROG.match(msg) if not (m_tag_step or m_step) else None
        m_state = _RE_STATE.match(msg) if not (m_tag_step or m_step or m_prog) else None
        m_tree = (
            _RE_TREE.match(msg)
            if not (m_tag_step or m_tag or m_step or m_prog or m_state)
            else None
        )

        if m_tag_step:
            self._insert_colored(cursor, m_tag_step.group(1), QColor("#00BCD4"))
            self._insert_colored(cursor, m_tag_step.group(2))
            ok = "✓" in m_tag_step.group(3)
            self._insert_colored(
                cursor,
                m_tag_step.group(3),
                QColor("#4CAF50") if ok else QColor("#F44336"),
            )
            self._insert_colored(cursor, m_tag_step.group(4), QColor("#757575"))
        elif m_tag:
            self._insert_colored(cursor, m_tag.group(1), QColor("#9C27B0"))
            self._insert_colored(cursor, m_tag.group(2))
        elif m_step:
            self._insert_colored(cursor, m_step.group(1), QColor("#9E9E9E"))
            self._insert_colored(cursor, m_step.group(2))
            ok = "✓" in m_step.group(3)
            self._insert_colored(
                cursor,
                m_step.group(3),
                QColor("#4CAF50") if ok else QColor("#F44336"),
            )
            self._insert_colored(cursor, m_step.group(4), QColor("#757575"))
        elif m_state:
            self._insert_colored(cursor, m_state.group(1), QColor("#9E9E9E"))
            icon = m_state.group(2)
            if icon == "▶":
                icon_color = QColor("#2196F3")
            elif icon == "⏹":
                icon_color = QColor("#F44336")
            else:
                icon_color = QColor("#FF9800")
            self._insert_colored(cursor, icon, icon_color)
            self._insert_colored(cursor, m_state.group(3))
        elif m_prog:
            self._insert_colored(cursor, m_prog.group(1), QColor("#9E9E9E"))
            self._insert_colored(cursor, m_prog.group(2))
            self._insert_colored(cursor, m_prog.group(3), QColor("#2196F3"))
            self._insert_colored(cursor, m_prog.group(4), QColor("#757575"))
        elif m_tree:
            self._insert_colored(cursor, m_tree.group(1), QColor("#9E9E9E"))
            color = self._get_log_color(m_tree.group(2))
            if color:
                fmt = QTextCharFormat()
                fmt.setForeground(color)
                cursor.setCharFormat(fmt)
            cursor.insertText(m_tree.group(2))
            if color:
                cursor.setCharFormat(QTextCharFormat())
        else:
            color = self._get_log_color(msg)
            if color:
                fmt = QTextCharFormat()
                fmt.setForeground(color)
                cursor.setCharFormat(fmt)
            cursor.insertText(msg)
            if color:
                cursor.setCharFormat(QTextCharFormat())

        cursor.insertText("\n")

    @staticmethod
    def _insert_colored(
        cursor: QTextCursor, text: str, color: Optional[QColor] = None
    ) -> None:
        if not text:
            return
        if color:
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            cursor.setCharFormat(fmt)
        cursor.insertText(text)
        if color:
            cursor.setCharFormat(QTextCharFormat())

    @staticmethod
    def _get_log_color(msg: str) -> Optional[QColor]:
        for pattern, color in _LOG_COLOR_RULES:
            if pattern.search(msg):
                return color
        return None

    def _trim_if_needed(self) -> None:
        self._trim_counter += 1
        if self._trim_counter < 100:
            return
        self._trim_counter = 0
        doc = self.log_text.document()
        if doc.blockCount() > _MAX_LOG_BLOCKS:
            trim_cursor = QTextCursor(doc)
            trim_cursor.movePosition(QTextCursor.Start)
            trim_cursor.movePosition(
                QTextCursor.Down,
                QTextCursor.KeepAnchor,
                doc.blockCount() - _MAX_LOG_BLOCKS,
            )
            trim_cursor.removeSelectedText()
