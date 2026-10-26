"""测试转写流式输出换行逻辑"""

import pytest
from PySide6.QtWidgets import QApplication, QTextEdit
from PySide6.QtCore import QTimer
from unittest.mock import MagicMock

from src.transcription.transcriber import TranscriptSegment
from src.ui.gui import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_segment_streaming_newlines(qapp):
    # 创建 MainWindow 实例（测试环境使用已初始化的 qapp）
    # 为避免触发依赖检测，临时将 startup_dependency_check 置空
    orig_check = MainWindow._startup_dependency_check
    MainWindow._startup_dependency_check = lambda self: None
    try:
        window = MainWindow()
    finally:
        MainWindow._startup_dependency_check = orig_check

    seg1 = TranscriptSegment(
        start=7.0, end=9.0, text="第一段文本", confidence=90.0, language="zh"
    )
    seg2 = TranscriptSegment(
        start=10.0, end=15.0, text="第二段文本", confidence=90.0, language="zh"
    )
    seg3 = TranscriptSegment(
        start=16.0, end=20.0, text="第三段文本", confidence=90.0, language="zh"
    )

    video_name = "test_video"

    # 1. 发射第一段
    window._on_segment_emitted(video_name, seg1)
    # 模拟定时器清空队列
    while window._segment_text_queue:
        window._pump_segment_text()
    window._pump_segment_text()  # 触发队列空处理

    text1 = window.transcript_view.toPlainText()
    assert "[00:00:07 - 00:00:09] 第一段文本" in text1
    assert text1.endswith("\n")

    # 2. 发射第二段（此时前一段已完全排空，且队列为空）
    window._on_segment_emitted(video_name, seg2)
    while window._segment_text_queue:
        window._pump_segment_text()
    window._pump_segment_text()

    text2 = window.transcript_view.toPlainText()
    expected_lines = [
        "[00:00:07 - 00:00:09] 第一段文本",
        "[00:00:10 - 00:00:15] 第二段文本",
    ]
    actual_lines = [line for line in text2.split("\n") if line.strip()]
    assert actual_lines == expected_lines

    # 3. 故意截断末尾换行（模拟用户编辑），发射第三段仍能换行
    window.transcript_view.setPlainText(text2.rstrip())
    window._on_segment_emitted(video_name, seg3)
    while window._segment_text_queue:
        window._pump_segment_text()
    window._pump_segment_text()

    text3 = window.transcript_view.toPlainText()
    expected_lines_3 = [
        "[00:00:07 - 00:00:09] 第一段文本",
        "[00:00:10 - 00:00:15] 第二段文本",
        "[00:00:16 - 00:00:20] 第三段文本",
    ]
    actual_lines_3 = [line for line in text3.split("\n") if line.strip()]
    assert actual_lines_3 == expected_lines_3
