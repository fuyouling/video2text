"""TranscriptionTab 单元测试"""

from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication
import sys

from src.config.settings import Settings
from src.ui.transcription_tab import TranscriptionTab


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_transcription_tab_init_whisper(qapp):
    """测试默认 Faster-Whisper 模式初始化与可见性"""
    settings = Settings()
    settings.set("transcription.engine", "faster-whisper")
    tab = TranscriptionTab(settings)

    assert tab.get_engine() == "faster-whisper"
    assert tab._radio_whisper.isChecked() is True
    assert tab._radio_funasr.isChecked() is False
    assert tab._whisper_group.isHidden() is False
    assert tab._funasr_group.isHidden() is True
    assert "whisper_model" in tab.get_section_edits()
    assert "whisper_initial_prompt" not in tab.get_section_edits()
    assert "whisper_hotwords" not in tab.get_section_edits()
    assert "funasr_model" in tab.get_section_edits()
    assert "funasr_num_workers" in tab.get_section_edits()
    assert "funasr_compute_type" in tab.get_section_edits()
    assert "funasr_punc_filter" in tab.get_section_edits()
    assert "funasr_vad_max_end_sil" in tab.get_section_edits()
    assert "funasr_hotwords" not in tab.get_section_edits()


def test_transcription_tab_toggle_engine(qapp):
    """测试单选切换引擎与面板显隐联动"""
    settings = Settings()
    tab = TranscriptionTab(settings)

    # 切换到 FunASR
    tab._radio_funasr.setChecked(True)
    assert tab.get_engine() == "funasr"
    assert tab._whisper_group.isHidden() is True
    assert tab._funasr_group.isHidden() is False

    # 切换回 Whisper
    tab._radio_whisper.setChecked(True)
    assert tab.get_engine() == "faster-whisper"
    assert tab._whisper_group.isHidden() is False
    assert tab._funasr_group.isHidden() is True


def test_transcription_tab_set_engine(qapp):
    """测试 set_engine 方法"""
    settings = Settings()
    tab = TranscriptionTab(settings)

    tab.set_engine("funasr")
    assert tab.get_engine() == "funasr"
    assert tab._radio_funasr.isChecked() is True
    assert tab._whisper_group.isHidden() is True
    assert tab._funasr_group.isHidden() is False

    tab.set_engine("faster-whisper")
    assert tab.get_engine() == "faster-whisper"
    assert tab._radio_whisper.isChecked() is True
    assert tab._whisper_group.isHidden() is False
    assert tab._funasr_group.isHidden() is True


def test_whisper_device_compute_type_link(qapp):
    """测试 Whisper 设备选 CPU 时禁用 float16 联动"""
    settings = Settings()
    tab = TranscriptionTab(settings)

    tab._whisper_device_combo.setCurrentText("cuda")
    items = [tab._whisper_compute_combo.itemText(i) for i in range(tab._whisper_compute_combo.count())]
    assert "float16" in items

    tab._whisper_device_combo.setCurrentText("cpu")
    items_cpu = [tab._whisper_compute_combo.itemText(i) for i in range(tab._whisper_compute_combo.count())]
    assert "float16" not in items_cpu
    assert "int8" in items_cpu
    assert "float32" in items_cpu


def test_funasr_device_compute_type_link(qapp):
    """测试 FunASR 设备选 CPU 时禁用 float16 联动"""
    settings = Settings()
    tab = TranscriptionTab(settings)

    tab._funasr_device_combo.setCurrentText("cuda")
    items = [tab._funasr_compute_combo.itemText(i) for i in range(tab._funasr_compute_combo.count())]
    assert "float16" in items
    assert "int8" in items

    tab._funasr_device_combo.setCurrentText("cpu")
    items_cpu = [tab._funasr_compute_combo.itemText(i) for i in range(tab._funasr_compute_combo.count())]
    assert "float16" not in items_cpu
    assert "int8" in items_cpu
    assert "float32" in items_cpu
