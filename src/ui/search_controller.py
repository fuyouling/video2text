"""搜索替换控制器 —— 从 MainWindow 提取的查找/替换逻辑"""

from typing import Callable, Optional

from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class SearchController(QWidget):
    """可复用的搜索替换面板，适用于任何 QTextEdit。"""

    def __init__(
        self,
        get_active_edit: Callable[[], QTextEdit],
        clear_all_highlights: Callable[[], None],
        on_replace_count: Optional[Callable[[int], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._get_active_edit = get_active_edit
        self._clear_all_highlights = clear_all_highlights
        self._on_replace_count = on_replace_count

        self._match_positions: list[tuple[int, int]] = []
        self._current_match_index: int = -1

        self._init_ui()
        self.setVisible(False)

    def _init_ui(self) -> None:
        """初始化搜索替换面板 UI：查找输入框、上/下导航按钮、替换输入框、全部替换按钮。"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 4, 0, 0)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("查找:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("输入搜索内容…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.returnPressed.connect(self._find_next)
        self._search_input.textChanged.connect(self._find_all_matches)
        search_row.addWidget(self._search_input, 1)

        prev_btn = QPushButton("▲")
        prev_btn.setFixedWidth(32)
        prev_btn.setToolTip("上一个")
        prev_btn.clicked.connect(self._find_prev)
        search_row.addWidget(prev_btn)

        next_btn = QPushButton("▼")
        next_btn.setFixedWidth(32)
        next_btn.setToolTip("下一个 (Enter)")
        next_btn.clicked.connect(self._find_next)
        search_row.addWidget(next_btn)

        self._count_label = QLabel("")
        self._count_label.setMinimumWidth(90)
        search_row.addWidget(self._count_label)

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(28)
        close_btn.setToolTip("关闭搜索栏")
        close_btn.clicked.connect(self.hide)
        search_row.addWidget(close_btn)

        main_layout.addLayout(search_row)

        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("替换:"))
        self._replace_input = QLineEdit()
        self._replace_input.setPlaceholderText("替换为…")
        replace_row.addWidget(self._replace_input, 1)

        replace_btn = QPushButton("替换")
        replace_btn.setMinimumWidth(60)
        replace_btn.clicked.connect(self._replace_current)
        replace_row.addWidget(replace_btn)

        replace_all_btn = QPushButton("全部替换")
        replace_all_btn.setMinimumWidth(80)
        replace_all_btn.clicked.connect(self._replace_all)
        replace_row.addWidget(replace_all_btn)

        main_layout.addLayout(replace_row)

    # ── 公开接口 ──

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        super().setVisible(True)
        self._search_input.setFocus()
        cursor = self._get_active_edit().textCursor()
        if cursor.hasSelection():
            self._search_input.setText(cursor.selectedText())
        elif self._search_input.text():
            self._find_all_matches()

    def hide(self) -> None:
        if not self.isVisible():
            return
        super().setVisible(False)
        self._clear_all_highlights()
        self._match_positions = []
        self._current_match_index = -1

    def clear_state(self) -> None:
        self._match_positions = []
        self._current_match_index = -1
        self._search_input.clear()
        self._count_label.setText("")

    def refresh_if_active(self) -> None:
        """标签页切换或内容加载后调用，刷新搜索结果。"""
        if self.isVisible() and self._search_input.text():
            self._find_all_matches()

    # ── 内部逻辑 ──

    def _find_all_matches(self) -> None:
        self._clear_all_highlights()
        self._match_positions = []
        self._current_match_index = -1

        search_text = self._search_input.text()
        if not search_text:
            self._count_label.setText("")
            return

        edit = self._get_active_edit()
        document = edit.document()
        cursor = QTextCursor(document)

        while True:
            cursor = document.find(search_text, cursor)
            if cursor.isNull():
                break
            start = cursor.selectionStart()
            end = cursor.selectionEnd()
            if start == end:
                cursor.setPosition(end + 1)
                continue
            self._match_positions.append((start, end))
            cursor.setPosition(end)

        count = len(self._match_positions)
        if count == 0:
            self._count_label.setText("未找到匹配")
            return

        current_pos = edit.textCursor().position()
        idx = 0
        for i, (start, _) in enumerate(self._match_positions):
            if start >= current_pos:
                idx = i
                break

        self._current_match_index = idx
        self._goto_match(idx)

    def _highlight_all_matches(self) -> None:
        normal_color = QColor(255, 255, 100)
        current_color = QColor(255, 140, 0)
        edit = self._get_active_edit()
        document = edit.document()
        selections = []
        for i, (start, end) in enumerate(self._match_positions):
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(
                current_color if i == self._current_match_index else normal_color
            )
            cur = QTextCursor(document)
            cur.setPosition(start)
            cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            selections.append(sel)
        edit.setExtraSelections(selections)

    def _goto_match(self, index: int) -> None:
        if (
            not self._match_positions
            or index < 0
            or index >= len(self._match_positions)
        ):
            return
        self._current_match_index = index
        start, end = self._match_positions[index]
        edit = self._get_active_edit()
        cursor = edit.textCursor()
        cursor.setPosition(start)
        edit.setTextCursor(cursor)
        edit.ensureCursorVisible()
        self._highlight_all_matches()
        total = len(self._match_positions)
        self._count_label.setText(f"{index + 1} / {total}")

    def _find_next(self) -> None:
        if not self._match_positions:
            self._find_all_matches()
            return
        self._current_match_index = (self._current_match_index + 1) % len(
            self._match_positions
        )
        self._goto_match(self._current_match_index)

    def _find_prev(self) -> None:
        if not self._match_positions:
            self._find_all_matches()
            return
        self._current_match_index = (self._current_match_index - 1) % len(
            self._match_positions
        )
        self._goto_match(self._current_match_index)

    def _replace_current(self) -> None:
        if not self._match_positions or self._current_match_index < 0:
            return
        replace_text = self._replace_input.text()
        start, end = self._match_positions[self._current_match_index]
        edit = self._get_active_edit()
        cursor = edit.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(replace_text)
        cursor.endEditBlock()
        self._find_all_matches()

    def _replace_all(self) -> None:
        search_text = self._search_input.text()
        replace_text = self._replace_input.text()
        if not search_text:
            return
        edit = self._get_active_edit()
        document = edit.document()
        edit_cursor = QTextCursor(document)
        edit_cursor.beginEditBlock()
        count = 0
        pos = 0
        while True:
            found = document.find(search_text, pos)
            if found.isNull():
                break
            edit_cursor.setPosition(found.selectionStart())
            edit_cursor.setPosition(
                found.selectionEnd(), QTextCursor.MoveMode.KeepAnchor
            )
            edit_cursor.insertText(replace_text)
            count += 1
            pos = edit_cursor.position()
        edit_cursor.endEditBlock()
        self._match_positions = []
        self._current_match_index = -1
        self._clear_all_highlights()
        if count > 0:
            self._count_label.setText(f"已替换 {count} 处")
            if self._on_replace_count:
                self._on_replace_count(count)
        else:
            self._count_label.setText("未找到匹配")
