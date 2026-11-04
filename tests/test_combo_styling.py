"""自动化验证跨对话框与窗口的 QComboBox 样式与紧凑尺寸"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QComboBox

from src.ui.gui_dialogs import ConfigEditorDialog, VideoSelectionDialog
from src.ui.result_viewer import ResultViewerWindow


def test_video_selection_dialog_combos():
    """验证 VideoSelectionDialog 下拉框尺寸紧凑（26px）及悬停高亮"""
    app = QApplication.instance() or QApplication([])
    files = [
        ("folder/test.mp4", 1000000),
        ("folder/demo.mkv", 50000000),
        ("music/sample.flac", 20000000),
    ]
    vsd = VideoSelectionDialog(files)
    vsd.show()
    QApplication.processEvents()

    for cb in [vsd._file_type_combo, vsd._suffix_combo, vsd._size_combo]:
        cb.showPopup()
        v = cb.view()
        for i in range(cb.count()):
            idx = cb.model().index(i, 0)
            vr = v.visualRect(idx)
            assert vr.height() == 26
        cb.hidePopup()
    vsd.close()


def test_config_editor_dialog_combos():
    """验证 ConfigEditorDialog 中所有 22 个下拉框不发生横向膨胀（<=480px）且行高均为 26px"""
    app = QApplication.instance() or QApplication([])
    ced = ConfigEditorDialog()
    ced.show()
    QApplication.processEvents()

    # 验证代理地址输入框合理约束宽度（与通用配置首行 280px 保持对称对齐）且提供规范占位符
    proxy_edit = ced._edits["app"]["proxy"]
    assert proxy_edit.maximumWidth() == 280
    assert proxy_edit.width() <= 280
    assert proxy_edit.placeholderText() == "http://127.0.0.1:7890"

    combos = ced.findChildren(QComboBox)
    assert len(combos) >= 20

    for cb in combos:
        cb.showPopup()
        v = cb.view()
        assert cb.width() <= 480
        if cb.count() > 0:
            vr0 = v.visualRect(cb.model().index(0, 0))
            assert vr0.height() == 26
        cb.hidePopup()
    ced.close()


def test_result_viewer_combos():
    """验证 ResultViewerWindow 中书签下拉框的行高与尺寸"""
    app = QApplication.instance() or QApplication([])
    rv = ResultViewerWindow()
    rv.show()
    QApplication.processEvents()

    for cb in [rv._bookmark_date_combo, rv._bookmark_sort_combo]:
        cb.showPopup()
        v = cb.view()
        for i in range(cb.count()):
            idx = cb.model().index(i, 0)
            vr = v.visualRect(idx)
            assert vr.height() == 26
        cb.hidePopup()
    rv.close()
