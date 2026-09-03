"""Nvidia API 响应测试对话框 —— 批量探测「文本总结可用模型」的可用性与延迟。

自包含模块（解耦设计，见 plans/20260809_nvidia_free_endpoint.md §6）：
  - `fetch_text_summary_models()`：动态拉取第 5.4 节的文本总结可用模型（复用
    `tests/fetch_free_endpoints.py` 的 NGC 目录翻页 + 关键词筛选逻辑，失败或无结果
    时返回空列表，由 UI 给出空状态提示，不再回退离线常量）。
  - `test_one_model()`：复用 `NvidiaClient` 发一次最小请求，30s 超时即判失败。
  - `ModelCard`：卡片式 UI（状态色条 + 响应时间 + 摘要片段 + 右上角重测按钮）。
  - `_ModelListFetcher`：常驻 QThread，后台拉取模型列表，到位后重建卡片。
  - `NvidiaApiTestWorker`：常驻 QThread，内部线程池最多 5 并发，结果经信号回主线程。
  - `NvidiaApiTestDialog`：对话框本体，`gui.py` 仅通过菜单入口调用，不含任何业务逻辑。

模型列表缓存（`_MODEL_LIST_CACHE`）：
  - 仅在进程内有效，生命周期为「本次软件打开」；
  - 首次进入对话框时由后台拉取填充（含空列表，表示拉取成功但无模型）；
  - 之后再次进入对话框直接复用，避免每次打开都发起 NGC 目录网络请求；
  - 用户仍可随时点工具栏「刷新列表」手动更新。
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.parse
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
from src.summarization.nvidia_client import NvidiaClient
from src.utils.env_loader import get_api_key
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger
from src.utils.paths import get_base_dir

logger = get_logger(__name__)


# ============================================================
# 常量
# ============================================================

#: 文本总结可用模型的离线常量（plans/20260809_nvidia_free_endpoint.md §5.4，共 27 个）。
#: 仅供单元测试与计划文档交叉校验使用，运行期不再回退到此列表：
#: 在线拉取失败或结果为空时 UI 直接展示「无可用在线模型」空状态。
_FALLBACK_TEXT_SUMMARY_MODELS: tuple = (
    "bytedance/seed-oss-36b-instruct",
    "google/diffusiongemma-26b-a4b-it",
    "google/gemma-4-31b-it",
    "meta/llama-3.2-1b-instruct",
    "meta/llama-3.2-3b-instruct",
    "meta/llama-3_1-70b-instruct",
    "meta/llama-3_1-8b-instruct",
    "meta/llama-3_3-70b-instruct",
    "minimaxai/minimax-m3",
    "mistralai/mistral-nemotron",
    "mistralai/mixtral-8x7b-instruct",
    "nvidia/llama-3_1-nemotron-nano-8b-v1",
    "nvidia/llama-3_3-nemotron-super-49b-v1",
    "nvidia/llama-3_3-nemotron-super-49b-v1_5",
    "nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-mini-4b-instruct",
    "nvidia/nvidia-nemotron-nano-9b-v2",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "poolside/laguna-xs-2.1",
    "qwen/qwen3-next-80b-a3b-instruct",
    "sarvamai/sarvam-m",
    "stepfun-ai/step-3.5-flash",
    "upstage/solar-10_7b-instruct",
    "z-ai/glm-5.2",
)

#: NGC catalog 搜索接口（plans/20260809_nvidia_free_endpoint.md §2.6，build 专用 org）。
_NGC_ENDPOINT_BASE = "https://api.ngc.nvidia.com/v2/search/catalog/resources/ENDPOINT"
#: 翻页数：每页 24 条，6 页覆盖全部 ~131 个 endpoint（§2.5）。
_NGC_PAGE_COUNT = 6
_NGC_PAGE_SIZE = 24

#: 文本总结模型的「必须命中」关键词（§5.2 的 INCLUDE）。
_MODEL_INCLUDE_KEYWORDS = (
    "text-to-text",
    "text-generation",
    "language generation",
    "chat",
    "reasoning",
    "instruction following",
    "agentic",
)
#: 命中即排除的非文本 / 非总结模态关键词（§5.2 的 EXCLUDE）。
_MODEL_EXCLUDE_KEYWORDS = (
    "embedding", "retriever", "rerank", "ranking", "tts", "speech", "voice",
    "vision", "vlm", "visual", "image", "ocr", "video", "captioning",
    "question answering", "doc intelligence", "protein", "biology", "bionemo",
    "safety", "guard", "moderation", "translation", "autonomous", "vehicles",
    "bev", "robotics", "broadcast", "smpte", "forensics", "speaker",
    "denoising", "calibration", "synthetic", "multimodal",
)


def _is_text_summary_model(general: str) -> bool:
    """按 §5.2 规则判断一个 endpoint 是否为纯文本生成 / 总结可用模型。"""
    g = general.lower()
    if not any(key in g for key in _MODEL_INCLUDE_KEYWORDS):
        return False
    if any(key in g for key in _MODEL_EXCLUDE_KEYWORDS):
        return False
    return True


def _query_ngc_page(page: int, timeout: int) -> list:
    """拉取 NGC catalog 搜索接口单页的 ENDPOINT 分组资源（§2.4 / §2.5）。"""
    query = {
        "query": "*:*",
        "page": page,
        "pageSize": _NGC_PAGE_SIZE,
        "scoredSize": _NGC_PAGE_SIZE,
        "groupBy": "resourceType",
        "filters": [
            {"field": "orgName", "value": "qc69jvmznzxy"},
            {"field": "resourceId", "value": "-(qc69jvmznzxy/fidelity) OR -(qc69jvmznzxy/fluent) OR -(qc69jvmznzxy/spectre-x) OR -(qc69jvmznzxy/star-ccm)"},
            {"field": "label", "value": "-(\"blueprint\")"},
            {"field": "resourceType", "value": "endpoint"},
            {"field": "notAccessType", "value": "NOT_LISTED"},
            {"field": "isPublic", "value": "true"},
        ],
        "orderBy": [{"field": "dateCreated", "value": "DESC"}],
        "fields": ["labels", "name", "resource_id"],
    }
    url = (
        f"{_NGC_ENDPOINT_BASE}?q={urllib.parse.quote(json.dumps(query))}"
        "&group-labels-by-labelset=true"
    )
    # 关键：避开服务端返回的 zstd 编码，否则 requests 解不出（§2.5）
    headers = {"Accept-Encoding": "gzip, deflate"}
    data = requests.get(url, headers=headers, timeout=timeout).json()
    for group in data.get("results", []):
        if group.get("groupValue") == "ENDPOINT":
            return group["resources"]
    return data["results"][0]["resources"]


def fetch_text_summary_models(timeout: int = 30) -> List[str]:
    """动态拉取「文本总结可用模型」列表（plans/20260809_nvidia_free_endpoint.md §5）。

    复用 `tests/fetch_free_endpoints.py` 的翻页 + `nimType` 过滤 + 关键词筛选逻辑，
    拼出形如 `openai/gpt-oss-120b` 的模型名。

    Returns:
        List[str]: 排序后的模型名列表（去重）。**不再回退到离线常量**：
        网络异常、目录为空或筛选后无命中时返回空列表，由调用方展示
        「无可用在线模型」空状态。
    """
    try:
        seen: set = set()
        models: List[str] = []
        for page in range(_NGC_PAGE_COUNT):
            for resource in _query_ngc_page(page, timeout):
                rid = resource["resourceId"]
                if rid in seen:
                    continue
                seen.add(rid)
                labels = {label["key"]: label for label in resource.get("labels", [])}
                nim = labels.get("nimType", {}).get("unresolvedValues", [])
                if "nim_type_preview" not in nim:  # Free Endpoint 标记
                    continue
                publisher_values = labels.get("publisher", {}).get("unresolvedValues", [])
                if not publisher_values:
                    continue
                publisher = publisher_values[0]
                general = " ".join(labels.get("general", {}).get("values", []))
                if _is_text_summary_model(general):
                    models.append(f"{publisher}/{resource['name']}")
        models = sorted(set(models))
        if not models:
            logger.warning("NGC catalog returned no text-summary models")
        return models
    except Exception as e:
        logger.warning("Failed to fetch text-summary models dynamically: %s", e)
        return []

#: 默认最长响应 30s，超过视为失败（§6.6）
TEST_TIMEOUT = 30
#: 探测用的最小请求
TEST_PROMPT = "In a nutshell: It's sunny today, perfect for a walk outside."
#: 探测预算需足够推理模型（如 openai/gpt-oss）留出 reasoning 余量，
#: 否则整段 max_tokens 被 reasoning 占满、message.content 为 null 被误判为「空响应」。
TEST_MAX_TOKENS = 512
#: 并发上限（§6.7）
MAX_CONCURRENCY = 5
#: 卡片内展示的返回摘要片段长度
SNIPPET_LENGTH = 40
#: 卡片内展示的错误信息最大长度（完整内容见 tooltip）
_MAX_DETAIL_LENGTH = 60

_DEFAULT_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_CARD_COLUMNS = 3
_POLL_INTERVAL = 0.1
_IDLE_POLL_INTERVAL = 0.2

#: 状态 -> (前景色, 背景色)，与 voice_to_text_widget 的配色保持一致
_STATUS_COLORS: Dict[str, tuple] = {
    "pending": ("#9e9e9e", "#f5f5f5"),
    "queued": ("#7e57c2", "#ede7f6"),
    "testing": ("#1976d2", "#e3f2fd"),
    "ok": ("#388e3c", "#e8f5e9"),
    "fail": ("#d32f2f", "#ffebee"),
    "rate_limited": ("#f57c00", "#fff3e0"),
}
_STATUS_TEXT_KEYS: Dict[str, str] = {
    "pending": "nvidia_test.status_pending",
    "queued": "nvidia_test.status_queued",
    "testing": "nvidia_test.status_testing",
    "ok": "nvidia_test.status_ok",
    "fail": "nvidia_test.status_fail",
    "rate_limited": "nvidia_test.status_rate_limited",
}
#: 已结束（不再变化）的终态
_FINAL_STATUSES = ("ok", "fail", "rate_limited")


# ============================================================
# 测试逻辑（纯函数，可脱离 UI 单测）
# ============================================================


def _is_rate_limit_error(message: str) -> bool:
    """判断异常文案是否为 429 限流。

    `NvidiaClient` 的限流文案已本地化，但各语言均保留了 "429" 字面量
    （见 locales 的 services.summarization.nvidia.rate_limited），故以此判定。
    """
    lowered = message.lower()
    return "429" in lowered or "rate limit" in lowered


def test_one_model(
    model: str,
    timeout: int = TEST_TIMEOUT,
    api_url: str = _DEFAULT_API_URL,
) -> dict:
    """探测单个模型的可用性与响应时间。

    复用 `NvidiaClient.generate()`（已含限流识别与异常归一），测试场景下关闭重试，
    单次超时即失败，避免一次失败被重试拖到 90s。

    Returns:
        dict: ``{model, ok, status, latency, snippet, error}``；
        ``status`` 取值 ``ok`` / ``fail`` / ``rate_limited``。
    """
    # API Key 缺失时直接返回，不发起任何网络请求（§6.10.5）。
    # 这里不调用 client.check_connection()：该方法会额外发一次真实请求，
    # 27 个模型将使请求量翻倍并显著提高 429 概率（§6.10.4）。
    if not get_api_key("NVIDIA_API_KEY"):
        return {
            "model": model,
            "ok": False,
            "status": "fail",
            "latency": 0.0,
            "snippet": "",
            "error": t("nvidia_test.error_no_key"),
        }

    # 每次新建 client：requests.Session 不跨线程共享（§6.10.2）
    client = NvidiaClient(api_url=api_url, timeout=timeout, model=model)
    client.max_retries = 1
    started = time.monotonic()
    try:
        text = client.generate(
            model=model,
            prompt=TEST_PROMPT,
            max_tokens=TEST_MAX_TOKENS,
            temperature=0.0,
            top_p=1.0,
            stream=False,
        )
        latency = time.monotonic() - started
        # 双重保险：即使底层未触发 Timeout，逻辑层也判定 >timeout 为失败
        if latency > timeout:
            return {
                "model": model,
                "ok": False,
                "status": "fail",
                "latency": latency,
                "snippet": "",
                "error": t(
                    "nvidia_test.error_timeout",
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
                "error": t("nvidia_test.error_empty"),
            }
        if len(snippet) > SNIPPET_LENGTH:
            snippet = snippet[:SNIPPET_LENGTH] + "…"
        return {
            "model": model,
            "ok": True,
            "status": "ok",
            "latency": latency,
            "snippet": snippet,
            "error": "",
        }
    except SummarizationError as e:
        message = str(e)
        return {
            "model": model,
            "ok": False,
            "status": "rate_limited" if _is_rate_limit_error(message) else "fail",
            "latency": time.monotonic() - started,
            "snippet": "",
            "error": message,
        }
    except Exception as e:  # 兜底：任何未预期异常都不应让线程池任务崩掉
        logger.debug("Nvidia API test unexpected error (%s): %s", model, e)
        return {
            "model": model,
            "ok": False,
            "status": "fail",
            "latency": time.monotonic() - started,
            "snippet": "",
            "error": str(e),
        }
    finally:
        client.close()


# ============================================================
# 卡片
# ============================================================


class _ElidedLabel(QLabel):
    """单行标签：宽度不足时按中间省略，避免长模型名被硬裁切。

    使用 Ignored 的水平尺寸策略，让宽度完全由布局决定，
    省略后 sizeHint 变化不会反过来影响布局（不产生抖动）。
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def full_text(self) -> str:
        return self._full_text

    def set_full_text(self, text: str) -> None:
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


class ModelCard(QFrame):
    """单个模型的测试结果卡片（圆角方块 + 左侧状态色条 + 右上角重测按钮）。

    模型名较长（最长 40 字符），因此拆成「publisher / 模型名」两行展示，
    并在宽度不足时按中间省略，避免被硬裁切；完整名称始终保留在 tooltip 中。
    """

    retest_clicked = Signal(str)
    apply_requested = Signal(str)

    def __init__(self, model: str, refresh_icon: QIcon, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._model = model
        self._status = "pending"
        self._latency: Optional[float] = None
        publisher, _, short_name = model.partition("/")

        self.setObjectName("modelCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(model + "\n" + t("nvidia_test.card_tooltip"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 10)
        layout.setSpacing(2)

        # ── 第一行：状态圆点 + publisher + 重测按钮 ──
        head = QHBoxLayout()
        head.setSpacing(6)
        self._dot = QLabel("●")
        self._dot.setObjectName("cardDot")
        head.addWidget(self._dot)

        self._publisher_label = QLabel(publisher)
        self._publisher_label.setObjectName("cardPublisher")
        head.addWidget(self._publisher_label)
        head.addStretch(1)

        self._retest_btn = QPushButton()
        self._retest_btn.setObjectName("cardRetestBtn")
        self._retest_btn.setIcon(refresh_icon)
        self._retest_btn.setFixedSize(24, 24)
        self._retest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retest_btn.setToolTip(t("nvidia_test.retest_tooltip"))
        self._retest_btn.clicked.connect(lambda: self.retest_clicked.emit(self._model))
        head.addWidget(self._retest_btn)
        layout.addLayout(head)

        # ── 第二行：模型名（独占整行，宽度不足时中间省略） ──
        self._name_text = short_name or model
        self._name_label = _ElidedLabel(self._name_text)
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

        # ── 第四行：返回摘要片段 / 错误信息（固定两行高，保证卡片等高） ──
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

    # ── 只读属性 ──

    @property
    def model(self) -> str:
        return self._model

    @property
    def status(self) -> str:
        return self._status

    @property
    def latency(self) -> Optional[float]:
        return self._latency

    # ── 状态变更 ──

    def set_pending(self) -> None:
        self._set_state("pending", None, "")

    def set_queued(self) -> None:
        self._set_state("queued", None, "")

    def set_testing(self) -> None:
        self._set_state("testing", None, "")

    def set_result(self, status: str, latency: float, snippet: str, error: str = "") -> None:
        """写入一次测试结果：状态色、响应时间、摘要片段/错误信息。"""
        if status not in _STATUS_COLORS:
            status = "fail"
        self._set_state(status, latency, snippet or error)

    def _set_state(self, status: str, latency: Optional[float], detail: str) -> None:
        self._status = status
        self._latency = latency
        # 排队中 / 测试中禁用「重测」，避免重复提交同一模型
        self._retest_btn.setEnabled(status not in ("queued", "testing"))
        # 错误信息可能很长（含服务端 JSON），截断后放进卡片，完整内容留在 tooltip
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
            latency_text = t("nvidia_test.latency_value", seconds=f"{self._latency:.2f}")
        elif self._status in ("fail", "rate_limited") and self._latency is not None:
            latency_text = (
                t("nvidia_test.latency_over", seconds=TEST_TIMEOUT)
                if self._latency >= TEST_TIMEOUT
                else t("nvidia_test.latency_value", seconds=f"{self._latency:.2f}")
            )
        else:
            latency_text = t("nvidia_test.latency_none")
        self._time_label.setText(f"{t('nvidia_test.latency_label')}: {latency_text}")

    # ── 交互 ──

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """双击「成功」卡片，把该模型写回配置（§6.10.6）。"""
        if self._status == "ok":
            self.apply_requested.emit(self._model)
        super().mouseDoubleClickEvent(event)


# ============================================================
# 测试线程
# ============================================================


class NvidiaApiTestWorker(QThread):
    """常驻测试线程：内部用 `ThreadPoolExecutor` 并发跑各模型测试。

    「一键测试」与单卡「重新测试」共用同一线程 / 同一线程池，
    因此单卡重测同样受全局并发上限约束（§6.7）。
    """

    model_started = Signal(str)
    result_ready = Signal(dict)
    batch_finished = Signal()

    def __init__(
        self,
        timeout: int = TEST_TIMEOUT,
        api_url: str = _DEFAULT_API_URL,
        max_workers: int = MAX_CONCURRENCY,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._timeout = timeout
        self._api_url = api_url
        self._max_workers = max(1, min(max_workers, MAX_CONCURRENCY))
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._shutdown = threading.Event()

    # ── 外部接口（主线程调用） ──

    def submit(self, models: Sequence[str]) -> None:
        """提交待测模型（可多次调用，包括批量与单卡重测）。"""
        if self._shutdown.is_set():
            return
        for model in models:
            self._queue.put(model)

    def cancel(self) -> None:
        """丢弃尚未开始的任务；已发出的请求会跑完并照常回报结果。"""
        self._drain()

    def shutdown(self) -> None:
        """请求线程退出（等待已发出的请求自然结束）。"""
        self._drain()
        self._shutdown.set()
        self._queue.put(None)  # 唤醒空闲等待

    # ── 线程体 ──

    def run(self) -> None:
        executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="nvapitest"
        )
        inflight: Dict[Future, str] = {}
        was_busy = False
        try:
            while not (self._shutdown.is_set() and not inflight):
                # 1) 在并发额度内取出待测模型
                while len(inflight) < self._max_workers and not self._shutdown.is_set():
                    model = self._take_next()
                    if model is None:
                        break
                    self.model_started.emit(model)
                    inflight[executor.submit(self._test, model)] = model

                # 2) 有在跑的任务：等任意一个完成并回报
                if inflight:
                    was_busy = True
                    done, _ = wait(
                        list(inflight),
                        timeout=_POLL_INTERVAL,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        model = inflight.pop(future, "")
                        self.result_ready.emit(self._result_of(future, model))
                    continue

                # 3) 空闲：忙 -> 闲 的瞬间视为一批结束
                if was_busy:
                    was_busy = False
                    self.batch_finished.emit()
                self._wait_for_task()
        finally:
            executor.shutdown(wait=False)

    # ── 内部工具 ──

    def _test(self, model: str) -> dict:
        return test_one_model(model, timeout=self._timeout, api_url=self._api_url)

    def _result_of(self, future: Future, model: str) -> dict:
        try:
            return future.result()
        except Exception as e:  # test_one_model 已兜底，这里再保险一层
            logger.debug("Nvidia API test future failed (%s): %s", model, e)
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
        """空闲时阻塞等待新任务，避免忙轮询。"""
        try:
            model = self._queue.get(timeout=_IDLE_POLL_INTERVAL)
        except queue.Empty:
            return
        if model is not None:
            self._queue.put(model)  # 放回，交由主循环统一调度

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


#: 关闭对话框时仍有请求在途的线程，暂存于此以免被 GC 提前回收（QThread 运行中被销毁会崩溃）
_ORPHAN_WORKERS: set = set()


def _release_orphan(worker: NvidiaApiTestWorker) -> None:
    _ORPHAN_WORKERS.discard(worker)


#: 关闭对话框时仍在联网拉取模型列表的 fetcher，移交此集合，等其自然结束后
#: 由 finished->deleteLater 自毁（绝不能 delete 一个仍在运行的线程）。
_ORPHAN_FETCHERS: set = set()

#: 程序生命周期内是否已自动拉取过一次模型列表；首次打开对话框时触发，
#: 之后打开复用已拉取结果，不再每次自动联网（手动「刷新列表」仍可随时更新）。
#: 为 ``None`` 表示尚未拉取；首次拉取成功后写入实际列表（含空列表），
#: 之后任何一次进入对话框都直接复用，不再自动联网，直到进程结束。
_MODEL_LIST_CACHE: Optional[List[str]] = None

#: 程序生命周期内已测试模型的最后一次结果，跨多次打开对话框保留状态
#: （与 _MODEL_LIST_CACHE 同作用域，仅进程内有效）。
_MODEL_TEST_RESULTS: Dict[str, dict] = {}


class _ModelListFetcher(QThread):
    """后台拉取模型列表，避免翻页网络请求阻塞主线程（§6.5 刷新列表）。"""

    models_ready = Signal(list)
    fetch_failed = Signal(str)

    def __init__(self, timeout: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._timeout = timeout

    def run(self) -> None:
        try:
            models = fetch_text_summary_models(timeout=self._timeout)
            self.models_ready.emit(models)
        except Exception as e:  # 兜底：极端情况下 fetch_text_summary_models 仍抛错
            logger.debug("Failed to refresh model list: %s", e)
            self.fetch_failed.emit(str(e))


# ============================================================
# 对话框
# ============================================================


_DIALOG_QSS = """
QDialog {
    background-color: #f0f2f5;
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
}
#toolBar, #statusBar {
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
}
#primaryBtn {
    background: #1976d2;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 6px 18px;
    font-size: 13px;
    font-weight: 500;
}
#primaryBtn:hover { background: #1565c0; }
#primaryBtn:pressed { background: #0d47a1; }
#primaryBtn:disabled { background: #bdbdbd; }
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


class NvidiaApiTestDialog(QDialog):
    """Nvidia API 响应测试对话框：卡片式展示文本总结可用模型的可用性与延迟。

    模型列表默认在打开时通过 NGC catalog 接口动态拉取（见 `fetch_text_summary_models`），
    也可随时点「刷新列表」重新拉取并重建卡片（§6.5 的可选增强）。
    """

    def __init__(self, parent: Optional[QWidget] = None, settings: Optional[Settings] = None):
        super().__init__(parent)
        global _MODEL_LIST_CACHE
        self._settings = settings or Settings()
        self._api_url = self._settings.get(
            "summarization.nvidia_api_url", _DEFAULT_API_URL
        )
        self._timeout = TEST_TIMEOUT
        self._max_workers = max(
            1,
            min(
                self._settings.get_int("summarization.nvidia_thread_count", MAX_CONCURRENCY),
                MAX_CONCURRENCY,
            ),
        )
        self._has_api_key = bool(get_api_key("NVIDIA_API_KEY"))
        self._cards: Dict[str, ModelCard] = {}
        # 进程内缓存的模型列表：首次打开时为 ``None``，随后立即在后台拉取；
        # 再次打开对话框直接复用缓存（含空列表），保证以在线结果为准且不重复联网。
        # 本次打开前若已缓存，则 UI 直接按缓存构建；否则先用空列表构建占位卡片区域，
        # 拉取到位后重建（首次拉取结果无论是否为空都会写入缓存）。
        cache_hit = _MODEL_LIST_CACHE is not None
        self._models: List[str] = list(_MODEL_LIST_CACHE) if cache_hit else []
        self._worker: Optional[NvidiaApiTestWorker] = None
        self._list_fetcher: Optional["_ModelListFetcher"] = None
        self._fetch_notify: bool = False
        self._card_area: Optional[QScrollArea] = None
        self._card_host: Optional[QWidget] = None
        self._card_grid: Optional[QGridLayout] = None
        self._empty_label: Optional[QLabel] = None

        self.setWindowTitle(t("nvidia_test.title"))
        self.setStyleSheet(_DIALOG_QSS)
        # 仅允许最大化按钮（不提供最小化），方便在宽屏下查看更多卡片
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.resize(1020, 720)

        self._refresh_icon = self._load_refresh_icon()
        self._init_ui(is_loading=not cache_hit)
        self._update_progress()
        # 仅在进程内尚未拉取过时自动联网；之后打开直接复用缓存（以在线结果为准），
        # 用户仍可随时点「刷新列表」手动更新缓存。
        if not cache_hit:
            self._start_model_fetch(notify=False)

    # ── UI 构建 ──

    def _load_refresh_icon(self) -> QIcon:
        icon_path = get_base_dir() / "assets" / "refresh.png"
        if not icon_path.exists():
            # 开发态下 get_base_dir() 已指向项目根，这里再兜底一次相对路径
            icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "refresh.png"
        if not icon_path.exists():
            logger.warning("Refresh icon not found: %s (run `python -m src.utils.generate_icon --widgets` to generate it)", icon_path)
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
            t("nvidia_test.api_key_ok") if self._has_api_key else t("nvidia_test.api_key_missing")
        )
        key_label.setObjectName("infoLabel")
        key_label.setStyleSheet(
            "color: #388e3c;" if self._has_api_key else "color: #d32f2f; font-weight: 600;"
        )
        layout.addWidget(key_label)

        timeout_label = QLabel(t("nvidia_test.timeout_label", seconds=self._timeout))
        timeout_label.setObjectName("infoLabel")
        layout.addWidget(timeout_label)

        concurrency_label = QLabel(
            t("nvidia_test.concurrency_label", workers=self._max_workers)
        )
        concurrency_label.setObjectName("infoLabel")
        layout.addWidget(concurrency_label)

        self._current_model_label = QLabel(
            t(
                "nvidia_test.current_model",
                model=self._settings.get("summarization.nvidia_model", "-"),
            )
        )
        self._current_model_label.setObjectName("infoLabel")
        layout.addWidget(self._current_model_label, 1)

        self._refresh_list_btn = QPushButton(t("nvidia_test.refresh_list"))
        self._refresh_list_btn.setObjectName("secondaryBtn")
        self._refresh_list_btn.clicked.connect(self._on_refresh_list)
        layout.addWidget(self._refresh_list_btn)

        self._test_all_btn = QPushButton(t("nvidia_test.one_click"))
        self._test_all_btn.setObjectName("primaryBtn")
        self._test_all_btn.clicked.connect(self._on_test_all)
        layout.addWidget(self._test_all_btn)

        self._stop_btn = QPushButton(t("nvidia_test.stop"))
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

    def _populate_cards(self, models: Sequence[str], is_loading: bool = False) -> None:
        """清空并重建卡片网格（用于初次构建与「刷新列表」）。

        ``models`` 为空时按 ``is_loading`` 区分两种占位：
          - ``True``（默认）：展示「正在获取在线模型列表…」提示，对应首次打开
            或手动刷新期间，避免在等待结果时误判为「无可用模型」；
          - ``False``：展示「无可用在线模型」空状态，并禁用「一键测试」按钮，
            避免在无可测对象时误发请求。
        """
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
        for index, model in enumerate(models):
            card = ModelCard(model, self._refresh_icon, self._card_host)
            card.retest_clicked.connect(self._on_retest)
            card.apply_requested.connect(self._on_apply_model)
            self._cards[model] = card
            self._card_grid.addWidget(card, index // _CARD_COLUMNS, index % _CARD_COLUMNS)
            # 复用已测试模型的历史结果，跨对话框打开保留状态
            prev = _MODEL_TEST_RESULTS.get(model)
            if prev is not None:
                card.set_result(
                    prev["status"], prev["latency"], prev["snippet"], prev["error"]
                )

        for column in range(_CARD_COLUMNS):
            self._card_grid.setColumnStretch(column, 1)
        self._card_grid.setRowStretch(self._card_grid.rowCount(), 1)

    def _show_empty_label(self, loading: bool = False) -> None:
        """在卡片区域居中显示空状态提示。

        ``loading=True`` 时显示「正在获取…」（首次打开 / 手动刷新期间）；
        ``loading=False`` 时显示「无可用在线模型」（拉取结果确认为空时）。
        """
        if self._card_grid is None:
            return
        key = "nvidia_test.models_loading" if loading else "nvidia_test.models_empty"
        label = QLabel(t(key))
        label.setObjectName("modelsEmptyLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            "color: #9e9e9e; font-size: 14px; padding: 32px;"
        )
        # 跨所有列、跨所有行居中显示
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
        hint = QLabel(t("nvidia_test.card_tooltip"))
        hint.setObjectName("infoLabel")
        layout.addWidget(hint)
        return bar

    # ── 线程管理 ──

    def _ensure_worker(self) -> NvidiaApiTestWorker:
        if self._worker is None:
            worker = NvidiaApiTestWorker(
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
        # 仍有请求在途（最多 timeout 秒）：留一个强引用，等它自然退出后再回收
        _ORPHAN_WORKERS.add(worker)
        worker.finished.connect(lambda: _release_orphan(worker))
        worker.finished.connect(worker.deleteLater)
        if worker.isFinished():
            _release_orphan(worker)

    # ── 槽函数 ──

    def _on_refresh_list(self) -> None:
        """重新拉取模型列表并重建卡片（§6.5 可选增强）。"""
        self._start_model_fetch(notify=True)

    def _start_model_fetch(self, notify: bool) -> None:
        """后台拉取最新模型列表；`notify` 为真时结果/失败以弹窗提示。"""
        if self._list_fetcher is not None and self._list_fetcher.isRunning():
            return
        self._fetch_notify = notify
        self._refresh_list_btn.setEnabled(False)
        # 拉取期间禁用「一键测试」与所有卡片的「重测」按钮，刷新完成（或失败回退）后才恢复
        self._test_all_btn.setEnabled(False)
        self._set_cards_retest_enabled(False)
        self._progress_label.setText(t("nvidia_test.refresh_list_loading"))
        fetcher = _ModelListFetcher(self._timeout, self)
        self._list_fetcher = fetcher
        fetcher.models_ready.connect(self._on_models_ready)
        fetcher.fetch_failed.connect(self._on_models_fetch_failed)
        fetcher.finished.connect(fetcher.deleteLater)
        fetcher.finished.connect(lambda: _ORPHAN_FETCHERS.discard(fetcher))
        fetcher.start()

    def _on_models_ready(self, models: List[str]) -> None:
        # 拉取线程自然结束后由 finished->deleteLater 自毁，此处清空引用避免悬空
        self._list_fetcher = None
        notify = self._fetch_notify
        self._fetch_notify = False
        # 写入进程内缓存：含空列表（表示「拉取成功但当前无模型」），后续打开直接复用
        global _MODEL_LIST_CACHE
        _MODEL_LIST_CACHE = list(models)
        self._models = list(models)
        self._populate_cards(self._models)
        self._refresh_list_btn.setEnabled(True)
        # 刷新结束：按当前是否有在跑的测试恢复「一键测试」/「停止」按钮
        self._set_busy(self._has_active_cards())
        self._update_progress()
        if notify:
            QMessageBox.information(
                self,
                t("nvidia_test.title"),
                t("nvidia_test.refresh_list_done", count=len(models)),
            )

    def _on_models_fetch_failed(self, error: str) -> None:
        self._list_fetcher = None
        notify = self._fetch_notify
        self._fetch_notify = False
        # 写入进程内缓存为空列表，避免下次打开重复触发同一次失败的网络请求
        global _MODEL_LIST_CACHE
        _MODEL_LIST_CACHE = []
        self._models = []
        self._populate_cards(self._models)
        self._refresh_list_btn.setEnabled(True)
        # 按当前是否有在跑的测试恢复「一键测试」/「停止」按钮
        self._set_busy(self._has_active_cards())
        self._update_progress()
        if notify:
            QMessageBox.warning(
                self,
                t("nvidia_test.title"),
                t("nvidia_test.refresh_list_failed", error=error),
            )

    def _set_cards_retest_enabled(self, enabled: bool) -> None:
        """批量设置所有卡片「重测」按钮的可用状态。

        拉取模型列表期间设为 False；完成/失败后恢复为 True。
        仍在排队/测试中的卡片（用户正在测）保持禁用，避免重复提交。
        """
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
        # 尚未开始的卡片回到「待测试」；已在途的等其自然回报
        for card in self._cards.values():
            if card.status == "queued":
                card.set_pending()
        # 若此刻并无在途请求，worker 不会再发 batch_finished，需要立即恢复按钮
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
                "Nvidia API test failed: %s -> %s",
                result.get("model", "?"),
                result.get("error", ""),
            )
        # 记录终态结果（跨对话框打开保留状态）
        if result.get("status") in _FINAL_STATUSES:
            _MODEL_TEST_RESULTS[result.get("model", "")] = {
                "status": result.get("status", "fail"),
                "latency": float(result.get("latency", 0.0)),
                "snippet": result.get("snippet", ""),
                "error": result.get("error", ""),
            }
        self._update_progress()

    def _on_batch_finished(self) -> None:
        # 单卡重测可能紧跟在上一批之后提交，此时不应误判为空闲
        self._set_busy(self._has_active_cards())
        self._update_progress()

    def _on_apply_model(self, model: str) -> None:
        """双击「成功」卡片：直接把该模型写入配置，不做弹窗确认/提示。"""
        try:
            self._settings.set("summarization.nvidia_model", model)
            self._settings.save()
        except Exception as e:
            logger.warning("Failed to write NVIDIA summary model: %s", e)
            return
        self._current_model_label.setText(t("nvidia_test.current_model", model=model))

    # ── 辅助 ──

    def _require_api_key(self) -> bool:
        if self._has_api_key:
            return True
        QMessageBox.warning(
            self, t("nvidia_test.title"), t("nvidia_test.api_key_missing_msg")
        )
        return False

    def _has_active_cards(self) -> bool:
        """是否仍有排队中或测试中的模型。"""
        return any(card.status in ("queued", "testing") for card in self._cards.values())

    def _set_busy(self, busy: bool) -> None:
        # 单卡「重新测试」在批量测试期间仍可用（受全局并发上限约束），
        # `_on_retest` 会拒绝已在排队/测试中的卡片。
        self._test_all_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)

    def _update_progress(self) -> None:
        total = len(self._cards)
        counts = {status: 0 for status in _STATUS_COLORS}
        for card in self._cards.values():
            counts[card.status] += 1
        done = sum(counts[status] for status in _FINAL_STATUSES)
        self._progress_label.setText(
            t(
                "nvidia_test.progress",
                done=done,
                total=total,
                ok=counts["ok"],
                fail=counts["fail"],
                limited=counts["rate_limited"],
            )
        )

    # ── 生命周期 ──

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._shutdown_fetcher()
        self._shutdown_worker()
        super().closeEvent(event)

    def reject(self) -> None:
        self._shutdown_fetcher()
        self._shutdown_worker()
        super().reject()

    def _shutdown_fetcher(self) -> None:
        """对话框关闭时断开模型列表拉取线程；若仍在联网则移交孤儿集合等其自然结束后自毁。"""
        fetcher = self._list_fetcher
        self._list_fetcher = None
        if fetcher is None:
            return
        for signal in (fetcher.models_ready, fetcher.fetch_failed):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        try:
            running = fetcher.isRunning()
        except RuntimeError:
            # C++ 对象已被 Qt 异步回收（finished->deleteLater），忽略
            return
        if running:
            # 仍在联网：绝不能 delete 一个运行中的线程（否则触发
            # "Destroyed while thread is still running"）；finished->deleteLater
            # 已连接，待其联网结束会自然自毁。
            _ORPHAN_FETCHERS.add(fetcher)
            return
        try:
            fetcher.deleteLater()
        except RuntimeError:
            pass
