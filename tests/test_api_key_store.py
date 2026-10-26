"""API Key Store 与管理对话框的单元测试。"""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from src.i18n import set_lang
from src.ui.api_key_manager_dialog import (
    ApiKeyManagerDialog,
    _ApiKeyRow,
    suggest_key_from_vendor,
)
from src.utils.api_key_store import (
    EnvLine,
    infer_vendor_name,
    load_env_file,
    parse_env,
    save_env_file,
    serialize_env,
)
from src.utils.env_loader import ensure_env_loaded, get_api_key


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def _zh_locale():
    set_lang("zh-CN")


def test_infer_vendor_name():
    assert infer_vendor_name("OLLAMA_API_KEY") == "Ollama"
    assert infer_vendor_name("NVIDIA_API_KEY") == "NVIDIA"
    assert infer_vendor_name("MISTRAL_API_KEY") == "Mistral AI"
    assert infer_vendor_name("AMD_API_KEY") == "AMD Radeon Cloud"
    assert infer_vendor_name("OPENAI_API_KEY") == "OpenAI"
    assert infer_vendor_name("DEEPSEEK_API_KEY") == "DeepSeek"
    assert infer_vendor_name("SILICONFLOW_API_KEY") == "SiliconFlow"
    assert infer_vendor_name("CUSTOM_MODEL_API_KEY") == "Custom Model"
    assert infer_vendor_name("SOME_KEY") == "Some"


def test_suggest_key_from_vendor():
    assert suggest_key_from_vendor("Ollama") == "OLLAMA_API_KEY"
    assert suggest_key_from_vendor("nvidia") == "NVIDIA_API_KEY"
    assert suggest_key_from_vendor("DeepSeek") == "DEEPSEEK_API_KEY"
    assert suggest_key_from_vendor("SiliconFlow") == "SILICONFLOW_API_KEY"
    assert suggest_key_from_vendor("My Custom Provider") == "MY_CUSTOM_PROVIDER_API_KEY"
    assert suggest_key_from_vendor("") == ""


def test_parse_env_basic_and_comments():
    raw_text = (
        "# Comment line\n"
        "\n"
        'OLLAMA_API_KEY="" # Ollama\n'
        'NVIDIA_API_KEY="sk-12345" # NVIDIA\n'
        "AMD_API_KEY=rc-999 # AMD Radeon Cloud\n"
        'MISTRAL_API_KEY="mistral-secret"\n'
        "UNKNOWN_API_KEY=foo\n"
    )
    entries = parse_env(raw_text)
    assert len(entries) == 7

    assert entries[0].kind == "comment"
    assert entries[0].raw == "# Comment line"

    assert entries[1].kind == "blank"

    assert entries[2].kind == "kv"
    assert entries[2].key == "OLLAMA_API_KEY"
    assert entries[2].value == ""
    assert entries[2].vendor == "Ollama"

    assert entries[3].kind == "kv"
    assert entries[3].key == "NVIDIA_API_KEY"
    assert entries[3].value == "sk-12345"
    assert entries[3].vendor == "NVIDIA"

    assert entries[4].kind == "kv"
    assert entries[4].key == "AMD_API_KEY"
    assert entries[4].value == "rc-999"
    assert entries[4].vendor == "AMD Radeon Cloud"

    # MISTRAL_API_KEY 没有行尾注释，自动根据 key 推断出 "Mistral AI"
    assert entries[5].kind == "kv"
    assert entries[5].key == "MISTRAL_API_KEY"
    assert entries[5].value == "mistral-secret"
    assert entries[5].vendor == "Mistral AI"

    # UNKNOWN_API_KEY 自动推断为 "Unknown"
    assert entries[6].kind == "kv"
    assert entries[6].key == "UNKNOWN_API_KEY"
    assert entries[6].value == "foo"
    assert entries[6].vendor == "Unknown"


def test_serialize_env_with_vendor():
    entries = [
        EnvLine(kind="comment", raw="# Header"),
        EnvLine(kind="blank", raw=""),
        EnvLine(kind="kv", key="OLLAMA_API_KEY", value="", vendor="Ollama"),
        EnvLine(kind="kv", key="NVIDIA_API_KEY", value="sk-test", vendor="NVIDIA"),
        EnvLine(kind="kv", key="CUSTOM_KEY", value="secret", vendor=""),
        EnvLine(kind="deleted", key="DEL_KEY", value="del", vendor="Del"),
    ]
    serialized = serialize_env(entries)
    expected = (
        "# Header\n"
        "\n"
        'OLLAMA_API_KEY="" # Ollama\n'
        "NVIDIA_API_KEY=sk-test # NVIDIA\n"
        "CUSTOM_KEY=secret\n"
    )
    assert serialized == expected


def test_save_and_load_roundtrip(tmp_path):
    env_file = tmp_path / ".env"
    initial_entries = [
        EnvLine(kind="comment", raw="# Configuration file"),
        EnvLine(kind="kv", key="OLLAMA_API_KEY", value="", vendor="Ollama"),
        EnvLine(kind="kv", key="AMD_API_KEY", value="rc-secret", vendor="AMD Radeon Cloud"),
    ]
    save_env_file(env_file, initial_entries)

    loaded, exists = load_env_file(env_file)
    assert exists is True
    assert len(loaded) == 3
    assert loaded[1].key == "OLLAMA_API_KEY"
    assert loaded[1].vendor == "Ollama"
    assert loaded[2].key == "AMD_API_KEY"
    assert loaded[2].value == "rc-secret"
    assert loaded[2].vendor == "AMD Radeon Cloud"


def test_env_loader_strips_vendor_comments(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'TEST_ENV_KEY="my-secret-token" # My Vendor\n'
        'ANOTHER_KEY=simple-token # Another Vendor\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    setattr(ensure_env_loaded, "_loaded", False)

    ensure_env_loaded()
    assert os.environ.get("TEST_ENV_KEY") == "my-secret-token"
    assert os.environ.get("ANOTHER_KEY") == "simple-token"


def test_api_key_dialog_ui(qapp, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'OLLAMA_API_KEY="" # Ollama\n'
        'NVIDIA_API_KEY="sk-test" # NVIDIA\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("src.ui.api_key_manager_dialog.get_base_dir", lambda: tmp_path)

    dlg = ApiKeyManagerDialog()
    assert len(dlg._rows) == 2

    # 第一行：Ollama
    row0 = dlg._rows[0]
    assert row0.vendor_edit.text() == "Ollama"
    assert row0.vendor_edit.isReadOnly() is False
    assert row0.key_edit.text() == "OLLAMA_API_KEY"
    assert row0.key_edit.isReadOnly() is True  # 已有 key 只读
    assert row0.value_edit.text() == ""

    # 第二行：NVIDIA
    row1 = dlg._rows[1]
    assert row1.vendor_edit.text() == "NVIDIA"
    assert row1.key_edit.text() == "NVIDIA_API_KEY"
    assert row1.value_edit.text() == "sk-test"

    # 新增一行
    dlg._on_add()
    assert len(dlg._rows) == 3
    new_row = dlg._rows[2]
    assert new_row.is_new is True
    assert new_row.key_edit.isReadOnly() is False

    # 联动测试：在新增行的厂商名输入 DeepSeek，自动建议 key 为 DEEPSEEK_API_KEY
    new_row.vendor_edit.setText("DeepSeek")
    new_row.vendor_edit.textEdited.emit("DeepSeek")
    assert new_row.key_edit.text() == "DEEPSEEK_API_KEY"

    # 修改已有行的厂商名（如修改 Ollama 为 Ollama Local）
    row0.vendor_edit.setText("Ollama Local")

    # 保存
    new_row.value_edit.setText("sk-deepseek-123")
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *args: None)
    dlg._on_save()

    # 验证保存后的 .env 文件内容
    reloaded, exists = load_env_file(env_file)
    assert exists is True
    assert len(reloaded) == 3
    assert reloaded[0].vendor == "Ollama Local"
    assert reloaded[1].vendor == "NVIDIA"
    assert reloaded[2].key == "DEEPSEEK_API_KEY"
    assert reloaded[2].vendor == "DeepSeek"
    assert reloaded[2].value == "sk-deepseek-123"

    # 验证运行时环境变量已注入
    assert os.environ.get("DEEPSEEK_API_KEY") == "sk-deepseek-123"

    dlg.close()
