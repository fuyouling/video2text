"""AMD API 响应测试对话框 —— 批量探测「文本与视觉可用模型」的可用性与延迟。

自包含模块（解耦设计，参考 nvidia_api_test_dialog.py）：
  - `fetch_amd_models()`：通过 request 方式访问 https://developer.amd.com.cn/radeon/tokenfactory
    拉取 Token Factory 目录，并发解析各卡片详情，筛选获取 LLM (Text) 与 VLM (Vision) 模型。
    在线拉取失败或结果为空时返回空列表，由 UI 给出「无可用在线模型」空状态提示，不回退离线常量。
  - `test_one_model()`：复用 `AmdClient` 发送轻量探测请求（Prompt: "Hi", max_tokens: 10），15s 超时即判失败。
  - `ModelCard`：卡片式 UI（状态色条 + [LLM]/[VLM] 徽章 + 响应时间 + 响应片段 + 右上角重测按钮）。
  - `_ModelListFetcher`：常驻 QThread，后台拉取模型列表，到位后重建卡片。
  - `AmdApiTestWorker`：常驻 QThread，内部线程池并发探测，结果经信号回主线程。
  - `AmdApiTestDialog`：对话框本体，`gui.py` 仅通过菜单入口调用，不含任何业务逻辑。

模型列表缓存（`_MODEL_LIST_CACHE`）：
  - 仅在进程内有效，生命周期为「本次软件打开」；
  - 首次进入对话框时由后台拉取填充（含空列表，表示拉取成功但无模型）；
  - 之后再次进入对话框直接复用，避免每次打开都发起 Token Factory 网络请求；
  - 用户仍可随时点工具栏「刷新列表」手动更新。
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import requests
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings
from src.i18n import t
from src.summarization.amd_client import AmdClient
from src.utils.env_loader import get_api_key
from src.utils.logger import get_logger
from src.utils.paths import get_base_dir

logger = get_logger(__name__)


# ============================================================
# 常量与配置
# ============================================================

#: Token Factory 页面与 API 接口地址
_AMD_TOKEN_FACTORY_URL = "https://developer.amd.com.cn/radeon/tokenfactory"
_AMD_BOOTSTRAP_URL = "https://developer.amd.com.cn/radeon/api/tokenfactory/bootstrap?directory=true"
_AMD_MODEL_DETAIL_BASE = "https://developer.amd.com.cn"
_AMD_REQUEST_HEADERS = {
    "Referer": _AMD_TOKEN_FACTORY_URL,
    "Origin": "https://developer.amd.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

#: 轻量探测超时：15秒
TEST_TIMEOUT = 15
#: 极简探测 Prompt 与 Token 上限
TEST_PROMPT = "Hi"
TEST_MAX_TOKENS = 10
#: 默认并发探测数
DEFAULT_CONCURRENCY = 4
#: 卡片内展示的返回片段长度
SNIPPET_LENGTH = 40
#: 卡片内展示的错误信息最大长度（完整内容留在 tooltip 中）
_MAX_DETAIL_LENGTH = 60

_DEFAULT_API_URL = "https://developer.amd.com.cn/radeon/api/v1/chat/completions"
_CARD_COLUMNS = 3
_POLL_INTERVAL = 0.1
_IDLE_POLL_INTERVAL = 0.2

#: 状态 -> (前景色, 背景色)
_STATUS_COLORS: Dict[str, tuple] = {
    "pending": ("#9e9e9e", "#f5f5f5"),
    "queued": ("#7e57c2", "#ede7f6"),
    "testing": ("#1976d2", "#e3f2fd"),
    "ok": ("#388e3c", "#e8f5e9"),
    "fail": ("#d32f2f", "#ffebee"),
    "rate_limited": ("#f57c00", "#fff3e0"),
}

_STATUS_TEXT_KEYS: Dict[str, str] = {
    "pending": "amd_test.status_pending",
    "queued": "amd_test.status_queued",
    "testing": "amd_test.status_testing",
    "ok": "amd_test.status_ok",
    "fail": "amd_test.status_fail",
    "rate_limited": "amd_test.status_rate_limited",
}

#: 已结束（不再变化）的终态
_FINAL_STATUSES = ("ok", "fail", "rate_limited")


# ============================================================
# 模型拉取与测试逻辑（纯函数，可脱离 UI 单测）
# ============================================================


def fetch_amd_models(timeout: int = 15) -> List[dict]:
    """通过 requests 方式访问 AMD Token Factory 获取 LLM 与 VLM 模型信息。

    Returns:
        List[dict]: 排序后的模型字典列表，每项格式：
            {"name": str, "type": "LLM" | "VLM", "publisher": str}
        网络异常、解析失败或无模型时返回空列表，由 UI 显示「无可用在线模型」。
    """
    try:
        session = requests.Session()
        resp = session.post(_AMD_BOOTSTRAP_URL, headers=_AMD_REQUEST_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("AMD Token Factory bootstrap failed with status %s", resp.status_code)
            return []

        cards = resp.json().get("cards", [])
        if not cards:
            logger.warning("AMD Token Factory bootstrap returned no cards")
            return []

        def _fetch_card_detail(card: dict) -> Optional[dict]:
            detail_rel = card.get("detail_url")
            if not detail_rel:
                return None
            detail_url = f"{_AMD_MODEL_DETAIL_BASE}{detail_rel}"
            try:
                card_resp = session.get(detail_url, headers=_AMD_REQUEST_HEADERS, timeout=timeout)
                if card_resp.status_code != 200:
                    return None
                data = card_resp.json()
                model_obj = data.get("model", {})
                tf = model_obj.get("token_factory", {})
                cap = tf.get("capability", {})
                cap_key = (cap.get("key") or "").lower()
                cap_label = (cap.get("label") or "").upper()

                # 仅保留 LLM (Text) 与 VLM (Vision)
                if cap_key == "text" or "LLM" in cap_label:
                    model_type = "LLM"
                elif cap_key == "vision" or "VLM" in cap_label:
                    model_type = "VLM"
                else:
                    return None

                access = tf.get("access", {})
                acc_m = access.get("model")
                access_model = acc_m.get("value") if isinstance(acc_m, dict) else (acc_m or "")
                api_model = access_model or model_obj.get("model") or tf.get("name") or ""
                display_name = tf.get("name") or api_model
                if not api_model:
                    return None

                publisher = tf.get("publisher", {}).get("name") or "Radeon Cloud"
                return {
                    "name": display_name,
                    "api_model": api_model,
                    "type": model_type,
                    "publisher": publisher,
                }
            except Exception as e:
                logger.debug("Failed to fetch detail for AMD model card %s: %s", detail_rel, e)
                return None

        # 并发获取各卡片详情
        with ThreadPoolExecutor(max_workers=5) as executor:
            details = list(executor.map(_fetch_card_detail, cards))

        valid_models = [d for d in details if d is not None and d.get("name")]
        # 按类型 (LLM 先，VLM 后) 然后按名称排序，保证显示规整
        valid_models.sort(key=lambda x: (x["type"], x["name"]))
        logger.info("Successfully fetched %d AMD models from Token Factory", len(valid_models))
        return valid_models
    except Exception as e:
        logger.warning("Failed to fetch AMD models dynamically from Token Factory: %s", e)
        return []


def test_one_model(
    model: str,
    timeout: int = TEST_TIMEOUT,
    api_url: str = _DEFAULT_API_URL,
) -> dict:
    """探测单个 AMD 模型的可用性与响应时间（轻量化探测）。

    Returns:
        dict: ``{model, ok, status, latency, snippet, error}``；
        ``status`` 取值 ``ok`` / ``fail`` / ``rate_limited``。
    """
    if not get_api_key("AMD_API_KEY"):
        return {
            "model": model,
            "ok": False,
            "status": "fail",
            "latency": 0.0,
            "snippet": "",
            "error": t("amd_test.error_no_key"),
        }

    # 每次新建 client：避免跨线程共享 Session
    client = AmdClient(api_url=api_url, timeout=timeout, model=model)
    client.max_retries = 1
    started = time.monotonic()
    try:
        text = client.generate(
            model=model,
            prompt=TEST_PROMPT,
            max_tokens=TEST_MAX_TOKENS,
            temperature=0.7,
            stream=False,
        )
        latency = time.monotonic() - started
        if latency > timeout:
            return {
                "model": model,
                "ok": False,
                "status": "fail",
                "latency": latency,
                "snippet": "",
                "error": t(
                    "amd_test.error_timeout",
                    latency=f"{latency:.1f}",
                    limit=timeout,
                ),
            }

        snippet = (text or "").strip()
        if not snippet:
            return {
                "model": model,
                "ok": False,
                "status": "fail",
                "latency": latency,
                "snippet": "",
                "error": t("amd_test.error_empty"),
            }

        return {
            "model": model,
            "ok": True,
            "status": "ok",
            "latency": latency,
            "snippet": snippet[:SNIPPET_LENGTH],
            "error": "",
        }
    except Exception as e:
        latency = time.monotonic() - started
        err_msg = str(e)
        lowered = err_msg.lower()
        is_limited = "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered or "限流" in err_msg
        return {
            "model": model,
            "ok": False,
            "status": "rate_limited" if is_limited else "fail",
            "latency": latency,
            "snippet": "",
            "error": err_msg,
        }


# ============================================================
# 自适应中间省略的标签
# ============================================================


class _ElidedLabel(QLabel):
    """宽度不足时按中间省略（ElideMiddle）的单行 QLabel。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt 命名)
        self._full_text = text
        self._update_elided()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self) -> None:
        width = self.width()
        if width <= 0:
            return
        super().setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideMiddle, width
            )
        )


# ============================================================
# 卡片组件 (ModelCard)
# ============================================================


class ModelCard(QFrame):
    """单个 AMD 模型的测试结果卡片（状态色条 + [LLM]/[VLM] 徽章 + 响应时间 + 重测按钮）。"""

    retest_clicked = Signal(str)
    apply_requested = Signal(str)

    def __init__(
        self,
        model_info: dict,
        refresh_icon: QIcon,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._display_name = model_info.get("name", "")
        self._api_model = model_info.get("api_model") or self._display_name
        self._model = self._api_model
        self._model_type = model_info.get("type", "LLM")
        self._publisher = model_info.get("publisher", "Radeon Cloud")
        self._status = "pending"
        self._latency: Optional[float] = None

        self.setObjectName("modelCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if self._display_name != self._api_model:
            tip_header = f"{self._display_name} (API: {self._api_model})"
        else:
            tip_header = self._display_name
        self.setToolTip(f"{tip_header}\n{t('amd_test.card_tooltip')}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 10)
        layout.setSpacing(4)

        # ── 第一行：状态圆点 + 类型徽章 + 发布者 + 重测按钮 ──
        head = QHBoxLayout()
        head.setSpacing(6)
        self._dot = QLabel("●")
        self._dot.setObjectName("cardDot")
        head.addWidget(self._dot)

        self._badge = QLabel(f" {self._model_type} ")
        self._badge.setObjectName("cardBadge")
        if self._model_type == "LLM":
            self._badge.setStyleSheet(
                "background: #e0f2fe; color: #0284c7; border: 1px solid #bae6fd; "
                "font-weight: 700; border-radius: 4px; padding: 1px 4px; font-size: 10px;"
            )
        else:
            self._badge.setStyleSheet(
                "background: #f3e8ff; color: #7c3aed; border: 1px solid #ddd6fe; "
                "font-weight: 700; border-radius: 4px; padding: 1px 4px; font-size: 10px;"
            )
        head.addWidget(self._badge)

        self._publisher_label = QLabel(self._publisher)
        self._publisher_label.setObjectName("cardPublisher")
        head.addWidget(self._publisher_label)
        head.addStretch(1)

        self._retest_btn = QPushButton()
        self._retest_btn.setObjectName("cardRetestBtn")
        self._retest_btn.setIcon(refresh_icon)
        self._retest_btn.setFixedSize(24, 24)
        self._retest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retest_btn.setToolTip(t("amd_test.retest_tooltip"))
        self._retest_btn.clicked.connect(lambda: self.retest_clicked.emit(self._api_model))
        head.addWidget(self._retest_btn)
        layout.addLayout(head)

        # ── 第二行：模型名（展示名称独占整行，宽度不足时中间省略） ──
        self._name_label = _ElidedLabel(self._display_name)
        self._name_label.setObjectName("cardName")
        layout.addWidget(self._name_label)

        # ── 第三行：状态 + 响应时间 ──
        info = QHBoxLayout()
        info.setSpacing(8)
        self._status_label = QLabel()
        self._status_label.setObjectName("cardStatus")
        info.addWidget(self._status_label)
        self._time_label = QLabel()
        self._time_label.setObjectName("cardLatency")
        info.addWidget(self._time_label)
        info.addStretch(1)
        layout.addLayout(info)

        # ── 第四行：返回摘要片段 / 错误信息（固定两行高） ──
        self._snippet_label = QLabel("")
        self._snippet_label.setObjectName("cardSnippet")
        self._snippet_label.setWordWrap(True)
        self._snippet_label.setFixedHeight(34)
        self._snippet_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._snippet_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._snippet_label)

        self._refresh_view()

    @property
    def model(self) -> str:
        """返回用于 API 探测与配置写入的真实模型标识。"""
        return self._api_model

    @property
    def api_model(self) -> str:
        return self._api_model

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def status(self) -> str:
        return self._status

    @property
    def latency(self) -> Optional[float]:
        return self._latency

    def set_pending(self) -> None:
        self._set_state("pending", None, "")

    def set_queued(self) -> None:
        self._set_state("queued", None, "")

    def set_testing(self) -> None:
        self._set_state("testing", None, "")

    def set_result(self, status: str, latency: float, snippet: str, error: str = "") -> None:
        if status not in _STATUS_COLORS:
            status = "fail"
        self._set_state(status, latency, snippet or error)

    def _set_state(self, status: str, latency: Optional[float], detail: str) -> None:
        self._status = status
        self._latency = latency
        self._retest_btn.setEnabled(status not in ("queued", "testing"))
        shown = detail if len(detail) <= _MAX_DETAIL_LENGTH else detail[:_MAX_DETAIL_LENGTH] + "…"
        self._snippet_label.setText(shown)
        self._snippet_label.setToolTip(detail)
        self._refresh_view()

    def _refresh_view(self) -> None:
        fg, bg = _STATUS_COLORS[self._status]
        self.setStyleSheet(
            "#modelCard {"
            "  background: #ffffff;"
            "  border: 1px solid #e0e0e0;"
            f" border-left: 4px solid {fg};"
            "  border-radius: 10px;"
            "}"
        )
        self._dot.setStyleSheet(f"color: {fg}; font-size: 13px;")
        self._status_label.setStyleSheet(
            f"color: {fg}; background: {bg}; border-radius: 8px;"
            " padding: 1px 8px; font-size: 12px; font-weight: 500;"
        )
        self._status_label.setText(t(_STATUS_TEXT_KEYS[self._status]))

        if self._status == "ok" and self._latency is not None:
            latency_text = t("amd_test.latency_value", seconds=f"{self._latency:.2f}")
        elif self._status in ("fail", "rate_limited") and self._latency is not None:
            latency_text = (
                t("amd_test.latency_over", seconds=TEST_TIMEOUT)
                if self._latency >= TEST_TIMEOUT
                else t("amd_test.latency_value", seconds=f"{self._latency:.2f}")
            )
        else:
            latency_text = t("amd_test.latency_none")
        self._time_label.setText(f"{t('amd_test.latency_label')}: {latency_text}")

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """双击「成功」卡片：把真实 API 模型名写回配置项 summarization.amd_model。"""
        if self._status == "ok":
            self.apply_requested.emit(self._api_model)
        try:
            super().mouseDoubleClickEvent(event)
        except TypeError:
            pass


# ============================================================
# 测试 Worker 线程
# ============================================================


class AmdApiTestWorker(QThread):
    """常驻测试线程：内部使用 `ThreadPoolExecutor` 并发执行 AMD 模型测试。"""

    model_started = Signal(str)
    result_ready = Signal(dict)
    batch_finished = Signal()

    def __init__(
        self,
        timeout: int = TEST_TIMEOUT,
        api_url: str = _DEFAULT_API_URL,
        max_workers: int = DEFAULT_CONCURRENCY,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._timeout = timeout
        self._api_url = api_url
        self._max_workers = max(1, min(max_workers, 8))
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._shutdown = threading.Event()

    def submit(self, models: Sequence[str]) -> None:
        if self._shutdown.is_set():
            return
        for model in models:
            self._queue.put(model)

    def cancel(self) -> None:
        self._drain()

    def shutdown(self) -> None:
        self._shutdown.set()
        self._drain()
        self._queue.put(None)

    def run(self) -> None:
        pool = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="amd-test")
        active: Dict[Future, str] = {}
        try:
            while not self._shutdown.is_set():
                while len(active) < self._max_workers:
                    model = self._take_next()
                    if model is None:
                        break
                    self.model_started.emit(model)
                    fut = pool.submit(self._run_one, model)
                    active[fut] = model

                if active:
                    done, _ = wait(
                        tuple(active.keys()),
                        timeout=_POLL_INTERVAL,
                        return_when=FIRST_COMPLETED,
                    )
                    for fut in done:
                        model = active.pop(fut)
                        try:
                            result = fut.result()
                        except Exception as e:
                            result = {
                                "model": model,
                                "ok": False,
                                "status": "fail",
                                "latency": 0.0,
                                "snippet": "",
                                "error": str(e),
                            }
                        self.result_ready.emit(result)

                    if not active and self._queue.empty():
                        self.batch_finished.emit()
                else:
                    self._wait_for_task()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _run_one(self, model: str) -> dict:
        try:
            return test_one_model(
                model=model,
                timeout=self._timeout,
                api_url=self._api_url,
            )
        except Exception as e:
            return {
                "model": model,
                "ok": False,
                "status": "fail",
                "latency": 0.0,
                "snippet": "",
                "error": str(e),
            }

    def _take_next(self) -> Optional[str]:
        while True:
            try:
                model = self._queue.get_nowait()
            except queue.Empty:
                return None
            if model is not None:
                return model

    def _wait_for_task(self) -> None:
        try:
            model = self._queue.get(timeout=_IDLE_POLL_INTERVAL)
        except queue.Empty:
            return
        if model is not None:
            self._queue.put(model)

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


# ============================================================
# 模型列表后台 Fetcher 线程与内存缓存
# ============================================================

_ORPHAN_WORKERS: set = set()
_ORPHAN_FETCHERS: set = set()

#: 进程内模型列表缓存（生命周期为软件打开期间）
_MODEL_LIST_CACHE: Optional[List[dict]] = None
#: 进程内各模型的最后测试结果缓存
_MODEL_TEST_RESULTS: Dict[str, dict] = {}


def _release_orphan(worker: AmdApiTestWorker) -> None:
    _ORPHAN_WORKERS.discard(worker)


class _ModelListFetcher(QThread):
    """后台拉取 AMD Token Factory 模型列表的 QThread。"""

    models_ready = Signal(list)
    fetch_failed = Signal(str)

    def __init__(self, timeout: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._timeout = timeout

    def run(self) -> None:
        try:
            models = fetch_amd_models(timeout=self._timeout)
            self.models_ready.emit(models)
        except Exception as e:
            self.fetch_failed.emit(str(e))


# ============================================================
# 对话框样式表
# ============================================================

_DIALOG_QSS = """
QDialog {
    background: #f7f9fa;
}
#toolBar {
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
}
#statusBar {
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
}
#primaryBtn {
    background: #1976d2;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 6px 18px;
    font-size: 13px;
    font-weight: 500;
}
#primaryBtn:hover { background: #1565c0; }
#primaryBtn:disabled { background: #e0e0e0; color: #9e9e9e; }
#secondaryBtn {
    background: transparent;
    color: #1976d2;
    border: 1px solid #1976d2;
    border-radius: 6px;
    padding: 6px 18px;
    font-size: 13px;
    font-weight: 500;
}
#secondaryBtn:hover { background: #e3f2fd; }
#secondaryBtn:disabled { color: #bdbdbd; border-color: #e0e0e0; }
#cardRetestBtn {
    background: transparent;
    border: none;
    border-radius: 12px;
}
#cardRetestBtn:hover { background: #e3f2fd; }
#cardRetestBtn:disabled { background: transparent; }
#cardName {
    font-size: 13px;
    font-weight: 600;
    color: #212121;
}
#cardPublisher {
    font-size: 11px;
    color: #9e9e9e;
}
#cardLatency {
    font-size: 12px;
    color: #616161;
}
#cardSnippet {
    font-size: 11px;
    color: #757575;
}
#infoLabel {
    font-size: 12px;
    color: #616161;
}
#progressLabel {
    font-size: 12px;
    font-weight: 500;
    color: #424242;
}
#cardArea { background: transparent; border: none; }
#cardHost { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #c0c0c0; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #a0a0a0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


# ============================================================
# AMD API 测试对话框本体 (AmdApiTestDialog)
# ============================================================


class AmdApiTestDialog(QDialog):
    """AMD API 响应测试对话框：卡片式展示 LLM 与 VLM 模型的可用性与延迟。"""

    def __init__(self, parent: Optional[QWidget] = None, settings: Optional[Settings] = None):
        super().__init__(parent)
        global _MODEL_LIST_CACHE
        self._settings = settings or Settings()
        self._api_url = self._settings.get(
            "summarization.amd_api_url", _DEFAULT_API_URL
        )
        self._timeout = TEST_TIMEOUT
        self._max_workers = max(
            1,
            min(
                self._settings.get_int("summarization.amd_thread_count", DEFAULT_CONCURRENCY),
                8,
            ),
        )
        self._has_api_key = bool(get_api_key("AMD_API_KEY"))
        self._cards: Dict[str, ModelCard] = {}

        cache_hit = _MODEL_LIST_CACHE is not None
        self._models: List[dict] = list(_MODEL_LIST_CACHE) if cache_hit else []
        self._worker: Optional[AmdApiTestWorker] = None
        self._list_fetcher: Optional[_ModelListFetcher] = None
        self._fetch_notify: bool = False
        self._card_area: Optional[QScrollArea] = None
        self._card_host: Optional[QWidget] = None
        self._card_grid: Optional[QGridLayout] = None
        self._empty_label: Optional[QLabel] = None

        self.setWindowTitle(t("amd_test.title"))
        self.setStyleSheet(_DIALOG_QSS)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.resize(1020, 720)

        self._refresh_icon = self._load_refresh_icon()
        self._init_ui(is_loading=not cache_hit)
        self._update_progress()

        if not cache_hit:
            self._start_model_fetch(notify=False)

    def _load_refresh_icon(self) -> QIcon:
        icon_path = get_base_dir() / "assets" / "refresh.png"
        if not icon_path.exists():
            icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "refresh.png"
        if not icon_path.exists():
            return QIcon()
        return QIcon(str(icon_path))

    def _init_ui(self, is_loading: bool = False) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._build_tool_bar())
        self._card_area = self._build_card_area(is_loading=is_loading)
        root.addWidget(self._card_area, 1)
        root.addWidget(self._build_status_bar())

    def _build_tool_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("toolBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        key_label = QLabel(
            t("amd_test.api_key_ok") if self._has_api_key else t("amd_test.api_key_missing")
        )
        key_label.setObjectName("infoLabel")
        key_label.setStyleSheet(
            "color: #388e3c;" if self._has_api_key else "color: #d32f2f; font-weight: 600;"
        )
        layout.addWidget(key_label)

        timeout_label = QLabel(t("amd_test.timeout_label", seconds=self._timeout))
        timeout_label.setObjectName("infoLabel")
        layout.addWidget(timeout_label)

        concurrency_label = QLabel(
            t("amd_test.concurrency_label", workers=self._max_workers)
        )
        concurrency_label.setObjectName("infoLabel")
        layout.addWidget(concurrency_label)

        self._current_model_label = QLabel(
            t(
                "amd_test.current_model",
                model=self._settings.get("summarization.amd_model", "Qwen3.8-Flash-Next"),
            )
        )
        self._current_model_label.setObjectName("infoLabel")
        layout.addWidget(self._current_model_label, 1)

        self._refresh_list_btn = QPushButton(t("amd_test.refresh_list"))
        self._refresh_list_btn.setObjectName("secondaryBtn")
        self._refresh_list_btn.clicked.connect(self._on_refresh_list)
        layout.addWidget(self._refresh_list_btn)

        self._test_all_btn = QPushButton(t("amd_test.one_click"))
        self._test_all_btn.setObjectName("primaryBtn")
        self._test_all_btn.clicked.connect(self._on_test_all)
        layout.addWidget(self._test_all_btn)

        self._stop_btn = QPushButton(t("amd_test.stop"))
        self._stop_btn.setObjectName("secondaryBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        layout.addWidget(self._stop_btn)
        return bar

    def _build_card_area(self, is_loading: bool = False) -> QScrollArea:
        self._card_host = QWidget()
        self._card_host.setObjectName("cardHost")
        self._card_grid = QGridLayout(self._card_host)
        self._card_grid.setContentsMargins(0, 0, 6, 0)
        self._card_grid.setHorizontalSpacing(10)
        self._card_grid.setVerticalSpacing(10)

        self._populate_cards(self._models, is_loading=is_loading)

        area = QScrollArea()
        area.setObjectName("cardArea")
        area.setWidgetResizable(True)
        area.setWidget(self._card_host)
        return area

    def _populate_cards(self, models: Sequence[dict], is_loading: bool = False) -> None:
        if self._card_grid is None or self._card_host is None:
            return
        while self._card_grid.count():
            item = self._card_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()
        self._hide_empty_label()

        if not models:
            self._show_empty_label(loading=is_loading)
            self._test_all_btn.setEnabled(False)
            return

        self._test_all_btn.setEnabled(True)
        for index, item in enumerate(models):
            card = ModelCard(item, self._refresh_icon, self._card_host)
            card.retest_clicked.connect(self._on_retest)
            card.apply_requested.connect(self._on_apply_model)
            model_key = card.model
            self._cards[model_key] = card
            self._card_grid.addWidget(card, index // _CARD_COLUMNS, index % _CARD_COLUMNS)

            prev = _MODEL_TEST_RESULTS.get(model_key)
            if prev is not None:
                card.set_result(
                    prev["status"], prev["latency"], prev["snippet"], prev["error"]
                )

        for column in range(_CARD_COLUMNS):
            self._card_grid.setColumnStretch(column, 1)
        self._card_grid.setRowStretch(self._card_grid.rowCount(), 1)

    def _show_empty_label(self, loading: bool = False) -> None:
        if self._card_grid is None:
            return
        key = "amd_test.models_loading" if loading else "amd_test.models_empty"
        label = QLabel(t(key))
        label.setObjectName("modelsEmptyLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "color: #9e9e9e; font-size: 14px; padding: 32px;"
        )
        self._card_grid.addWidget(label, 0, 0, self._card_grid.rowCount(), _CARD_COLUMNS)
        self._empty_label = label

    def _hide_empty_label(self) -> None:
        if self._empty_label is None or self._card_grid is None:
            return
        self._card_grid.removeWidget(self._empty_label)
        self._empty_label.deleteLater()
        self._empty_label = None

    def _build_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("statusBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 6, 12, 6)
        self._progress_label = QLabel("")
        self._progress_label.setObjectName("progressLabel")
        layout.addWidget(self._progress_label)
        layout.addStretch(1)
        hint = QLabel(t("amd_test.card_tooltip"))
        hint.setObjectName("infoLabel")
        layout.addWidget(hint)
        return bar

    # ── 线程管理 ──

    def _ensure_worker(self) -> AmdApiTestWorker:
        if self._worker is None:
            worker = AmdApiTestWorker(
                timeout=self._timeout,
                api_url=self._api_url,
                max_workers=self._max_workers,
            )
            worker.model_started.connect(self._on_model_started)
            worker.result_ready.connect(self._on_result)
            worker.batch_finished.connect(self._on_batch_finished)
            worker.start()
            self._worker = worker
        return self._worker

    def _shutdown_worker(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        for signal in (worker.model_started, worker.result_ready, worker.batch_finished):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        worker.shutdown()
        if worker.wait(1500):
            worker.deleteLater()
            return
        _ORPHAN_WORKERS.add(worker)
        worker.finished.connect(lambda: _release_orphan(worker))
        worker.finished.connect(worker.deleteLater)
        if worker.isFinished():
            _release_orphan(worker)

    # ── 槽函数 ──

    def _on_refresh_list(self) -> None:
        self._start_model_fetch(notify=True)

    def _start_model_fetch(self, notify: bool) -> None:
        if self._list_fetcher is not None and self._list_fetcher.isRunning():
            return
        self._fetch_notify = notify
        self._refresh_list_btn.setEnabled(False)
        self._test_all_btn.setEnabled(False)
        self._set_cards_retest_enabled(False)
        self._progress_label.setText(t("amd_test.refresh_list_loading"))
        fetcher = _ModelListFetcher(self._timeout, self)
        self._list_fetcher = fetcher
        fetcher.models_ready.connect(self._on_models_ready)
        fetcher.fetch_failed.connect(self._on_models_fetch_failed)
        fetcher.finished.connect(fetcher.deleteLater)
        fetcher.finished.connect(lambda: _ORPHAN_FETCHERS.discard(fetcher))
        fetcher.start()

    def _on_models_ready(self, models: List[dict]) -> None:
        self._list_fetcher = None
        global _MODEL_LIST_CACHE
        _MODEL_LIST_CACHE = list(models)
        self._models = list(models)
        self._populate_cards(self._models, is_loading=False)
        self._refresh_list_btn.setEnabled(True)
        self._set_cards_retest_enabled(True)
        self._update_progress()
        if self._fetch_notify:
            QMessageBox.information(
                self,
                t("amd_test.title"),
                t("amd_test.refresh_list_done", count=len(models)),
            )

    def _on_models_fetch_failed(self, error: str) -> None:
        self._list_fetcher = None
        self._populate_cards(self._models, is_loading=False)
        self._refresh_list_btn.setEnabled(True)
        self._set_cards_retest_enabled(True)
        self._update_progress()
        if self._fetch_notify:
            QMessageBox.warning(
                self,
                t("amd_test.title"),
                t("amd_test.refresh_list_failed", error=error),
            )

    def _set_cards_retest_enabled(self, enabled: bool) -> None:
        for card in self._cards.values():
            if card.status in ("queued", "testing"):
                continue
            card._retest_btn.setEnabled(enabled)

    def _on_test_all(self) -> None:
        if not self._require_api_key():
            return
        pending = []
        for card in self._cards.values():
            card.set_queued()
            pending.append(card.model)
        self._set_busy(True)
        self._update_progress()
        self._ensure_worker().submit(pending)

    def _on_retest(self, model: str) -> None:
        if not self._require_api_key():
            return
        card = self._cards.get(model)
        if card is None or card.status in ("queued", "testing"):
            return
        card.set_queued()
        self._set_busy(True)
        self._update_progress()
        self._ensure_worker().submit([model])

    def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        for card in self._cards.values():
            if card.status == "queued":
                card.set_pending()
        self._set_busy(self._has_active_cards())
        self._update_progress()

    def _on_model_started(self, model: str) -> None:
        card = self._cards.get(model)
        if card is not None:
            card.set_testing()
        self._update_progress()

    def _on_result(self, result: dict) -> None:
        card = self._cards.get(result.get("model", ""))
        if card is not None:
            card.set_result(
                result.get("status", "fail"),
                float(result.get("latency", 0.0)),
                result.get("snippet", ""),
                result.get("error", ""),
            )
        if not result.get("ok"):
            logger.info(
                "AMD API test failed: %s -> %s",
                result.get("model", "?"),
                result.get("error", ""),
            )
        if result.get("status") in _FINAL_STATUSES:
            _MODEL_TEST_RESULTS[result.get("model", "")] = {
                "status": result.get("status", "fail"),
                "latency": float(result.get("latency", 0.0)),
                "snippet": result.get("snippet", ""),
                "error": result.get("error", ""),
            }
        self._update_progress()

    def _on_batch_finished(self) -> None:
        self._set_busy(self._has_active_cards())
        self._update_progress()

    def _on_apply_model(self, model: str) -> None:
        """双击「成功」卡片：把该模型写入配置项 summarization.amd_model，并同步切换 summarization.provider 为 amd。"""
        try:
            self._settings.set("summarization.provider", "amd")
            self._settings.set("summarization.amd_model", model)
            self._settings.save()
        except Exception as e:
            logger.warning("Failed to write AMD summary model: %s", e)
            return
        self._current_model_label.setText(t("amd_test.current_model", model=model))

    # ── 辅助方法 ──

    def _require_api_key(self) -> bool:
        if self._has_api_key:
            return True
        QMessageBox.warning(
            self, t("amd_test.title"), t("amd_test.api_key_missing_msg")
        )
        return False

    def _has_active_cards(self) -> bool:
        return any(card.status in ("queued", "testing") for card in self._cards.values())

    def _set_busy(self, busy: bool) -> None:
        self._test_all_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)

    def _update_progress(self) -> None:
        total = len(self._cards)
        counts = {"ok": 0, "fail": 0, "rate_limited": 0}
        done = 0
        for card in self._cards.values():
            if card.status in counts:
                counts[card.status] += 1
                done += 1
        self._progress_label.setText(
            t(
                "amd_test.progress",
                done=done,
                total=total,
                ok=counts["ok"],
                fail=counts["fail"],
                limited=counts["rate_limited"],
            )
        )

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if self._list_fetcher is not None and self._list_fetcher.isRunning():
            fetcher = self._list_fetcher
            self._list_fetcher = None
            try:
                fetcher.models_ready.disconnect(self._on_models_ready)
                fetcher.fetch_failed.disconnect(self._on_models_fetch_failed)
            except (RuntimeError, TypeError):
                pass
            _ORPHAN_FETCHERS.add(fetcher)
        self._shutdown_worker()
        super().closeEvent(event)
