"""Wave 1 - Track B 全套工具库、安全与系统兼容修复专项单元测试"""

import datetime
import email.utils
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.api_key_store import EnvLine, parse_env, serialize_env, save_env_file, load_env_file
from src.utils.compat import patch_av_open
from src.utils.dll_downloader import DllDownloader, DLL_REQUIRED_FILES
from src.utils.env_loader import ensure_env_loaded, get_api_key
from src.utils.json_utils import safe_read_json, atomic_write_json
from src.utils.logger import (
    SafeRotatingFileHandler,
    get_shared_rotating_handler,
    mask_sensitive_data,
    setup_logger,
)
from src.utils.model_downloader import (
    ModelDownloader,
    CORE_FILE_MIN_SIZES,
    check_models_integrity,
)
from src.utils.path_resolver import (
    sanitize_filename,
    to_long_path,
    is_path_too_long,
    resolve_output_path,
    validate_output_dir,
)
from src.utils.proxy_detect import _normalize_proxy, detect_system_proxy, _detect_windows_proxy
from src.utils.rate_limit import RateLimiter, get_retry_after, exponential_backoff
from src.utils.subprocess_compat import get_no_window_kwargs, CREATE_NO_WINDOW
from src.utils.validators import validate_executable_path
from src.utils.exceptions import ConfigurationError


# ── 1. dll_downloader ─────────────────────────────────────────

def test_dll_downloader_sha256(tmp_path):
    downloader = DllDownloader()
    downloader.libs_dir = tmp_path
    archive = tmp_path / "test.7z"
    archive.write_bytes(b"sample bytes for 7z")
    downloader.archive_path = archive

    hash_val = downloader.compute_sha256(archive)
    assert len(hash_val) == 64
    assert downloader.verify_archive(hash_val) is True
    assert downloader.verify_archive("123456") is False


def test_dll_downloader_check_dlls_in_use(tmp_path):
    downloader = DllDownloader()
    downloader.libs_dir = tmp_path
    test_dll = tmp_path / "cublas64_12.dll"
    test_dll.write_bytes(b"binary content")

    # 未被锁定时返回空列表
    assert downloader.check_dlls_in_use() == []

    # 模拟真实占用状态（抛出 PermissionError）
    orig_open = open

    def mock_open(path, *args, **kwargs):
        if str(path) == str(test_dll):
            raise PermissionError("Access denied")
        return orig_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=mock_open):
        res = downloader.check_dlls_in_use()
        assert res == ["cublas64_12.dll"]


# ── 2. model_downloader ───────────────────────────────────────

def test_model_downloader_min_size_and_clean(tmp_path):
    downloader = ModelDownloader("large-v3")
    downloader.models_dir = tmp_path

    # 创建所有核心文件，但 model.bin 仅 50MB（小于 100MB 下限）
    for f in downloader.model_config["core_files"]:
        fp = tmp_path / f
        if f == "model.bin":
            fp.write_bytes(b"x" * 1024)  # 远远小于 100MB
        else:
            fp.write_bytes(b"mock content")

    # is_model_exists 应判定为 False
    assert downloader.is_model_exists() is False

    # 清理不完整文件
    downloader._clean_incomplete_core_files()
    assert not (tmp_path / "model.bin").exists()
    assert (tmp_path / "config.json").exists()


def test_paraformer_model_downloader_vad_min_size(tmp_path):
    """测试 FunASR paraformer-large-vad-punc 模型中 vad/model_quant.onnx 的尺寸判定逻辑。"""
    downloader = ModelDownloader("paraformer-large-vad-punc")
    downloader.models_dir = tmp_path

    # 创建所有核心文件，其中 vad/model_quant.onnx 约为 500KB（正常 int8 权重）
    for f in downloader.model_config["core_files"]:
        fp = tmp_path / f
        fp.parent.mkdir(parents=True, exist_ok=True)
        if f in ("asr/model_quant.onnx", "punc/model_quant.onnx"):
            fp.write_bytes(b"x" * (51 * 1024 * 1024))
        elif f == "vad/model_quant.onnx":
            fp.write_bytes(b"x" * (500 * 1024))  # ~500KB，满足 >100KB 下限
        else:
            fp.write_bytes(b"mock")

    assert downloader.is_model_exists() is True

    # 若 vad 模型被截断（例如仅 50KB 残片），应判定为 False
    (tmp_path / "vad" / "model_quant.onnx").write_bytes(b"x" * (50 * 1024))
    assert downloader.is_model_exists() is False


def test_get_model_display_name():
    """测试模型名称友好格式化函数"""
    from src.utils.model_downloader import get_model_display_name
    from src.i18n import t

    assert "FunASR" in get_model_display_name("paraformer-large-vad-punc")
    assert "Faster-Whisper" in get_model_display_name("faster-whisper-large-v3-turbo-ct2")
    assert get_model_display_name("custom-model") == "custom-model"
    assert get_model_display_name("") == ""

    # 测试 i18n 消息格式化
    msg = t("main.confirm_download_msg", model=get_model_display_name("paraformer-large-vad-punc"))
    assert "FunASR" in msg
    assert "网盘" in msg
    assert "HuggingFace" not in msg
    assert "models 目录" in msg


def test_model_downloader_check_integrity_config_flag(monkeypatch):
    class MockSettings:
        def __init__(self):
            self.data = {"app.is_check_model_file": "true"}

        def get_bool(self, key, default):
            return self.data.get(key, str(default)).lower() == "true"

        def get(self, key, default):
            return self.data.get(key, default)

        def set(self, key, val):
            self.data[key] = val

    settings = MockSettings()

    # 模拟 download 失败（all_ok is False）
    with patch.object(ModelDownloader, "is_model_exists", return_value=False), \
         patch.object(ModelDownloader, "download_model", return_value=False):
        ok = check_models_integrity(settings, target_models=["large-v3"])
        assert ok is False
        # all_ok is False 时不得置为 "false"
        assert settings.data.get("app.is_check_model_file") == "true"

    # 模拟 download 成功（all_ok is True）
    with patch.object(ModelDownloader, "is_model_exists", return_value=True):
        ok = check_models_integrity(settings, target_models=["large-v3"])
        assert ok is True
        # all_ok is True 时置为 "false"
        assert settings.data.get("app.is_check_model_file") == "false"


# ── 3. api_key_store ──────────────────────────────────────────

def test_api_key_store_crlf_and_export():
    raw = (
        "export_var=hello\n"
        "export REAL_KEY=world\n"
        "NORMAL=ok\n"
    )
    lines = parse_env(raw)
    assert lines[0].key == "export_var"
    assert lines[1].key == "REAL_KEY"

    entries = [
        EnvLine(kind="kv", key="TEST\r\nKEY", value="val\r\nue", vendor="my\r\nvendor")
    ]
    serialized = serialize_env(entries)
    assert "\r\nKEY" not in serialized
    assert "\r\nue" not in serialized
    assert "TESTKEY=" in serialized
    assert '\\r\\nue' in serialized
    assert "my vendor" in serialized


def test_api_key_store_save_atomic(tmp_path):
    env_path = tmp_path / ".env"
    entries = [EnvLine(kind="kv", key="A", value="1")]
    save_env_file(env_path, entries)
    loaded, exists = load_env_file(env_path)
    assert exists is True
    assert loaded[0].key == "A"
    assert loaded[0].value == "1"


# ── 4. proxy_detect ───────────────────────────────────────────

def test_proxy_detect_normalize():
    assert _normalize_proxy("127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert _normalize_proxy("127.0.0.1:1080", default_scheme="socks5://") == "socks5://127.0.0.1:1080"
    assert _normalize_proxy("socks://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert _normalize_proxy("https://127.0.0.1:8080") == "https://127.0.0.1:8080"
    assert _normalize_proxy("") == ""


def test_proxy_detect_windows_pac():
    # 模拟注册表检测 PAC 脚本
    mock_key = MagicMock()
    mock_key.__enter__.return_value = mock_key
    mock_key.__exit__.return_value = None

    def query_val(key, subkey):
        if subkey == "ProxyEnable":
            return (0, 4)
        if subkey == "AutoConfigURL":
            return ("http://127.0.0.1:8989/proxy.pac", 1)
        raise OSError()

    with patch("src.utils.proxy_detect.winreg", create=True) as mock_winreg:
        mock_winreg.HKEY_CURRENT_USER = 1
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.side_effect = query_val
        proxy = _detect_windows_proxy()
        assert proxy == "http://127.0.0.1:8989/proxy.pac"


# ── 5. logger ─────────────────────────────────────────────────

def test_logger_mask_sensitive_data():
    raw_text = "Connecting to API with key sk-1234567890abcdef and Bearer secret_token_xyz_12345"
    masked = mask_sensitive_data(raw_text)
    assert "sk-1234567890abcdef" not in masked
    assert "secret_token_xyz_12345" not in masked
    assert "Bearer ***MASKED***" in masked

    generic_text = '{"api_key": "super_secret_api_key_value"}'
    masked_generic = mask_sensitive_data(generic_text)
    assert "super_secret_api_key_value" not in masked_generic


def test_logger_safe_rotating_rollover(tmp_path):
    log_file = tmp_path / "test.log"
    handler = SafeRotatingFileHandler(log_file, maxBytes=100, backupCount=1)

    # 模拟在 rollover 时抛出 PermissionError（WinError 32）
    with patch.object(SafeRotatingFileHandler, "rotate", side_effect=PermissionError("WinError 32")):
        # 不应抛出异常
        handler.doRollover()

    handler.close()


def test_logger_shared_pool(tmp_path):
    log_file = tmp_path / "shared.log"
    h1 = get_shared_rotating_handler(log_file)
    h2 = get_shared_rotating_handler(log_file)
    assert h1 is h2
    h1.close()


# ── 6. validators ─────────────────────────────────────────────

def test_validators_executable_path(tmp_path):
    # 非可执行文件应被拒绝
    txt = tmp_path / "notes.txt"
    txt.write_text("just text")
    with pytest.raises(ConfigurationError):
        validate_executable_path(str(txt))

    # Python 可执行文件应通过
    res = validate_executable_path("python")
    assert res is not None


# ── 7. path_resolver ──────────────────────────────────────────

def test_path_resolver_windows_reserved():
    # 保留设备名应加上前缀转换
    for reserved in ["con", "prn", "aux", "nul", "com1", "lpt1"]:
        cleaned = sanitize_filename(reserved)
        assert cleaned.startswith("_")
        assert reserved.upper() in cleaned.upper()

    cleaned_ext = sanitize_filename("con.txt")
    assert cleaned_ext.startswith("_")


def test_path_resolver_long_path():
    short_p = "C:\\short\\path"
    assert to_long_path(short_p) == short_p

    if os.name == "nt":
        long_p = "C:\\" + "a" * 260
        extended = to_long_path(long_p)
        assert extended.startswith("\\\\?\\")


# ── 8. rate_limit ─────────────────────────────────────────────

def test_rate_limit_retry_after():
    # 纯数字
    assert get_retry_after({"Retry-After": "15"}) == 15.0

    # RFC 7231 HTTP-Date
    future_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=45)
    date_str = email.utils.format_datetime(future_date)
    val = get_retry_after({"Retry-After": date_str})
    assert val is not None
    assert 40 <= val <= 50

    # 无效格式
    assert get_retry_after({"Retry-After": "invalid-date"}) is None


def test_rate_limit_exponential_backoff_max():
    for attempt in range(1, 20):
        val = exponential_backoff(attempt, base=2.0, max_backoff=60.0)
        assert val <= 60.0


def test_rate_limiter_acquire_no_lock_sleep():
    limiter = RateLimiter(min_interval=0.1)
    start = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.09


# ── 9. subprocess_compat ──────────────────────────────────────

def test_subprocess_compat():
    kwargs = get_no_window_kwargs()
    if sys.platform == "win32":
        assert "creationflags" in kwargs
        assert kwargs["creationflags"] == CREATE_NO_WINDOW
    else:
        assert kwargs == {}


# ── 10. compat ────────────────────────────────────────────────

def test_compat_wraps():
    import av
    def orig_fn(*args, **kwargs):
        """Docstring for orig"""
        return "ok"

    old_open = av.open
    try:
        av.open = orig_fn
        patch_av_open()
        assert av.open.__name__ == "orig_fn"
        assert av.open.__doc__ == "Docstring for orig"
    finally:
        av.open = old_open


# ── 11. json_utils ────────────────────────────────────────────

def test_json_utils_corruption_isolation_and_restore(tmp_path):
    target_file = tmp_path / "data.json"
    bak_file = tmp_path / "data.json.bak"

    valid_data = {"key": "saved_value"}
    bak_file.write_text(json.dumps(valid_data), encoding="utf-8")

    # 写入损坏的 JSON
    target_file.write_text("{ broken json content", encoding="utf-8")

    # safe_read_json 遇到 JSONDecodeError 时应隔离并从 .bak 恢复
    result = safe_read_json(target_file, default={})
    assert result == valid_data

    # 验证隔离的 .corrupted.<timestamp>.bak 备份文件存在
    corrupted_backups = list(tmp_path.glob("data.json.corrupted.*.bak"))
    assert len(corrupted_backups) == 1
    assert corrupted_backups[0].read_text(encoding="utf-8") == "{ broken json content"


def test_json_utils_atomic_write(tmp_path):
    target = tmp_path / "atomic.json"
    data = {"test": 123}
    atomic_write_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data


# ── 12. env_loader ────────────────────────────────────────────

def test_env_loader_base_dir_priority(tmp_path, monkeypatch):
    base_env = tmp_path / ".env"
    base_env.write_text("PRIORITY_TEST_KEY=from_base_dir\n", encoding="utf-8")

    monkeypatch.setattr("src.utils.paths.get_base_dir", lambda: tmp_path)
    if hasattr(ensure_env_loaded, "_loaded"):
        delattr(ensure_env_loaded, "_loaded")

    ensure_env_loaded()
    assert get_api_key("PRIORITY_TEST_KEY") == "from_base_dir"
