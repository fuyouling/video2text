"""Nvidia API 响应测试对话框单元测试（离线，不发起真实网络请求）。

覆盖：模型常量、test_one_model 各分支（成功/空响应/限流/超时/无 Key）、
卡片状态切换、对话框构建与进度统计、测试线程的并发调度与退出。
"""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import src.ui.nvidia_api_test_dialog as dlg_mod  # noqa: E402
from src.i18n import set_lang, t  # noqa: E402
from src.utils.exceptions import SummarizationError  # noqa: E402


# ── 替身与夹具 ──────────────────────────────────────────────


class SpyClient:
    """替身 NvidiaClient：记录调用参数，按 behaviour 返回不同结果。"""

    behaviour = "ok"
    delay = 0.0
    calls: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.max_retries = 3
        self.closed = False

    def generate(self, **kwargs):
        SpyClient.calls.append({"init": self.kwargs, "gen": kwargs, "retries": self.max_retries})
        if SpyClient.delay:
            time.sleep(SpyClient.delay)
        if SpyClient.behaviour == "ok":
            return "今天天气晴朗，适合外出散步。" * 5
        if SpyClient.behaviour == "empty":
            return "   "
        if SpyClient.behaviour == "rate":
            raise SummarizationError("NVIDIA API 限流 (429), 4秒后重试")
        raise SummarizationError("NVIDIA API 请求超时")

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _zh_locale():
    set_lang("zh-CN")


@pytest.fixture(autouse=True)
def _no_auto_fetch(monkeypatch):
    """测试中不发起真实的模型列表网络拉取，并把缓存预填为离线常量。

    这样既有 27 张卡片的测试在缓存命中场景下行为不变；
    想验证「空缓存 / 自动拉取」的场景只需在用例里手动重置缓存。
    """
    monkeypatch.setattr(
        dlg_mod.NvidiaApiTestDialog, "_start_model_fetch", lambda self, notify=False: None
    )
    # 隔离模块级状态（缓存列表 / 已测试模型结果），保证每个用例独立
    dlg_mod._MODEL_LIST_CACHE = list(dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS)
    dlg_mod._MODEL_TEST_RESULTS.clear()


@pytest.fixture
def spy(monkeypatch):
    """用替身 client 接管网络请求，并伪造一个可用的 API Key。"""
    SpyClient.behaviour = "ok"
    SpyClient.delay = 0.0
    SpyClient.calls = []
    monkeypatch.setattr(dlg_mod, "NvidiaClient", SpyClient)
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda _name: "fake-key")
    return SpyClient


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


# ── 1. 模型常量 ─────────────────────────────────────────────


def test_model_list_matches_plan():
    """27 个文本总结可用模型，无重复，均为 publisher/name 形式。"""
    models = dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS
    assert len(models) == 27
    assert len(set(models)) == 27
    assert all(m.count("/") == 1 and m == m.strip() for m in models)
    assert "openai/gpt-oss-120b" in models
    assert "z-ai/glm-5.2" in models


def test_constants():
    assert dlg_mod.TEST_TIMEOUT == 30
    assert dlg_mod.MAX_CONCURRENCY == 5


def test_fetch_returns_empty_on_no_match(monkeypatch):
    """NGC 目录无命中时直接返回空列表，不再回退离线常量。"""
    monkeypatch.setattr(dlg_mod, "_NGC_PAGE_COUNT", 1)
    monkeypatch.setattr(
        dlg_mod, "_query_ngc_page",
        lambda page, timeout: [
            {"resourceId": "x/y", "name": "y",
             "labels": [
                 {"key": "nimType", "unresolvedValues": ["nim_type_preview"]},
                 {"key": "publisher", "unresolvedValues": ["openai"]},
                 {"key": "general", "values": ["vision image"]},  # 被 EXCLUDE 命中
             ]},
        ],
    )
    assert dlg_mod.fetch_text_summary_models() == []


def test_fetch_returns_empty_on_network_error(monkeypatch):
    """网络异常时返回空列表，由 UI 给出空状态提示。"""
    def boom(page, timeout):
        raise RuntimeError("network down")
    monkeypatch.setattr(dlg_mod, "_NGC_PAGE_COUNT", 1)
    monkeypatch.setattr(dlg_mod, "_query_ngc_page", boom)
    assert dlg_mod.fetch_text_summary_models() == []


# ── 2. test_one_model 各分支 ────────────────────────────────


def test_missing_api_key_skips_request(monkeypatch):
    """API Key 缺失时直接判失败，且不发起任何请求。"""
    SpyClient.calls = []
    monkeypatch.setattr(dlg_mod, "NvidiaClient", SpyClient)
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda _name: "")

    result = dlg_mod.test_one_model("openai/gpt-oss-120b")

    assert result["ok"] is False
    assert result["status"] == "fail"
    assert result["error"] == t("nvidia_test.error_no_key")
    assert SpyClient.calls == []


def test_success_branch(spy):
    result = dlg_mod.test_one_model("openai/gpt-oss-120b")

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["latency"] >= 0
    # 摘要片段截断到 SNIPPET_LENGTH 并追加省略号
    assert len(result["snippet"]) == dlg_mod.SNIPPET_LENGTH + 1
    assert result["snippet"].endswith("…")


def test_request_uses_minimal_payload_and_no_retry(spy):
    dlg_mod.test_one_model("openai/gpt-oss-120b", timeout=30, api_url="https://example/x")

    call = spy.calls[-1]
    assert call["retries"] == 1, "测试场景必须关闭重试，避免单次失败拖到 90s"
    assert call["init"]["timeout"] == 30
    assert call["init"]["api_url"] == "https://example/x"
    assert call["gen"]["max_tokens"] == dlg_mod.TEST_MAX_TOKENS
    assert call["gen"]["stream"] is False
    assert call["gen"]["model"] == "openai/gpt-oss-120b"


def test_empty_response_is_failure(spy):
    spy.behaviour = "empty"
    result = dlg_mod.test_one_model("m/empty")
    assert result["status"] == "fail"
    assert result["error"] == t("nvidia_test.error_empty")


def test_rate_limited_branch(spy):
    spy.behaviour = "rate"
    result = dlg_mod.test_one_model("m/rate")
    assert result["status"] == "rate_limited"
    assert result["ok"] is False


def test_timeout_branch(spy):
    spy.behaviour = "timeout"
    result = dlg_mod.test_one_model("m/timeout")
    assert result["status"] == "fail"


def test_latency_over_limit_is_failure(spy):
    """底层未抛 Timeout 时，逻辑层仍按 latency > timeout 兜底判失败。"""
    spy.delay = 0.3
    result = dlg_mod.test_one_model("m/slow", timeout=0)
    assert result["status"] == "fail"
    assert result["ok"] is False


@pytest.mark.parametrize(
    "message, expected",
    [
        ("NVIDIA API 限流 (429), 4秒后重试", True),
        ("NVIDIA API rate limited (429), retrying in 4s", True),
        ("NVIDIA API ограничение скорости (429)", True),
        ("NVIDIA API 错误: 500", False),
        ("NVIDIA API 请求超时", False),
    ],
)
def test_rate_limit_detection(message, expected):
    assert dlg_mod._is_rate_limit_error(message) is expected


# ── 3. 卡片 ─────────────────────────────────────────────────


def test_model_card_states(qapp):
    from PySide6.QtGui import QIcon

    card = dlg_mod.ModelCard("openai/gpt-oss-120b", QIcon())
    assert card.status == "pending" and card.latency is None

    card.set_queued()
    assert card.status == "queued"
    card.set_testing()
    assert card.status == "testing"

    card.set_result("ok", 1.234, "今天天气晴朗。")
    assert card.status == "ok"
    assert card.latency == pytest.approx(1.234)
    assert "1.23" in card._time_label.text()

    card.set_result("rate_limited", 2.0, "", "限流")
    assert card.status == "rate_limited"

    card.set_result("fail", 30.0, "", "超时")
    assert ">" in card._time_label.text(), "达到上限时应显示 >30s"

    card.set_result("bogus-status", 1.0, "")
    assert card.status == "fail", "未知状态回退为 fail"


def test_model_card_retest_signal(qapp):
    from PySide6.QtGui import QIcon

    card = dlg_mod.ModelCard("openai/gpt-oss-20b", QIcon())
    received = []
    card.retest_clicked.connect(received.append)
    card._retest_btn.click()
    assert received == ["openai/gpt-oss-20b"]


def test_model_card_shows_publisher_and_name(qapp):
    from PySide6.QtGui import QIcon

    card = dlg_mod.ModelCard("nvidia/llama-3_3-nemotron-super-49b-v1_5", QIcon())
    assert card._publisher_label.text() == "nvidia"
    assert card._name_label.full_text() == "llama-3_3-nemotron-super-49b-v1_5"
    assert card.toolTip().startswith("nvidia/llama-3_3-nemotron-super-49b-v1_5")


def test_long_error_does_not_grow_card(qapp):
    """长错误信息被截断，卡片高度保持不变，完整内容留在 tooltip。"""
    from PySide6.QtGui import QIcon

    card = dlg_mod.ModelCard("openai/gpt-oss-120b", QIcon())
    card.show()
    qapp.processEvents()
    height = card.height()

    long_error = "NVIDIA API 错误: 500, " + "详细报错信息" * 30
    card.set_result("fail", 2.0, "", long_error)
    qapp.processEvents()

    assert len(card._snippet_label.text()) <= dlg_mod._MAX_DETAIL_LENGTH + 1
    assert card._snippet_label.toolTip() == long_error
    assert card.height() == height
    card.close()


# ── 4. 对话框 ───────────────────────────────────────────────


def test_dialog_builds_cards(qapp):
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        assert dialog.windowTitle() == t("nvidia_test.title")
        assert len(dialog._cards) == 27
        assert set(dialog._cards) == set(dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS)
        assert not dialog._refresh_icon.isNull(), "assets/refresh.png 应存在"
        assert 1 <= dialog._max_workers <= dlg_mod.MAX_CONCURRENCY
        assert dialog._worker is None, "未点击测试前不应创建后台线程"
        assert "0/27" in dialog._progress_label.text()
    finally:
        dialog.close()


def test_tested_status_persists_across_reopen(qapp):
    """已测试模型的结果应在再次打开对话框时保留（进程内）。"""
    dlg1 = dlg_mod.NvidiaApiTestDialog()
    try:
        dlg1._on_result(
            {"model": "openai/gpt-oss-120b", "ok": True, "status": "ok",
             "latency": 1.5, "snippet": "摘要片段", "error": ""}
        )
        assert dlg1._cards["openai/gpt-oss-120b"].status == "ok"
    finally:
        dlg1.close()

    dlg2 = dlg_mod.NvidiaApiTestDialog()
    try:
        card = dlg2._cards["openai/gpt-oss-120b"]
        assert card.status == "ok"
        assert card.latency == pytest.approx(1.5)
        # 未测试的模型仍为待测试
        assert dlg2._cards["openai/gpt-oss-20b"].status == "pending"
    finally:
        dlg2.close()


def test_dialog_cards_are_uniform_and_not_clipped(qapp):
    """所有卡片等高，且任何窗口宽度下模型名都不会被硬裁切（超长则中间省略）。"""
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        for width in (1020, 820):
            dialog.resize(width, 720)
            dialog.show()
            for _ in range(3):
                qapp.processEvents()

            assert len({c.height() for c in dialog._cards.values()}) == 1, "卡片应等高"
            for card in dialog._cards.values():
                label = card._name_label
                shown = label.text()
                need = label.fontMetrics().horizontalAdvance(shown)
                assert need <= label.width(), f"{card.model} 在 {width}px 下被裁切"
                if shown != label.full_text():
                    assert "…" in shown, "省略时应显示省略号"
    finally:
        dialog.close()


def test_dialog_progress_and_busy_state(qapp):
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        dialog._on_model_started("openai/gpt-oss-120b")
        assert dialog._cards["openai/gpt-oss-120b"].status == "testing"

        dialog._on_result(
            {"model": "openai/gpt-oss-120b", "ok": True, "status": "ok",
             "latency": 0.9, "snippet": "摘要", "error": ""}
        )
        dialog._on_result(
            {"model": "openai/gpt-oss-20b", "ok": False, "status": "rate_limited",
             "latency": 1.0, "snippet": "", "error": "429"}
        )
        text = dialog._progress_label.text()
        assert "2/27" in text and "成功 1" in text and "限流 1" in text

        dialog._set_busy(True)
        assert not dialog._test_all_btn.isEnabled() and dialog._stop_btn.isEnabled()
        dialog._set_busy(False)
        assert dialog._test_all_btn.isEnabled() and not dialog._stop_btn.isEnabled()
    finally:
        dialog.close()


def test_dialog_stop_resets_queued_cards(qapp):
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        first, second = dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS[0], dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS[1]
        dialog._cards[first].set_queued()
        dialog._cards[second].set_testing()

        dialog._on_stop()

        assert dialog._cards[first].status == "pending", "未开始的卡片应回到待测试"
        assert dialog._cards[second].status == "testing", "已在途的卡片保持测试中"
        assert not dialog._test_all_btn.isEnabled(), "仍有在途请求时保持忙碌"
    finally:
        dialog.close()


def test_dialog_stop_before_any_request_restores_buttons(qapp):
    """停止时若无在途请求，worker 不会再发结束信号，按钮必须立即恢复。"""
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        for card in dialog._cards.values():
            card.set_queued()
        dialog._set_busy(True)

        dialog._on_stop()

        assert all(c.status == "pending" for c in dialog._cards.values())
        assert dialog._test_all_btn.isEnabled()
        assert not dialog._stop_btn.isEnabled()
    finally:
        dialog.close()


def test_batch_finished_keeps_busy_when_retest_pending(qapp):
    """上一批结束信号不应打断紧随其后提交的单卡重测。"""
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        dialog._cards[dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS[0]].set_queued()
        dialog._on_batch_finished()
        assert not dialog._test_all_btn.isEnabled()

        dialog._cards[dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS[0]].set_pending()
        dialog._on_batch_finished()
        assert dialog._test_all_btn.isEnabled()
    finally:
        dialog.close()


def test_dialog_without_api_key_warns(qapp, monkeypatch):
    """无 API Key 时不创建线程、不发请求，仅提示用户。"""
    monkeypatch.setattr(dlg_mod, "get_api_key", lambda _name: "")
    warned = []
    monkeypatch.setattr(
        dlg_mod.QMessageBox, "warning", lambda *a, **k: warned.append(a[2])
    )

    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        dialog._on_test_all()
        assert warned == [t("nvidia_test.api_key_missing_msg")]
        assert dialog._worker is None
    finally:
        dialog.close()


def test_dialog_shows_empty_label_when_no_models(qapp):
    """缓存已确认为空（拉取结果）时展示「无可用在线模型」空状态并禁用一键测试。"""
    dlg_mod._MODEL_LIST_CACHE = []
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        assert dialog._cards == {}
        assert dialog._empty_label is not None
        assert dialog._empty_label.text() == t("nvidia_test.models_empty")
        assert not dialog._test_all_btn.isEnabled(), "无可测模型时一键测试必须禁用"
    finally:
        dialog.close()


def test_dialog_first_open_shows_loading_label(qapp, monkeypatch):
    """进程内首次打开（缓存为 None）展示「正在获取…」占位，不触发空状态。"""
    dlg_mod._MODEL_LIST_CACHE = None
    monkeypatch.setattr(
        dlg_mod.NvidiaApiTestDialog, "_start_model_fetch",
        lambda self, notify=False: None,
    )
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        assert dialog._cards == {}
        assert dialog._empty_label is not None
        assert dialog._empty_label.text() == t("nvidia_test.models_loading")
        assert not dialog._test_all_btn.isEnabled()
    finally:
        dialog.close()


def test_dialog_reuses_model_cache_across_reopens(qapp, monkeypatch):
    """同一进程内第二次打开对话框复用首次拉取的列表，不再自动联网。"""
    online_only = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    dlg_mod._MODEL_LIST_CACHE = list(online_only)
    started = []
    monkeypatch.setattr(
        dlg_mod.NvidiaApiTestDialog, "_start_model_fetch",
        lambda self, notify=False: started.append(("called", notify)),
    )

    dlg1 = dlg_mod.NvidiaApiTestDialog()
    try:
        assert set(dlg1._cards) == set(online_only)
        assert started == []
    finally:
        dlg1.close()

    dlg2 = dlg_mod.NvidiaApiTestDialog()
    try:
        assert set(dlg2._cards) == set(online_only)
        assert started == [], "第二次进入不应再触发自动拉取"
    finally:
        dlg2.close()


def test_dialog_first_open_triggers_fetch_when_cache_empty(qapp, monkeypatch):
    """进程内首次进入（缓存为 None）应触发后台拉取，并把结果写入缓存。"""
    dlg_mod._MODEL_LIST_CACHE = None
    called = []
    fake_models = ["meta/llama-3.2-1b-instruct"]
    monkeypatch.setattr(
        dlg_mod.NvidiaApiTestDialog, "_start_model_fetch",
        lambda self, notify=False: called.append(notify),
    )

    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        # 首次进入：自动后台拉取且不弹窗
        assert called == [False]
        # 模拟拉取成功回填缓存
        dialog._on_models_ready(fake_models)
        assert dlg_mod._MODEL_LIST_CACHE == fake_models
        # 再次进入应直接复用缓存，不再触发拉取
        called.clear()
    finally:
        dialog.close()

    dialog2 = dlg_mod.NvidiaApiTestDialog()
    try:
        assert called == []
        assert set(dialog2._cards) == set(fake_models)
    finally:
        dialog2.close()


def test_models_ready_writes_cache_and_renders(qapp):
    """_on_models_ready 把拉取结果（含空列表）写入进程缓存并重建卡片。"""
    dlg_mod._MODEL_LIST_CACHE = list(dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS)
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        online = ["openai/gpt-oss-120b"]
        dialog._on_models_ready(online)
        assert dlg_mod._MODEL_LIST_CACHE == online
        assert set(dialog._cards) == set(online)
    finally:
        dialog.close()


def test_models_fetch_failed_writes_empty_cache(qapp):
    """_on_models_fetch_failed 把空列表写入缓存，避免下次重复触发同一次失败。"""
    dlg_mod._MODEL_LIST_CACHE = None
    dialog = dlg_mod.NvidiaApiTestDialog()
    try:
        dialog._on_models_fetch_failed("boom")
        assert dlg_mod._MODEL_LIST_CACHE == []
        assert dialog._cards == {}
        assert dialog._empty_label is not None
    finally:
        dialog.close()


# ── 5. 测试线程 ─────────────────────────────────────────────


def test_worker_schedules_all_models(qapp, spy):
    worker = dlg_mod.NvidiaApiTestWorker(timeout=5, max_workers=5)
    started, results, batches = [], [], []
    worker.model_started.connect(started.append)
    worker.result_ready.connect(results.append)
    worker.batch_finished.connect(lambda: batches.append(1))
    worker.start()
    try:
        worker.submit(list(dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not batches:
            qapp.processEvents()
            time.sleep(0.02)

        assert len(started) == 27
        assert len(results) == 27
        assert len(batches) == 1, "一批任务只应发出一次结束信号"
        assert all({"model", "ok", "status", "latency"} <= set(r) for r in results)
        assert {r["model"] for r in results} == set(dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS)
    finally:
        worker.shutdown()
        assert worker.wait(5000)


def test_worker_cancel_drops_pending(qapp, spy):
    """cancel() 后未开始的任务被丢弃，回报数量少于提交数量。"""
    spy.delay = 0.2
    worker = dlg_mod.NvidiaApiTestWorker(timeout=5, max_workers=1)
    results = []
    worker.result_ready.connect(results.append)
    worker.start()
    try:
        worker.submit(list(dlg_mod._FALLBACK_TEXT_SUMMARY_MODELS))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not results:
            qapp.processEvents()
            time.sleep(0.02)
        worker.cancel()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)
        assert 0 < len(results) < 27
    finally:
        worker.shutdown()
        assert worker.wait(5000)
