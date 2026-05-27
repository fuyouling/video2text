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
        m = re.match(r"^(.+?)\s*\(已选择\s+\d+\s*个文件\)\s*$", text)
        if m:
            return m.group(1).strip()
        return text.strip()

    def fav_input_dir(self, parent: QWidget) -> None:
        raw = self._input_combo.currentText().strip()
        if not raw:
            QMessageBox.warning(parent, "提示", "输入框为空，无法收藏。")
            return
        text = self._extract_dir_from_input(raw)
        folder = str(Path(text).parent) if Path(text).is_file() else text
        self._dir_manager.add_input_dir(folder)
        self._refresh_input_combo()
        self._show_status(f"已收藏输入目录: {folder}")

    def fav_output_dir(self, parent: QWidget) -> None:
        text = self._output_combo.currentText().strip()
        if not text:
            QMessageBox.warning(parent, "提示", "输出框为空，无法收藏。")
            return
        self._dir_manager.add_output_dir(text)
        self._refresh_output_combo()
        self._show_status(f"已收藏输出目录: {text}")

    def fav_both_dirs(self, parent: QWidget) -> None:
        raw_input = self._input_combo.currentText().strip()
        output_text = self._output_combo.currentText().strip()
        if not raw_input and not output_text:
            QMessageBox.warning(parent, "提示", "输入和输出框均为空，无法收藏。")
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
        self._show_status("已收藏输入和输出目录")

    def clear_all_input_dirs(self, parent: QWidget) -> None:
        dirs = self._dir_manager.get_input_dirs()
        if not dirs:
            self._show_status("输入目录列表已为空", 3000)
            return
        reply = QMessageBox.question(
            parent,
            "确认清空",
            f"确定要移除所有 {len(dirs)} 个常用输入目录吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._dir_manager.clear_input_dirs()
        self._refresh_input_combo()
        self._show_status("已移除所有常用输入目录")

    def clear_all_output_dirs(self, parent: QWidget) -> None:
        dirs = self._dir_manager.get_output_dirs()
        if not dirs:
            self._show_status("输出目录列表已为空", 3000)
            return
        reply = QMessageBox.question(
            parent,
            "确认清空",
            f"确定要移除所有 {len(dirs)} 个常用输出目录吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._dir_manager.clear_output_dirs()
        self._refresh_output_combo()
        self._show_status("已移除所有常用输出目录")

    def _remove_input_favorite(self, path: str) -> None:
        self._dir_manager.remove_input_dir(path)
        self._refresh_input_combo()
        self._show_status(f"已移除常用输入目录: {path}", 3000)

    def _remove_output_favorite(self, path: str) -> None:
        self._dir_manager.remove_output_dir(path)
        self._refresh_output_combo()
        self._show_status(f"已移除常用输出目录: {path}", 3000)

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
                        delete_action = menu.addAction(f"删除「{path}」")
                        action = menu.exec(event.globalPosition().toPoint())
                        if action == delete_action:
                            remove_fn(path)
                return True

        return super().eventFilter(obj, event)
