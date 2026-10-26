"""AMD API 响应测试对话框单元测试（离线，不发起真实网络请求）。

覆盖：
  - fetch_amd_models 解析与过滤（仅保留 LLM / VLM，去除非文本/视觉模型）
  - test_one_model 各分支（成功/空响应/限流/超时/无 Key）
  - ModelCard 徽章与状态切换
  - AmdApiTestWorker 并发调度与优雅退出
  - AmdApiTestDialog 构建、卡片渲染与双击应用模型
"""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import src.ui.amd_api_test_dialog as dlg_mod  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.i18n import set_lang, t  # noqa: E402
from src.utils.exceptions import SummarizationError  # noqa: E402


# ── 替身与夹具 ──────────────────────────────────────────────


class SpyAmdClient:
    """替身 AmdClient：记录调用参数，按 behaviour 返回不同结果。"""

    behaviour = "ok"
    delay = 0.0
    calls: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.max_retries = 3
        self.closed = False

    def generate(self, **kwargs):
        SpyAmdClient.calls.append({"init": self.kwargs, "gen": kwargs, "retries": self.max_retries})
        if SpyAmdClient.delay:
            time.sleep(SpyAmdClient.delay)
        if SpyAmdClient.behaviour == "ok":
            return "Hello there!"
        if SpyAmdClient.behaviour == "empty":
            return "   "
        if SpyAmdClient.behaviour == "rate":
            raise SummarizationError("AMD API 错误: 429, Rate limited")
        raise SummarizationError("AMD API 请求超时")

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _zh_locale():
    set_lang("zh-CN")


@pytest.fixture(autouse=True)
def _reset_spies(monkeypatch):
    SpyAmdClient.behaviour = "ok"
    SpyAmdClient.delay = 0.0
    SpyAmdClient.calls = []
    monkeypatch.setattr(dlg_mod, "AmdClient", SpyAmdClient)
    monkeypatch.setattr(dlg_mod, "_MODEL_LIST_CACHE", None)
    monkeypatch.setattr(dlg_mod, "_MODEL_TEST_RESULTS", {})


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


# ── 测试 fetch_amd_models ────────────────────────────────────


def test_fetch_amd_models_filter_capabilities():
    """验证从 Token Factory 解析卡片并过滤出 LLM 与 VLM 模型。"""
    mock_bootstrap_resp = MagicMock()
    mock_bootstrap_resp.status_code = 200
    mock_bootstrap_resp.json.return_value = {
        "cards": [
            {"detail_url": "/model?id=1"},
            {"detail_url": "/model?id=2"},
            {"detail_url": "/model?id=3"},
            {"detail_url": "/model?id=4"},
        ]
    }

    mock_details = {
        "https://developer.amd.com.cn/model?id=1": {
            "model": {
                "model": "model-text-1",
                "token_factory": {
                    "name": "Model-Text-1",
                    "capability": {"key": "text", "label": "LLM (Text)"},
                    "publisher": {"name": "TestPub"},
                },
            }
        },
        "https://developer.amd.com.cn/model?id=2": {
            "model": {
                "model": "fallback-model",
                "token_factory": {
                    "name": "DeepSeek-V4.1-Flash-1017",
                    "capability": {"key": "vision", "label": "VLM (Vision)"},
                    "publisher": {"name": "DeepSeek"},
                    "access": {
                        "model": {
                            "label": "Model",
                            "value": "DeepSeek-V4.1-Flash",
                        }
                    },
                },
            }
        },
        "https://developer.amd.com.cn/model?id=3": {
            "model": {
                "model": "model-tts",
                "token_factory": {
                    "name": "Model-TTS",
                    "capability": {"key": "speech", "label": "TTS (Speech)"},
                },
            }
        },
        "https://developer.amd.com.cn/model?id=4": {
            "model": {
                "model": "model-ocr",
                "token_factory": {
                    "name": "Model-OCR",
                    "capability": {"key": "ocr", "label": "OCR (Document)"},
                },
            }
        },
    }

    def fake_get(url, **kwargs):
        resp = MagicMock()
        if url in mock_details:
            resp.status_code = 200
            resp.json.return_value = mock_details[url]
        else:
            resp.status_code = 404
        return resp

    with patch("requests.Session.post", return_value=mock_bootstrap_resp), patch(
        "requests.Session.get", side_effect=fake_get
    ):
        models = dlg_mod.fetch_amd_models(timeout=5)

    assert len(models) == 2
    types = {m["name"]: m["type"] for m in models}
    assert types["Model-Text-1"] == "LLM"
    assert types["DeepSeek-V4.1-Flash-1017"] == "VLM"

    vlm = [m for m in models if m["type"] == "VLM"][0]
    assert vlm["name"] == "DeepSeek-V4.1-Flash-1017"
    assert vlm["api_model"] == "DeepSeek-V4.1-Flash"


def test_fetch_amd_models_network_failure():
    """网络故障时应安全返回空列表，不抛出异常。"""
    with patch("requests.Session.post", side_effect=Exception("Connection refused")):
        models = dlg_mod.fetch_amd_models(timeout=5)
    assert models == []


# ── 测试 test_one_model ──────────────────────────────────────


def test_test_one_model_missing_key(monkeypatch):
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda k: "")
    res = dlg_mod.test_one_model("Qwen3.8-Flash-Next")
    assert not res["ok"]
    assert res["status"] == "fail"
    assert "Key" in res["error"] or "缺失" in res["error"]


def test_test_one_model_success(monkeypatch):
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda k: "fake-key")
    SpyAmdClient.behaviour = "ok"
    res = dlg_mod.test_one_model("Qwen3.8-Flash-Next")
    assert res["ok"]
    assert res["status"] == "ok"
    assert res["snippet"] == "Hello there!"
    assert res["latency"] >= 0.0


def test_test_one_model_empty_response(monkeypatch):
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda k: "fake-key")
    SpyAmdClient.behaviour = "empty"
    res = dlg_mod.test_one_model("Qwen3.8-Flash-Next")
    assert not res["ok"]
    assert res["status"] == "fail"
    assert "空" in res["error"] or "empty" in res["error"].lower()


def test_test_one_model_rate_limit(monkeypatch):
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda k: "fake-key")
    SpyAmdClient.behaviour = "rate"
    res = dlg_mod.test_one_model("Qwen3.8-Flash-Next")
    assert not res["ok"]
    assert res["status"] == "rate_limited"


def test_test_one_model_timeout(monkeypatch):
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda k: "fake-key")
    SpyAmdClient.behaviour = "ok"
    SpyAmdClient.delay = 0.05
    res = dlg_mod.test_one_model("Qwen3.8-Flash-Next", timeout=0.01)
    assert not res["ok"]
    assert res["status"] == "fail"
    assert "超时" in res["error"] or "timed out" in res["error"].lower()


# ── 测试 ModelCard ───────────────────────────────────────────


def test_model_card_ui_and_state(qapp):
    card = dlg_mod.ModelCard(
        {"name": "Qwen3.8-Flash-Next", "type": "VLM", "publisher": "Qwen"},
        dlg_mod.QIcon(),
    )
    assert card.model == "Qwen3.8-Flash-Next"
    assert card._model_type == "VLM"
    assert card.status == "pending"
    assert "VLM" in card._badge.text()

    card.set_testing()
    assert card.status == "testing"

    card.set_result("ok", 1.23, "Test Snippet")
    assert card.status == "ok"
    assert card.latency == 1.23
    assert "1.23" in card._time_label.text()
    assert card._snippet_label.text() == "Test Snippet"


def test_model_card_apply_on_double_click(qapp):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    card = dlg_mod.ModelCard(
        {"name": "DeepSeek-V4.1-Flash-1017", "api_model": "DeepSeek-V4.1-Flash", "type": "VLM", "publisher": "DeepSeek"},
        dlg_mod.QIcon(),
    )
    applied = []
    card.apply_requested.connect(lambda m: applied.append(m))

    event = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    # 验证卡片展示名与实际 API 模型分离
    assert card.display_name == "DeepSeek-V4.1-Flash-1017"
    assert card.api_model == "DeepSeek-V4.1-Flash"
    assert card.model == "DeepSeek-V4.1-Flash"
    assert "DeepSeek-V4.1-Flash-1017" in card._name_label._full_text

    # 未成功时双击不触发
    card.set_result("fail", 0.5, "", "Error")
    card.mouseDoubleClickEvent(event)
    assert applied == []

    # 成功时双击触发：传递真实 API 模型名称
    card.set_result("ok", 0.5, "Snippet")
    card.mouseDoubleClickEvent(event)
    assert applied == ["DeepSeek-V4.1-Flash"]


# ── 测试 AmdApiTestWorker 调度 ──────────────────────────────


def test_worker_submit_and_finish(qapp, monkeypatch):
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda k: "fake-key")
    worker = dlg_mod.AmdApiTestWorker(timeout=5, max_workers=2)

    results = []
    finished = []
    worker.result_ready.connect(lambda r: results.append(r))
    worker.batch_finished.connect(lambda: finished.append(True))

    worker.start()
    try:
        worker.submit(["model-1", "model-2"])
        start = time.time()
        while len(results) < 2 and time.time() - start < 3:
            qapp.processEvents()
            time.sleep(0.05)
        assert len(results) == 2
        assert len(finished) >= 1
    finally:
        worker.shutdown()
        worker.wait(1000)


# ── 测试 AmdApiTestDialog ───────────────────────────────────


def test_dialog_init_and_apply_model(qapp, monkeypatch):
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda k: "fake-key")
    # 模拟已有缓存避免触发自动拉取线程
    test_models = [
        {"name": "GLM-5.3-Flash", "type": "LLM", "publisher": "Z.ai"},
        {"name": "Qwen3.8-Flash-Next", "type": "VLM", "publisher": "Qwen"},
    ]
    monkeypatch.setattr(dlg_mod, "_MODEL_LIST_CACHE", test_models)

    settings = Settings()
    orig_model = settings.get("summarization.amd_model")
    orig_provider = settings.get("summarization.provider")
    monkeypatch.setattr(settings, "save", lambda: None)
    dialog = dlg_mod.AmdApiTestDialog(None, settings)
    try:
        assert len(dialog._cards) == 2
        assert "GLM-5.3-Flash" in dialog._cards
        assert "Qwen3.8-Flash-Next" in dialog._cards

        # 测试双击应用模型
        dialog._on_apply_model("GLM-5.3-Flash")
        assert settings.get("summarization.provider") == "amd"
        assert settings.get("summarization.amd_model") == "GLM-5.3-Flash"
        assert "GLM-5.3-Flash" in dialog._current_model_label.text()
    finally:
        if orig_model is not None:
            settings.set("summarization.amd_model", orig_model)
        if orig_provider is not None:
            settings.set("summarization.provider", orig_provider)
        dialog.close()
