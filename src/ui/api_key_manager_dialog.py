"""API Key 文件（.env）内容管理对话框。

自包含模块（解耦设计，见 plans/20260810_api_key_manage.md §3.2）：
- 文件解析 / 序列化 / 读写全部下沉到 ``src.utils.api_key_store``（纯 Python、无 Qt 依赖）。
- `ApiKeyManagerDialog` 仅负责 UI 与交互，通过菜单入口被 `gui.py` 调用，不含文件格式细节。

交互要点（与需求一致）：
- value 默认以密码掩码显示，可逐条或一键切换显示 / 隐藏。
- 已有条目的 key 为只读（不可修改），避免误改键名导致配置失效。
- 可新增（key 可编辑、带校验）、可修改 value、可删除。
- 保存时原子写入 `get_base_dir() / ".env"`，并把改动同步进 `os.environ`（运行时立即生效）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.i18n import t
from src.utils.api_key_store import EnvLine, load_env_file, save_env_file
from src.utils.logger import get_logger
from src.utils.paths import get_base_dir

logger = get_logger(__name__)

_DEFAULT_ENV_NAME = ".env"
#: 新建 `.env` 时默认预置的键（值为空，供用户填写）
_DEFAULT_ENV_KEYS: tuple = ("OLLAMA_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY")
#: 合法 key：字母 / 数字 / 下划线，且以字母或下划线开头
_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: key 的唯一性比较统一忽略大小写
_KEY_NORM = str.upper


class _ApiKeyRow(QFrame):
    """单个键值行：key（只读或可编辑）+ value（默认掩码）+ 显示切换 + 删除。"""

    def __init__(
        self,
        entry: EnvLine,
        is_new: bool,
        show_toggled: bool,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._entry = entry
        self._is_new = is_new
        self.setObjectName("apiKeyRow")
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.key_edit = QLineEdit()
        self.key_edit.setObjectName("keyEdit")
        self.key_edit.setPlaceholderText(t("api_key.key_placeholder"))
        self.key_edit.setText(entry.key)
        self.key_edit.setMinimumWidth(180)
        if not is_new:
            # 已有条目：key 不可修改（只读 + 置灰）
            self.key_edit.setReadOnly(True)
            self.key_edit.setEnabled(False)
        layout.addWidget(self.key_edit, 2)

        self.value_edit = QLineEdit()
        self.value_edit.setObjectName("valueEdit")
        self.value_edit.setPlaceholderText(t("api_key.value_placeholder"))
        self.value_edit.setText(entry.value)
        self.value_edit.setMinimumWidth(220)
        if not show_toggled:
            self.value_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.value_edit, 3)

        self.toggle_btn = QPushButton(t("api_key.show") if not show_toggled else t("api_key.hide"))
        self.toggle_btn.setObjectName("rowToggleBtn")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(show_toggled)
        self.toggle_btn.setFixedWidth(56)
        self.toggle_btn.clicked.connect(self._on_toggle)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.toggle_btn)

        self.delete_btn = QPushButton(t("api_key.delete"))
        self.delete_btn.setObjectName("rowDeleteBtn")
        self.delete_btn.setFixedWidth(48)
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.delete_btn)

    @property
    def entry(self) -> EnvLine:
        return self._entry

    @property
    def is_new(self) -> bool:
        return self._is_new

    def _on_toggle(self, checked: bool) -> None:
        self.value_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.toggle_btn.setText(t("api_key.hide") if checked else t("api_key.show"))

    def set_revealed(self, revealed: bool) -> None:
        """受工具栏「全部显示 / 隐藏」统一控制。"""
        self.value_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password
        )
        self.toggle_btn.setChecked(revealed)
        self.toggle_btn.setText(t("api_key.hide") if revealed else t("api_key.show"))


_DIALOG_QSS = """
QDialog {
    background-color: #f0f2f5;
    font-family: "Microsoft YaHei", "PingFang SC", "Segoi UI", sans-serif;
}
#headerBar, #toolBar, #footerBar {
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
}
#subtitleLabel { font-size: 12px; color: #757575; }
#pathLabel { font-size: 12px; color: #9e9e9e; }
#missingLabel { font-size: 12px; color: #d32f2f; font-weight: 600; }
#sectionLabel { font-size: 12px; font-weight: 600; color: #424242; }
#apiKeyRow {
    background: #ffffff;
    border: 1px solid #ececec;
    border-radius: 8px;
}
#keyEdit, #valueEdit {
    border: 1px solid #dcdcdc;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 13px;
    background: #fafafa;
    color: #212121;
}
#keyEdit:disabled { background: #f0f0f0; color: #9e9e9e; }
#valueEdit:focus, #keyEdit:focus { border: 1px solid #1976d2; background: #ffffff; }
#rowToggleBtn, #rowDeleteBtn {
    background: transparent;
    border: 1px solid #dcdcdc;
    border-radius: 6px;
    font-size: 12px;
    color: #616161;
}
#rowToggleBtn:hover, #rowDeleteBtn:hover { background: #f2f2f2; }
#rowDeleteBtn:hover { color: #d32f2f; border-color: #ef9a9a; }
#addBtn {
    background: transparent;
    color: #1976d2;
    border: 1px dashed #90caf9;
    border-radius: 8px;
    padding: 8px;
    font-size: 13px;
    font-weight: 500;
}
#addBtn:hover { background: #e3f2fd; }
#primaryBtn {
    background: #1976d2;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 22px;
    font-size: 13px;
    font-weight: 500;
}
#primaryBtn:hover { background: #1565c0; }
#primaryBtn:pressed { background: #0d47a1; }
#secondaryBtn {
    background: transparent;
    color: #616161;
    border: 1px solid #dcdcdc;
    border-radius: 6px;
    padding: 7px 22px;
    font-size: 13px;
    font-weight: 500;
}
#secondaryBtn:hover { background: #f2f2f2; }
#rowArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #c0c0c0; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #a0a0a0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


class ApiKeyManagerDialog(QDialog):
    """API Key（.env）管理对话框。

    菜单入口见 ``gui.py`` 的 ``_on_show_api_key_manage``。打开即加载
    ``get_base_dir() / .env``（不存在则不报错，保存时新建）。
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # 路径取项目根 / exe 所在目录（`get_base_dir` 已正确处理打包后 frozen 模式）。
        env_path = get_base_dir() / _DEFAULT_ENV_NAME
        self._env_path: Path = env_path
        self._entries: List[EnvLine]
        self._file_exists: bool
        self._entries, self._file_exists = load_env_file(env_path)
        # 文件不存在：预置三个默认键并写入 `.env`，避免保存路径歧义
        # （兼容便携版 exe 目录；get_base_dir 在 frozen 下返回 exe 所在目录）。
        if not self._file_exists:
            try:
                self._entries = [
                    EnvLine(kind="kv", key=key, value="")
                    for key in _DEFAULT_ENV_KEYS
                ]
                save_env_file(env_path, self._entries)
                self._file_exists = True
            except Exception as e:  # 目录不可写等：保留缺失提示，保存时再报错
                logger.warning("Failed to create .env at %s: %s", env_path, e)
        self._rows: List[_ApiKeyRow] = []
        self._row_map: Dict[int, _ApiKeyRow] = {}
        self._reveal_all = False

        self.setWindowTitle(t("api_key.title"))
        self.setStyleSheet(_DIALOG_QSS)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.resize(720, 560)

        self._init_ui()
        self._populate_rows()

        # Ctrl+Enter 保存
        save_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        save_shortcut.activated.connect(self._on_save)

    # ── UI 构建 ──

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addWidget(self._build_header())
        root.addWidget(self._build_tool_bar())
        root.addWidget(self._build_row_area(), 1)
        root.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("headerBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        title = QLabel(t("api_key.title"))
        title.setObjectName("sectionLabel")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #1976d2;")
        layout.addWidget(title)

        subtitle = QLabel(t("api_key.subtitle"))
        subtitle.setObjectName("subtitleLabel")
        layout.addWidget(subtitle)

        self._path_label = QLabel(
            t("api_key.path_label", path=str(self._env_path))
        )
        self._path_label.setObjectName("pathLabel")
        layout.addWidget(self._path_label)
        return bar

    def _build_tool_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("toolBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)

        section = QLabel(t("api_key.list_section"))
        section.setObjectName("sectionLabel")
        layout.addWidget(section)
        layout.addStretch(1)

        if not self._file_exists:
            missing = QLabel(t("api_key.file_missing"))
            missing.setObjectName("missingLabel")
            layout.addWidget(missing)

        self._reveal_check = QCheckBox(t("api_key.show_all"))
        self._reveal_check.setObjectName("revealCheck")
        self._reveal_check.stateChanged.connect(self._on_reveal_all)
        layout.addWidget(self._reveal_check)
        return bar

    def _build_row_area(self) -> QScrollArea:
        self._row_host = QWidget()
        self._row_host.setObjectName("rowHost")
        self._row_layout = QVBoxLayout(self._row_host)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(8)
        self._row_layout.addStretch(1)

        area = QScrollArea()
        area.setObjectName("rowArea")
        area.setWidgetResizable(True)
        area.setWidget(self._row_host)
        return area

    def _build_footer(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("footerBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        self._add_btn = QPushButton("+ " + t("api_key.add"))
        self._add_btn.setObjectName("addBtn")
        self._add_btn.clicked.connect(self._on_add)
        layout.addWidget(self._add_btn)
        layout.addStretch(1)

        buttons = QDialogButtonBox()
        self._save_btn = QPushButton(t("api_key.save"))
        self._save_btn.setObjectName("primaryBtn")
        self._save_btn.clicked.connect(self._on_save)
        self._cancel_btn = QPushButton(t("api_key.cancel"))
        self._cancel_btn.setObjectName("secondaryBtn")
        self._cancel_btn.clicked.connect(self.reject)
        buttons.addButton(self._save_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self._cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)
        return bar

    # ── 行渲染 ──

    def _populate_rows(self) -> None:
        """依据 ``self._entries`` 重建所有键值行（顶部插入，保留底部 stretch）。"""
        for entry in self._entries:
            if entry.kind == "kv":
                self._insert_row(entry, is_new=False)
        self._update_empty_hint()

    def _insert_row(self, entry: EnvLine, is_new: bool) -> _ApiKeyRow:
        row = _ApiKeyRow(entry, is_new, self._reveal_all, self._row_host)
        row.delete_btn.clicked.connect(lambda _checked=False, r=row: self._on_delete(r))
        # 插入到 stretch 之前（stretch 是布局最后一项）
        self._row_layout.insertWidget(self._row_layout.count() - 1, row)
        self._rows.append(row)
        self._row_map[id(entry)] = row
        return row

    def _update_empty_hint(self) -> None:
        has_kv = any(r.entry.kind == "kv" for r in self._rows)
        if not has_kv:
            if getattr(self, "_empty_hint", None) is None:
                self._empty_hint = QLabel(t("api_key.empty_hint"))
                self._empty_hint.setObjectName("subtitleLabel")
                self._empty_hint.setStyleSheet(
                    "color: #9e9e9e; padding: 18px 6px;"
                )
                self._row_layout.insertWidget(
                    self._row_layout.count() - 1, self._empty_hint
                )
        else:
            if getattr(self, "_empty_hint", None) is not None:
                self._empty_hint.deleteLater()
                self._empty_hint = None

    # ── 交互 ──

    def _on_reveal_all(self, state: int) -> None:
        self._reveal_all = state == Qt.CheckState.Checked.value
        for row in self._rows:
            row.set_revealed(self._reveal_all)

    def _on_add(self) -> None:
        entry = EnvLine(kind="kv", key="", value="")
        self._entries.append(entry)
        if getattr(self, "_empty_hint", None) is not None:
            self._empty_hint.deleteLater()
            self._empty_hint = None
        row = self._insert_row(entry, is_new=True)
        row.key_edit.setFocus()

    def _on_delete(self, row: _ApiKeyRow) -> None:
        entry = row.entry
        # 标记删除：序列化时跳过；新行直接丢弃，已存在行保留位置信息以便撤销不实现
        entry.kind = "deleted"
        self._row_map.pop(id(entry), None)
        if row in self._rows:
            self._rows.remove(row)
        row.deleteLater()
        self._update_empty_hint()

    def _on_save(self) -> None:
        # 1) 把 UI 上的编辑同步回 entry
        for row in self._rows:
            entry = row.entry
            if entry.kind != "kv":
                continue
            if row.is_new:
                entry.key = row.key_edit.text().strip()
            entry.value = row.value_edit.text()

        # 2) 校验新增行的 key（已有行 key 只读，无需校验）
        # 收集全部已有 key（用于去重判断，大小写不敏感）
        normalized = {
            _KEY_NORM(r.entry.key)
            for r in self._rows
            if r.entry.kind == "kv" and not r.is_new
        }
        for row in self._rows:
            entry = row.entry
            if entry.kind != "kv" or not row.is_new:
                continue
            key = entry.key
            if not key:
                self._warn_focus(row, t("api_key.error_empty_key"))
                return
            if not _KEY_PATTERN.match(key):
                self._warn_focus(row, t("api_key.error_invalid_key"))
                return
            if _KEY_NORM(key) in normalized:
                self._warn_focus(row, t("api_key.error_dup_key"))
                return
            normalized.add(_KEY_NORM(key))

        # 3) 原子写入
        try:
            save_env_file(self._env_path, self._entries)
        except Exception as e:  # 写入失败不应静默，明确提示
            logger.warning("Failed to save .env: %s", e)
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(
                self, t("api_key.title"), t("api_key.save_failed", error=str(e))
            )
            return

        # 4) 同步到当前进程环境变量（运行时立即生效）
        for row in self._rows:
            entry = row.entry
            if entry.kind == "kv" and entry.key:
                os.environ[entry.key] = entry.value

        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            self,
            t("api_key.title"),
            t("api_key.saved_msg", path=str(self._env_path)),
        )
        self.accept()

    def _warn_focus(self, row: _ApiKeyRow, message: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, t("api_key.title"), message)
        if row.is_new:
            row.key_edit.setFocus()
            row.key_edit.selectAll()
