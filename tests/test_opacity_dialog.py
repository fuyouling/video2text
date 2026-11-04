"""BackgroundOpacityDialog 单元测试。

验证：
1. 初始值范围限制与滑块/微调框初值绑定。
2. 滑块与微调框双向联动及实时 on_preview 回调触发。
3. 预设按钮与恢复默认按钮能正确赋值并触发预览。
4. 取消（reject）操作自动触发回调恢复至初始透明度。
5. 确认（accept）操作保留当前透明度。
6. 未设置背景图状态提示及选择背景图回调。
"""

import os
from unittest.mock import MagicMock
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.ui.opacity_dialog import BackgroundOpacityDialog


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_opacity_dialog_initialization(qapp):
    """测试对话框初始化与初始值绑定"""
    dialog = BackgroundOpacityDialog(current_value=128, has_bg_image=True)
    assert dialog.slider.value() == 128
    assert dialog.spin.value() == 128
    assert dialog.percent_label.text() == "50%"
    assert dialog.get_value() == 128
    assert abs(dialog.get_opacity() - (128 / 255.0)) < 1e-4
    assert dialog._no_bg_widget.isHidden()
    dialog.close()
    dialog.deleteLater()


def test_opacity_dialog_clamping(qapp):
    """测试超范围数值自动边界限制"""
    dialog_low = BackgroundOpacityDialog(current_value=-50)
    assert dialog_low.slider.value() == 0
    assert dialog_low.get_value() == 0
    dialog_low.close()
    dialog_low.deleteLater()

    dialog_high = BackgroundOpacityDialog(current_value=300)
    assert dialog_high.slider.value() == 255
    assert dialog_high.get_value() == 255
    dialog_high.close()
    dialog_high.deleteLater()


def test_opacity_dialog_no_bg_image(qapp):
    """测试未设置背景图时的提示条可见"""
    choose_mock = MagicMock()
    dialog = BackgroundOpacityDialog(
        current_value=100, has_bg_image=False, on_choose_image=choose_mock
    )
    assert not dialog._no_bg_widget.isHidden()

    dialog._on_choose_image_clicked()
    choose_mock.assert_called_once()
    dialog.close()
    dialog.deleteLater()


def test_opacity_dialog_slider_and_spin_sync(qapp):
    """测试滑动条与微调框双向联动以及 on_preview 实时响应"""
    preview_calls = []

    def on_preview(val: int):
        preview_calls.append(val)

    dialog = BackgroundOpacityDialog(current_value=100, on_preview=on_preview)

    # 1. 改变 slider -> spin 同步更新
    dialog.slider.setValue(180)
    assert dialog.spin.value() == 180
    assert dialog.percent_label.text() == f"{round(180 / 255 * 100)}%"
    assert 180 in preview_calls

    # 2. 改变 spin -> slider 同步更新
    dialog.spin.setValue(50)
    assert dialog.slider.value() == 50
    assert dialog.percent_label.text() == f"{round(50 / 255 * 100)}%"
    assert 50 in preview_calls

    dialog.close()
    dialog.deleteLater()


def test_opacity_dialog_presets_and_reset(qapp):
    """测试快捷预设按钮与恢复默认按钮"""
    preview_calls = []
    dialog = BackgroundOpacityDialog(
        current_value=200, on_preview=lambda v: preview_calls.append(v)
    )

    # 测试 set_value
    dialog.set_value(51)  # 20%
    assert dialog.slider.value() == 51
    assert dialog.spin.value() == 51
    assert preview_calls[-1] == 51

    # 测试恢复默认按钮
    dialog.set_value(BackgroundOpacityDialog.DEFAULT_OPACITY_VALUE)
    assert dialog.slider.value() == 100
    assert dialog.spin.value() == 100
    assert preview_calls[-1] == 100

    dialog.close()
    dialog.deleteLater()


def test_opacity_dialog_cancel_reverts_preview(qapp):
    """关键交互验证：取消操作必须自动调用 on_preview 还原初始透明度"""
    preview_calls = []
    dialog = BackgroundOpacityDialog(
        current_value=100, on_preview=lambda v: preview_calls.append(v)
    )

    # 用户拖动滑块预览
    dialog.slider.setValue(220)
    assert preview_calls[-1] == 220

    # 用户点击取消 (reject)
    dialog.reject()
    assert preview_calls[-1] == 100  # 自动还原到初始值 100

    dialog.deleteLater()


def test_opacity_dialog_accept_confirms_value(qapp):
    """测试确定操作保持当前数值"""
    preview_calls = []
    dialog = BackgroundOpacityDialog(
        current_value=100, on_preview=lambda v: preview_calls.append(v)
    )

    dialog.slider.setValue(150)
    assert dialog.get_value() == 150

    dialog.accept()
    assert dialog.get_value() == 150
    assert preview_calls[-1] == 150

    dialog.deleteLater()


def test_main_window_adjust_bg_transparency(qapp, monkeypatch):
    """测试主界面通过 BackgroundOpacityDialog 调节透明度"""
    from src.ui.gui import MainWindow

    monkeypatch.setattr(MainWindow, "_startup_dependency_check", lambda self: None)
    window = MainWindow()
    window._bg_opacity = 0.4
    window._bg_content.set_bg_opacity(0.4)

    class MockDialog:
        def __init__(self, parent=None, current_value=100, has_bg_image=True, on_preview=None, on_choose_image=None):
            self.on_preview = on_preview
            self.val = 220

        def exec(self):
            if self.on_preview:
                self.on_preview(self.val)
            return True

        def get_value(self):
            return self.val

    monkeypatch.setattr("src.ui.opacity_dialog.BackgroundOpacityDialog", MockDialog)
    save_called = False

    def mock_save():
        nonlocal save_called
        save_called = True

    monkeypatch.setattr(window, "_save_bg_config", mock_save)

    window._adjust_bg_transparency()

    assert abs(window._bg_opacity - (220 / 255.0)) < 1e-4
    assert abs(window._bg_content.bg_opacity - (220 / 255.0)) < 1e-4
    assert save_called

    window.close()
    window.deleteLater()

