"""AboutDialog 单元测试。

验证：
1. AboutDialog 能够成功初始化，且展示最新的描述信息、技术栈、版本与 QQ交流群号。
2. 点击「复制」按钮时，剪贴板内容被设置为群号 "296875960"。
3. 复制按钮呈现即时状态反馈（文本变为包含 "已复制"），复原逻辑正常。
4. 语言切换时（如 en、zh-TW），文案正常本地化。
"""

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.i18n import set_lang, t
from src.ui.about_dialog import AboutDialog


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_about_dialog_init(qapp):
    """测试关于对话框构建与组件内容正确性"""
    set_lang("zh-CN")
    dialog = AboutDialog()

    assert "关于" in dialog.windowTitle()
    assert bool(dialog.windowFlags() & Qt.WindowType.WindowCloseButtonHint)
    assert dialog.qq_number == "296875960"
    assert dialog.qq_val_label.text() == "296875960"
    assert dialog.copy_btn.text() == "复制"
    assert "faster-whisper" in t("about.desc")
    assert "FunASR" in t("about.desc")
    assert "FunASR" in t("about.tech_value")
    assert t("about.qq_group_label") == "QQ交流群"
    dialog.close()


def test_about_dialog_copy_action(qapp):
    """测试点击复制按钮后，剪贴板和按钮状态的变更"""
    set_lang("zh-CN")
    dialog = AboutDialog()

    clipboard = QApplication.clipboard()
    if clipboard:
        clipboard.clear()

    # 点击复制
    dialog.copy_btn.click()

    # 验证剪贴板写入了群号
    if clipboard:
        assert clipboard.text() == "296875960"

    # 验证按钮文字提示已复制
    assert "已复制" in dialog.copy_btn.text()

    # 测试复原按钮
    dialog._reset_copy_btn()
    assert dialog.copy_btn.text() == "复制"

    dialog.close()


def test_about_dialog_english(qapp):
    """测试英文环境下的文案展示"""
    set_lang("en")
    dialog = AboutDialog()

    assert "About" in dialog.windowTitle()
    assert dialog.qq_number == "296875960"
    assert dialog.copy_btn.text() == "Copy"
    assert t("about.qq_group_label") == "QQ Group"

    dialog.copy_btn.click()
    assert "Copied" in dialog.copy_btn.text()

    dialog._reset_copy_btn()
    assert dialog.copy_btn.text() == "Copy"

    dialog.close()
    set_lang("zh-CN")

