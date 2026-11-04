"""VideoSelectionDialog 搜索与交互测试"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from src.i18n import available_languages, set_lang, t
from src.ui.gui_dialogs import VideoSelectionDialog, _SearchLineEdit


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_search_line_edit_key_press_event(qapp):
    """验证 _SearchLineEdit 拦截回车键并发送 returnPressed，不冒泡触发 QDialog 默认按钮。"""
    edit = _SearchLineEdit()
    emitted = []
    edit.returnPressed.connect(lambda: emitted.append(True))

    ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r")
    edit.keyPressEvent(ev)

    assert ev.isAccepted()
    assert len(emitted) == 1


def test_search_placeholder_multilingual():
    """验证所有 9 种语言中的搜索框占位符文本同步修改。"""
    expected = {
        "zh-CN": "输入关键字",
        "zh-TW": "輸入關鍵字",
        "en": "Enter keyword",
        "de": "Schlüsselwort eingeben",
        "es": "Ingrese palabra clave",
        "fr": "Entrez un mot-clé",
        "ja": "キーワードを入力",
        "ko": "키워드 입력",
        "ru": "Введите ключевое слово",
    }
    for lang, exp in expected.items():
        set_lang(lang)
        assert t("dialogs.file_select.search_placeholder") == exp
    set_lang("zh-CN")


def test_selection_dialog_search_on_enter(qapp):
    """验证 VideoSelectionDialog 搜索功能回车后正确过滤文件。"""
    metas = [
        ("C:/videos/doudian/intro.mp4", 1024),
        ("C:/videos/GPT-SoVITS/整合包教程.mp4", 2048),
        ("C:/videos/剪映教程/2025-10-15_【基础篇】主轨常用按钮.m4a", 4096),
        ("C:/videos/剪映教程/2025-10-15_【基础篇】关键帧.m4a", 8192),
    ]
    dialog = VideoSelectionDialog(metas)
    dialog.show()
    qapp.processEvents()

    assert dialog._search_edit.placeholderText() == "输入关键字"
    assert len(dialog.get_selected_files()) == 4

    # 输入关键字并回车
    dialog._search_edit.setText("基础篇")
    ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r")
    qapp.sendEvent(dialog._search_edit, ev)
    qapp.processEvents()

    selected = dialog.get_selected_files()
    assert len(selected) == 2
    assert all("基础篇" in f for f in selected)

    # 清空输入框应恢复所有文件选中
    dialog._search_edit.clear()
    qapp.processEvents()
    assert len(dialog.get_selected_files()) == 4

    # 搜索相对目录名
    dialog._search_edit.setText("GPT-SoVITS")
    qapp.sendEvent(dialog._search_edit, ev)
    qapp.processEvents()
    selected = dialog.get_selected_files()
    assert len(selected) == 1
    assert "整合包教程.mp4" in selected[0]

    dialog.close()


def test_selection_dialog_scanning_rebuild_preserves_filter(qapp):
    """验证扫描过程中或渐进加载时重建树不会覆盖当前搜索过滤。"""
    dialog = VideoSelectionDialog([], scanning=True)
    dialog.show()
    qapp.processEvents()

    # 预先输入搜索关键字
    dialog._search_edit.setText("基础篇")

    # 模拟后台添加文件
    dialog.add_file("C:/test/folder/a.mp4", 100)
    dialog.add_file("C:/test/folder/【基础篇】01.mp4", 200)
    dialog.finish_scan()

    while dialog._loading_timer.isActive() or dialog._pending_timer.isActive():
        qapp.processEvents()

    selected = dialog.get_selected_files()
    assert len(selected) == 1
    assert "【基础篇】01.mp4" in selected[0]

    dialog.close()


def test_selection_dialog_numeric_search(qapp):
    """验证输入纯数字时的搜索行为：按独立数字片段匹配，避免 2026 误匹配 02。"""
    metas = [
        ("C:/0make/video2text/video/test/2026-06-21/2026-06-21_test01.mp4", 100),
        ("C:/0make/video2text/video/test/2026-06-22/2026-06-22_test02.mp4", 200),
        ("C:/0make/video2text/video/test/2026-06-23/2026-06-23_test03.mp4", 300),
        ("C:/0make/video2text/video/test/2026-06-24/2026-06-24_test04.mp4", 400),
        ("C:/0make/video2text/video/test/2026-06-25/2026-06-25_test05.mp4", 500),
    ]
    dialog = VideoSelectionDialog(metas)
    dialog.show()
    qapp.processEvents()

    ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r")

    # 1. 搜索 "02" 只应匹配 test02，不应因 2026 包含 02 而全选
    dialog._search_edit.setText("02")
    qapp.sendEvent(dialog._search_edit, ev)
    qapp.processEvents()
    sel = dialog.get_selected_files()
    assert len(sel) == 1
    assert "2026-06-22_test02.mp4" in sel[0]

    # 2. 搜索单个数字 "2" 同理只匹配 test02
    dialog._search_edit.setText("2")
    qapp.sendEvent(dialog._search_edit, ev)
    qapp.processEvents()
    sel = dialog.get_selected_files()
    assert len(sel) == 1
    assert "2026-06-22_test02.mp4" in sel[0]

    # 3. 搜索年份 "2026" 匹配所有 5 个文件
    dialog._search_edit.setText("2026")
    qapp.sendEvent(dialog._search_edit, ev)
    qapp.processEvents()
    assert len(dialog.get_selected_files()) == 5

    # 4. 搜索日期 "20260622"
    dialog._search_edit.setText("20260622")
    qapp.sendEvent(dialog._search_edit, ev)
    qapp.processEvents()
    sel = dialog.get_selected_files()
    assert len(sel) == 1
    assert "2026-06-22_test02.mp4" in sel[0]

    dialog.close()

