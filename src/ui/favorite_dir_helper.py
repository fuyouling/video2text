"""常用目录管理助手 —— 从 MainWindow 提取的收藏目录逻辑"""

import re
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QMenu,
    QMessageBox,
    QWidget,
)

from src.config.directory_manager import DirectoryManager
from src.i18n import t


class FavoriteDirHelper(QObject):
    """管理输入/输出下拉框的常用目录收藏功能。"""

    def __init__(
        self,
        dir_manager: DirectoryManager,
        input_combo: QComboBox,
        output_combo: QComboBox,
        default_output_dir: str = "output",
        status_callback: Optional[Callable[[str, int], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._dir_manager = dir_manager
        self._input_combo = input_combo
        self._output_combo = output_combo
        self._default_output_dir = default_output_dir
        self._status_cb = status_callback

        self._input_combo.view().viewport().installEventFilter(self)
        self._output_combo.view().viewport().installEventFilter(self)

    def _show_status(self, msg: str, timeout: int = 5000) -> None:
        if self._status_cb:
            self._status_cb(msg, timeout)

    def load(self) -> None:
        """从 DirectoryManager 加载常用目录到两个 QComboBox"""
        self._refresh_input_combo()
        self._refresh_output_combo()

    def _refresh_input_combo(self) -> None:
        current_text = self._input_combo.currentText()
        self._input_combo.blockSignals(True)
        self._input_combo.clear()
        for d in self._dir_manager.get_input_dirs():
            self._input_combo.addItem(d)
        if current_text:
            self._input_combo.setCurrentText(current_text)
        self._input_combo.blockSignals(False)

    def _refresh_output_combo(self) -> None:
        current_text = self._output_combo.currentText()
        self._output_combo.blockSignals(True)
        self._output_combo.clear()
        for d in self._dir_manager.get_output_dirs():
            self._output_combo.addItem(d)
        if current_text:
            self._output_combo.setCurrentText(current_text)
        else:
            self._output_combo.setCurrentText(self._default_output_dir)
        self._output_combo.blockSignals(False)

    @staticmethod
    def _extract_dir_from_input(text: str) -> str:
        # 匹配任意语言中 "路径 (N 个文件/selected)" 格式的显示文本
        m = re.match(r"^(.+?)\s*\([^)]*\d+[^)]*\)\s*$", text)
        if m:
            return m.group(1).strip()
        return text.strip()

    def fav_input_dir(self, parent: QWidget) -> None:
        raw = self._input_combo.currentText().strip()
        if not raw:
            QMessageBox.warning(parent, t("fav.hint_title"), t("fav.empty_input"))
            return
        text = self._extract_dir_from_input(raw)
        folder = str(Path(text).parent) if Path(text).is_file() else text
        self._dir_manager.add_input_dir(folder)
        self._refresh_input_combo()
        self._show_status(t("fav.fav_input_done", folder=folder))

    def fav_output_dir(self, parent: QWidget) -> None:
        text = self._output_combo.currentText().strip()
        if not text:
            QMessageBox.warning(parent, t("fav.hint_title"), t("fav.empty_output"))
            return
        self._dir_manager.add_output_dir(text)
        self._refresh_output_combo()
        self._show_status(t("fav.fav_output_done", text=text))

    def fav_both_dirs(self, parent: QWidget) -> None:
        raw_input = self._input_combo.currentText().strip()
        output_text = self._output_combo.currentText().strip()
        if not raw_input and not output_text:
            QMessageBox.warning(parent, t("fav.hint_title"), t("fav.empty_both"))
            return
        if raw_input:
            input_text = self._extract_dir_from_input(raw_input)
            folder = (
                str(Path(input_text).parent)
                if Path(input_text).is_file()
                else input_text
            )
            self._dir_manager.add_input_dir(folder)
        if output_text:
            self._dir_manager.add_output_dir(output_text)
        self._refresh_input_combo()
        self._refresh_output_combo()
        self._show_status(t("fav.fav_both_done"))

    def clear_all_input_dirs(self, parent: QWidget) -> None:
        dirs = self._dir_manager.get_input_dirs()
        if not dirs:
            self._show_status(t("fav.input_empty_already"), 3000)
            return
        reply = QMessageBox.question(
            parent,
            t("fav.confirm_clear_input_title"),
            t("fav.confirm_clear_input_msg", count=len(dirs)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._dir_manager.clear_input_dirs()
        self._refresh_input_combo()
        self._show_status(t("fav.cleared_input"))

    def clear_all_output_dirs(self, parent: QWidget) -> None:
        dirs = self._dir_manager.get_output_dirs()
        if not dirs:
            self._show_status(t("fav.output_empty_already"), 3000)
            return
        reply = QMessageBox.question(
            parent,
            t("fav.confirm_clear_output_title"),
            t("fav.confirm_clear_output_msg", count=len(dirs)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._dir_manager.clear_output_dirs()
        self._refresh_output_combo()
        self._show_status(t("fav.cleared_output"))

    def _remove_input_favorite(self, path: str) -> None:
        self._dir_manager.remove_input_dir(path)
        self._refresh_input_combo()
        self._show_status(t("fav.removed_input", path=path), 3000)

    def _remove_output_favorite(self, path: str) -> None:
        self._dir_manager.remove_output_dir(path)
        self._refresh_output_combo()
        self._show_status(t("fav.removed_output", path=path), 3000)

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.RightButton
        ):
            combo = None
            remove_fn = None
            if obj is self._input_combo.view().viewport():
                combo = self._input_combo
                remove_fn = self._remove_input_favorite
            elif obj is self._output_combo.view().viewport():
                combo = self._output_combo
                remove_fn = self._remove_output_favorite

            if combo is not None:
                view = combo.view()
                index = view.indexAt(event.position().toPoint())
                if index.isValid():
                    path = index.data(Qt.ItemDataRole.DisplayRole)
                    if path:
                        menu = QMenu(self.parent())
                        delete_action = menu.addAction(t("fav.context_delete", path=path))
                        action = menu.exec(event.globalPosition().toPoint())
                        if action == delete_action:
                            remove_fn(path)
                return True

        return super().eventFilter(obj, event)
