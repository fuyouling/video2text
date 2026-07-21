"""VoiceToText 主界面 Widget —— 语音录入、流式转写、对话管理"""

import html
import json
import random
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, QObject, Signal, Qt
from PySide6.QtGui import QPainter, QPixmap, QColor, QBrush, QTextCursor, QTextBlockUserData
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from src.i18n import t
from src.ui.background_content import BackgroundContent
from src.config.settings import Settings
from src.services.voice_recorder import VoiceRecorder
from src.services.voice_transcription import (
    VoiceTranscriptionService,
)
from src.storage.voice_conversation_store import (
    VoiceConversation,
    VoiceConversationStore,
    VoiceMessage,
)
from src.summarization.providers import create_provider
from src.utils.exceptions import Video2TextError
from src.utils.logger import get_logger

from src.utils.paths import get_base_dir as _get_base_dir

logger = get_logger(__name__)

_USER_PREFIX = t("voice.user_prefix")
_SUMMARY_PREFIX = t("voice.summary_prefix")

# ── 状态颜色映射 ─────────────────────────────────────────────────
_STATUS_COLORS = {
    "ok": ("#388e3c", "#e8f5e9"),
    "warn": ("#f57c00", "#fff3e0"),
    "error": ("#d32f2f", "#ffebee"),
    "busy": ("#1976d2", "#e3f2fd"),
    "info": ("#616161", "#f5f5f5"),
}


class _SignalBridge(QObject):
    result = Signal(str)
    error = Signal(str)


class _WaveformWidget(QWidget):
    """音频录入动态波纹控件 —— 柱状高度随录音音量实时变化（增强版）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._bars = 20
        self._values = [0.0] * self._bars
        self._target_volume = 0.0
        self._level_volume = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._update_values)
        self.setFixedHeight(90)

    def start(self) -> None:
        self._active = True
        self._target_volume = 0.0
        self._level_volume = 0.0
        self._timer.start()

    def stop(self) -> None:
        self._active = False
        self._timer.stop()
        self._values = [0.0] * self._bars
        self._target_volume = 0.0
        self._level_volume = 0.0
        self.update()

    def update_volume(self, volume: float) -> None:
        self._target_volume = volume * 7.0
        self._level_volume = min(1.0, max(self._level_volume, self._target_volume * 0.7))

    def _update_values(self) -> None:
        resting = self._level_volume < 0.06
        if resting:
            self._level_volume *= 0.85
            for i in range(self._bars):
                self._values[i] += (0.0 - self._values[i]) * 0.25
        else:
            for i in range(self._bars):
                spread = self._level_volume * 0.85
                variation = random.uniform(-spread, spread)
                target = max(0.05, min(1.0, self._target_volume + variation))
                self._values[i] += (target - self._values[i]) * 0.40
            self._level_volume *= 0.90
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        gap = 3
        bar_w = max(3, (w - (self._bars + 1) * gap) / self._bars)
        painter.setPen(Qt.PenStyle.NoPen)
        for i, val in enumerate(self._values):
            x = gap + i * (bar_w + gap)
            if self._active:
                bar_h = max(4, val * (h - 10))
                alpha = int(60 + val * 195)
                color = QColor(25, 118, 210, alpha)
            else:
                bar_h = 4
                color = QColor(25, 118, 210, 60)
            y = (h - bar_h) / 2
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(int(x), int(y), int(bar_w), int(bar_h), 3, 3)
        painter.end()


class _BlockData(QTextBlockUserData):
    def __init__(self, msg_uuid: str = ""):
        super().__init__()
        self.msg_uuid = msg_uuid


class VoiceToTextWidget(QWidget):

    def __init__(self, settings: Optional[Settings] = None, parent=None):
        super().__init__(parent)
        self._settings = settings or Settings()
        self._store = VoiceConversationStore(self._settings)
        self._transcription = VoiceTranscriptionService(self._settings)

        self._current_conv_id: Optional[str] = None
        self._recording: bool = False
        self._multi_select_mode: bool = False
        self._editing_msg_uuid: Optional[str] = None
        self._model_loaded: bool = False
        self._closing: bool = False  # 应用正在关闭，跳过耗时操作

        self._recorder: Optional[VoiceRecorder] = None

        self._realtime_timer = QTimer(self)
        self._realtime_timer.setInterval(
            self._settings.get_int(
                "voice_to_text.realtime_auto_send_interval", 10
            )
            * 1000
        )
        self._realtime_timer.timeout.connect(self._on_realtime_chunk)

        self._vad_enabled = self._settings.get_bool(
            "voice_to_text.vad_endpoint_detection", True
        )

        self._recording_seconds: int = 0
        self._recording_timer = QTimer(self)
        self._recording_timer.setInterval(1000)
        self._recording_timer.timeout.connect(self._on_recording_tick)

        self._last_transcribed_text = ""

        # ── 背景图片 ───────────────────────────────────────────────────────
        self._bg_pixmap: Optional[QPixmap] = None
        self._bg_opacity: float = 0.4
        self._bg_image_path: str = ""

        self._init_ui()
        self._refresh_history()
        self._set_status(t("voice.status.initializing"), "info")
        self._load_bg_settings()

    # ── UI 构建 ────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        # BackgroundContent 作为根部容器，支持背景图片
        self._bg_content = BackgroundContent(self)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(self._bg_content)

        bg_layout = QVBoxLayout(self._bg_content)
        bg_layout.setContentsMargins(8, 8, 8, 0)
        bg_layout.setSpacing(0)

        self._load_styles()

        content_layout = QHBoxLayout()
        content_layout.setSpacing(8)

        sidebar = self._build_sidebar()
        main_area = self._build_main_area()
        control_bar = self._build_control_bar()

        right_layout = QVBoxLayout()
        right_layout.setSpacing(0)
        right_layout.addWidget(main_area, 1)
        right_layout.addWidget(control_bar)

        content_layout.addWidget(sidebar)
        content_layout.addLayout(right_layout, 1)

        bg_layout.addLayout(content_layout, 1)

    def _build_sidebar(self) -> QWidget:
        group = QGroupBox(t("voice.sidebar.title"))
        group.setObjectName("sidebarGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 16, 8, 8)
        layout.setSpacing(0)

        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_list.itemClicked.connect(self._on_history_selected)
        self.history_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.history_list.customContextMenuRequested.connect(
            self._on_history_context_menu
        )
        layout.addWidget(self.history_list, 1)

        group.setFixedWidth(280)
        return group

    def _build_main_area(self) -> QWidget:
        group = QGroupBox()
        group.setObjectName("mainAreaGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        title_bar = self._build_title_bar()
        layout.addWidget(title_bar)

        self.chat_display = QTextEdit()
        self.chat_display.setObjectName("chatDisplay")
        self.chat_display.setReadOnly(True)
        self.chat_display.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.chat_display.customContextMenuRequested.connect(
            self._on_chat_context_menu
        )
        self.chat_display.setPlaceholderText(
            t("voice.chat.placeholder")
        )
        layout.addWidget(self.chat_display, 1)

        return group

    def _build_title_bar(self) -> QWidget:
        """头部操作栏：[返回主界面] [新建] [开始录音]  status_label  recording_time"""
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(8)

        self.back_btn = QPushButton(t("voice.back_main"))
        self.back_btn.setObjectName("backBtn")
        self.back_btn.clicked.connect(self._on_back)
        bar_layout.addWidget(self.back_btn)

        new_btn = QPushButton(t("voice.new_conv"))
        new_btn.setObjectName("newConvBtn")
        new_btn.clicked.connect(self._on_new_conversation)
        bar_layout.addWidget(new_btn)

        self.record_btn = QPushButton("🎤 " + t("voice.record_start"))
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.clicked.connect(self._on_record_toggled)
        bar_layout.addWidget(self.record_btn)

        bar_layout.addSpacing(12)

        self.status_label = QLabel(t("voice.status.initializing"))
        self.status_label.setObjectName("statusLabel")
        self._apply_status_style("info")
        bar_layout.addWidget(self.status_label)

        self.recording_time_label = QLabel("00:00")
        self.recording_time_label.setObjectName("recordingTime")
        bar_layout.addWidget(self.recording_time_label)

        bar_layout.addStretch()

        return bar

    def _build_control_bar(self) -> QWidget:
        """底部波形区"""
        group = QGroupBox("")
        group.setObjectName("controlGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        self.waveform = _WaveformWidget()
        self.waveform.setObjectName("waveformWidget")
        layout.addWidget(self.waveform)

        return group

    # ── 样式 ────────────────────────────────────────────────────────────────

    def _load_styles(self) -> None:
        style_path = Path(__file__).parent / "styles" / "voice_to_text.qss"
        if style_path.exists():
            self.setStyleSheet(style_path.read_text(encoding="utf-8"))

    def _apply_status_style(self, level: str) -> None:
        """应用状态颜色到 status_label"""
        if level not in _STATUS_COLORS:
            level = "info"
        color, bg = _STATUS_COLORS[level]
        self.status_label.setStyleSheet(
            f"font-size:12px;font-weight:500;padding:2px 8px;"
            f"border-radius:10px;color:{color};background:{bg};"
        )

    # ── 状态栏 ──────────────────────────────────────────────────────────────

    def _set_status(self, text: str, level: str = "info") -> None:
        """统一状态标签更新"""
        self.status_label.setText(text)
        self._apply_status_style(level)

    # ── 计时器 ──────────────────────────────────────────────────────────────

    def _on_recording_tick(self) -> None:
        self._recording_seconds += 1
        m, s = divmod(self._recording_seconds, 60)
        self.recording_time_label.setText(f"{m:02d}:{s:02d}")

    # ── 新建会话 ────────────────────────────────────────────────────────────

    def _on_new_conversation(self) -> None:
        if self._editing_msg_uuid:
            self._editing_msg_uuid = None
            self.chat_display.setReadOnly(True)
        self._current_conv_id = None
        self._last_transcribed_text = ""
        self.chat_display.clear()
        self._recording_seconds = 0
        self.recording_time_label.setText("00:00")
        self._set_status(t("voice.status.ready"), "ok")
        self._refresh_history()

    # ── 录音控制 ────────────────────────────────────────────────────────────

    def _on_record_toggled(self) -> None:
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        try:
            if not self._model_loaded:
                self._set_status(t("voice.status.model_not_ready"), "warn")
                return

            self._recorder = VoiceRecorder(
                sample_rate=16000, channels=1, settings=self._settings,
            )
            self._recorder.finished.connect(self._on_record_finished)
            self._recorder.error_occurred.connect(self._on_record_error)
            self._recorder.volume_changed.connect(self.waveform.update_volume)
            self._recorder.speech_ended.connect(self._on_speech_ended)

            self._recording = True
            self._recording_seconds = 0
            self.recording_time_label.setText("00:00")
            self._recording_timer.start()

            self.waveform.start()

            if self._vad_enabled:
                self._set_status(t("voice.status.recording_vad"), "error")
            else:
                self._realtime_timer.start()
                self._set_status(t("voice.status.recording_timer"), "error")

            self.record_btn.setText("⏹ " + t("voice.record_stop"))
            self._recorder.start()

        except Exception as exc:
            logger.error("启动录音失败: %s", exc)
            QMessageBox.warning(
                self, t("voice.dialog.record_fail.title"), t("voice.dialog.record_fail.msg", error=str(exc))
            )
            self._recording = False
            self.record_btn.setText("🎤 " + t("voice.record_start"))
            self._recording_timer.stop()
            self.waveform.stop()
            self._set_status(t("voice.status.record_fail", error=str(exc)), "error")

    def _stop_recording(self) -> None:
        self._recording = False
        self._realtime_timer.stop()
        self._recording_timer.stop()

        self.waveform.stop()

        if self._recorder is not None:
            try:
                self._recorder.speech_ended.disconnect(self._on_speech_ended)
            except (RuntimeError, TypeError):
                pass
            self._recorder.stop()
            if not self._closing:
                # 检查缓冲区是否有有效音频，避免将尾部静音/噪声送入 Whisper 产生幻觉文字
                buffer_rms = self._recorder.get_buffer_rms()
                noise_floor = (
                    getattr(self._recorder, "_noise_floor", None)
                    or 0.005
                )
                energy_threshold = max(noise_floor * 2.0, 0.01)

                if buffer_rms > energy_threshold:
                    # 缓冲区含有有效音频，提取并转写
                    chunk_path = self._recorder.extract_chunk()
                    if chunk_path:
                        self._transcribe_async(
                            chunk_path,
                            previous_text=self._last_transcribed_text,
                        )
                else:
                    # 缓冲区能量太低（静音/噪声），直接丢弃，不送入转写
                    self._recorder.extract_chunk()

        self.record_btn.setText("🎤 " + t("voice.record_start"))
        self._set_status(t("voice.status.stopped"), "info")

    def _on_record_finished(self, wav_path: str) -> None:
        try:
            self.record_btn.setText("🎤 " + t("voice.record_start"))
            self._recording_timer.stop()
            self.waveform.stop()
            self._set_status(t("voice.status.recording_done"), "busy")
            self._transcribe_async(wav_path)
        except Exception:
            logger.error("on_record_finished Exception:\n%s", traceback.format_exc())

    def _on_record_error(self, msg: str) -> None:
        if "__NO_AUDIO_DATA__" in msg:
            return
        try:
            self._recording = False
            self.record_btn.setText("🎤 " + t("voice.record_start"))
            self._realtime_timer.stop()
            self._recording_timer.stop()
            self.waveform.stop()
            QMessageBox.warning(self, t("voice.dialog.record_error.title"), msg)
        except Exception:
            logger.error("on_record_error 异常:\n%s", traceback.format_exc())

    def _cleanup_recorder(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()

    # ── 流式转录 ──────────────────────────────────────────────────────────

    def _on_realtime_chunk(self) -> None:
        """定时切片（VAD 关闭时回退）"""
        if not self._recording or self._recorder is None:
            return
        chunk_path = self._recorder.extract_chunk()
        if chunk_path:
            self._transcribe_async(chunk_path, previous_text=self._last_transcribed_text)

    def _on_speech_ended(self, chunk_path: str) -> None:
        """VAD 检测到语音结束"""
        if not self._recording:
            return
        self._transcribe_async(chunk_path, previous_text=self._last_transcribed_text)

    # ── 转写 (threading.Thread, 无 QThread) ────────────────────────────────

    def _transcribe_async(self, wav_path: str, previous_text: str = "") -> None:
        self._set_status(t("voice.status.transcribing"), "busy")
        bridge = _SignalBridge()

        def _on_done(text: str) -> None:
            try:
                self._on_transcribe_done(text, wav_path)
            except Exception:
                logger.error("_on_transcribe_done 异常:\n%s", traceback.format_exc())
            bridge.deleteLater()

        def _on_err(err: str) -> None:
            try:
                self._on_transcribe_error(err, wav_path)
            except Exception:
                logger.error("_on_transcribe_error 异常:\n%s", traceback.format_exc())
            bridge.deleteLater()

        bridge.result.connect(_on_done)
        bridge.error.connect(_on_err)

        def _worker():
            try:
                result = self._transcription.transcribe_file(
                    wav_path, previous_text=previous_text,
                )
                bridge.result.emit(result)
            except Exception as exc:
                bridge.error.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_transcribe_done(self, text: str, wav_path: str) -> None:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception:
            pass

        # 未检测到语音内容不显示在聊天界面
        if text.startswith("__NO_SPEECH__"):
            self._set_status(t("voice.status.no_speech"), "info")
            return

        self._last_transcribed_text = text

        if self._current_conv_id is None:
            self._current_conv_id = self._store.create_conversation(text, mode="realtime")
            self._refresh_history()
            conv = self._store.get_conversation(self._current_conv_id)
            msg_uuid = conv.messages[0].uuid if conv and conv.messages else ""
        else:
            msg = VoiceMessage(
                role="user",
                content=text,
                uuid=uuid.uuid4().hex[:16],
                mode="realtime",
                timestamp=datetime.now().timestamp(),
            )
            self._store.append_message(self._current_conv_id, msg)
            msg_uuid = msg.uuid

        self._append_user(text, msg_uuid=msg_uuid)
        self._refresh_history()
        self._set_status(t("voice.status.transcribe_done"), "ok")

    def _on_transcribe_error(self, err: str, wav_path: str) -> None:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception:
            pass
        self._set_status(t("voice.status.transcribe_fail", error=err), "error")

    # ── 对话显示 ────────────────────────────────────────────────────────────

    def _append_user(self, text: str, msg_uuid: str = "") -> None:
        self._append_message("user", text, label=_USER_PREFIX, msg_uuid=msg_uuid)

    def _append_assistant(self, text: str, label: str = "", msg_uuid: str = "") -> None:
        self._append_message("assistant", text, label=label, msg_uuid=msg_uuid)

    def _append_system(self, text: str) -> None:
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(
            f'<div style="color:#9e9e9e;font-style:italic;font-size:12px;'
            f'padding:4px 0;text-align:center;">── {html.escape(text)} ──</div>'
        )
        self._scroll_to_bottom()

    def _append_message(self, role: str, text: str, label: str = "", msg_uuid: str = "") -> None:
        escaped = html.escape(text)
        label_html = ""
        if label:
            label_html = (
                f'<span style="color:#1976d2;font-weight:500">'
                f'{html.escape(label)}</span> '
            )

        if role == "user":
            html_text = (
                f'<table cellpadding="0" cellspacing="0" style="margin:6px 0;">'
                f'<tr><td style="background:#e3f2fd;padding:10px 16px;'
                f'border-radius:12px;font-size:14px;line-height:1.6;color:#1a1a1a;">'
                f'{label_html}{escaped}</td></tr></table>'
            )
        else:
            html_text = (
                f'<table align="right" cellpadding="0" cellspacing="0" style="margin:6px 0;">'
                f'<tr><td style="background:white;padding:10px 16px;border-radius:12px;'
                f'font-size:14px;line-height:1.6;color:#1a1a1a;'
                f'border:1px solid #e0e0e0;">'
                f'{label_html}{escaped}</td></tr></table>'
            )

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html_text)
        block = cursor.block()
        if not block.text().strip() and block.position() > 0:
            block = self.chat_display.document().findBlock(block.position() - 1)
        block.setUserData(_BlockData(msg_uuid=msg_uuid))
        self.chat_display.setTextCursor(cursor)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        sb = self.chat_display.verticalScrollBar()
        auto_scroll = (sb.value() + sb.pageStep() >= sb.maximum() - 40)
        if auto_scroll:
            sb.setValue(sb.maximum())

    # ── 历史会话 ────────────────────────────────────────────────────────────

    def _refresh_history(self) -> None:
        self.history_list.clear()
        convs = self._store.list_conversations()
        for conv in convs:
            item = QListWidgetItem()
            widget = self._create_conv_item(conv)
            item.setData(Qt.ItemDataRole.UserRole, conv["id"])
            item.setSizeHint(widget.sizeHint())
            self.history_list.addItem(item)
            self.history_list.setItemWidget(item, widget)

    def _create_conv_item(self, conv: dict) -> QWidget:
        widget = QWidget()
        widget.setObjectName("convItem")
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(2)

        title_label = QLabel(conv.get("title", ""))
        title_label.setObjectName("convItemTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        updated = conv.get("updated_at", 0)
        msg_count = conv.get("message_count", 0)
        time_str = (
            datetime.fromtimestamp(updated).strftime("%m-%d %H:%M")
            if updated else ""
        )
        meta = t("voice.conv.meta", time=time_str, count=msg_count)
        meta_label = QLabel(meta)
        meta_label.setObjectName("convItemMeta")
        layout.addWidget(meta_label)

        return widget

    def _reload_conversation_display(self, conv: Optional[VoiceConversation] = None) -> None:
        if not self._current_conv_id:
            return
        if conv is None:
            conv = self._store.get_conversation(self._current_conv_id)
        if conv is None:
            return
        self.chat_display.clear()
        for msg in conv.messages:
            if msg.role == "user":
                self._append_user(msg.content, msg_uuid=msg.uuid)
            elif msg.role == "system":
                self._append_system(msg.content)
            else:
                if msg.corrected:
                    label = f"[{msg.role}]"
                    self._append_assistant(msg.content, label=label, msg_uuid=msg.uuid)
                else:
                    self._append_assistant(msg.content, label=_SUMMARY_PREFIX, msg_uuid=msg.uuid)

    def _on_history_selected(self, item: QListWidgetItem) -> None:
        if self._editing_msg_uuid:
            self._editing_msg_uuid = None
            self.chat_display.setReadOnly(True)
        if self._multi_select_mode:
            count = len(self.history_list.selectedItems())
            self._set_status(t("voice.status.selected_count", count=count))
            return
        conv_id = item.data(Qt.ItemDataRole.UserRole)
        conv = self._store.get_conversation(conv_id)
        if conv is None:
            return
        self._current_conv_id = conv_id
        self._reload_conversation_display()

    # ── 历史会话右键菜单 ────────────────────────────────────────────────────

    def _on_history_context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.setObjectName("historyMenu")

        if self._multi_select_mode:
            exit_select_action = menu.addAction(t("voice.menu.exit_multi_select"))
            delete_selected_action = menu.addAction(t("voice.menu.delete_selected"))
            select_all_action = menu.addAction(t("voice.menu.select_toggle_all"))
            menu.addSeparator()
            delete_all_action = menu.addAction(t("voice.menu.delete_all"))
        else:
            item = self.history_list.itemAt(pos)
            if item is None:
                multi_action = menu.addAction(t("voice.menu.multi_select"))
                delete_all_action = menu.addAction(t("voice.menu.delete_all"))
            else:
                multi_action = menu.addAction(t("voice.menu.multi_select"))
                delete_action = menu.addAction(t("voice.menu.delete_conv"))
                menu.addSeparator()
                delete_all_action = menu.addAction(t("voice.menu.delete_all"))

        action = menu.exec(self.history_list.mapToGlobal(pos))
        if action is None:
            return

        if self._multi_select_mode:
            if action == exit_select_action:
                self._toggle_multi_select(False)
            elif action == delete_selected_action:
                self._delete_selected_conversations()
            elif action == select_all_action:
                self._toggle_select_all()
            elif action == delete_all_action:
                self._delete_all_conversations()
        else:
            if action == multi_action:
                self._toggle_multi_select(True)
                item = self.history_list.itemAt(pos)
                if item is not None:
                    item.setSelected(True)
            elif action == delete_action:
                item = self.history_list.itemAt(pos)
                if item is not None:
                    conv_id = item.data(Qt.ItemDataRole.UserRole)
                    self._delete_conversation(conv_id)
            elif action == delete_all_action:
                self._delete_all_conversations()

    def _delete_conversation(self, conv_id: str) -> None:
        reply = QMessageBox.question(
            self,
            t("voice.dialog.delete_conv.title"),
            t("voice.dialog.delete_conv.msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._store.delete_conversation(conv_id)
            if self._current_conv_id == conv_id:
                self._current_conv_id = None
                self.chat_display.clear()
                self._set_status(t("voice.status.ready"), "ok")
            self._refresh_history()
            self._set_status(t("voice.status.conv_deleted"), "ok")
        except Exception as exc:
            logger.error(t("voice.log_delete_conv_fail", conv_id=conv_id, error=exc))
            QMessageBox.warning(self, t("voice.dialog.delete_fail.title"), t("voice.dialog.delete_fail.msg", error=str(exc)))

    def _toggle_multi_select(self, enable: bool) -> None:
        self._multi_select_mode = enable
        if enable:
            self.history_list.setSelectionMode(
                QListWidget.SelectionMode.MultiSelection
            )
            self._set_status(t("voice.status.multi_select"), "busy")
        else:
            self.history_list.setSelectionMode(
                QListWidget.SelectionMode.SingleSelection
            )
            self.history_list.clearSelection()
            self._set_status(t("voice.status.ready"), "ok")

    def _toggle_select_all(self) -> None:
        if not self._multi_select_mode:
            return
        all_selected = all(
            self.history_list.item(i).isSelected()
            for i in range(self.history_list.count())
        )
        if all_selected:
            self.history_list.clearSelection()
        else:
            for i in range(self.history_list.count()):
                self.history_list.item(i).setSelected(True)

    def _delete_selected_conversations(self) -> None:
        selected_items = self.history_list.selectedItems()
        if not selected_items:
            QMessageBox.information(self, t("common.hint"), t("voice.dialog.no_selection"))
            return
        count = len(selected_items)
        reply = QMessageBox.question(
            self,
            t("voice.dialog.batch_delete.title"),
            t("voice.dialog.batch_delete.msg", count=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = 0
        for item in selected_items:
            conv_id = item.data(Qt.ItemDataRole.UserRole)
            if not conv_id:
                continue
            try:
                self._store.delete_conversation(conv_id)
                if self._current_conv_id == conv_id:
                    self._current_conv_id = None
                    self.chat_display.clear()
                    deleted += 1
            except Exception as exc:
                logger.error(t("voice.log_delete_conv_fail", conv_id=conv_id, error=exc))
        self._refresh_history()
        self._set_status(t("voice.status.deleted_count", count=deleted), "ok")

    def _delete_all_conversations(self) -> None:
        count = self.history_list.count()
        if count == 0:
            QMessageBox.information(self, t("common.hint"), t("voice.dialog.no_convs"))
            return
        reply = QMessageBox.question(
            self,
            t("voice.dialog.delete_all.title"),
            t("voice.dialog.delete_all.msg", count=count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        convs = self._store.list_conversations()
        deleted = 0
        for conv in convs:
            try:
                self._store.delete_conversation(conv["id"])
                deleted += 1
            except Exception as exc:
                logger.error(t("voice.log_delete_conv_fail", conv_id=conv["id"], error=exc))
        self._current_conv_id = None
        self.chat_display.clear()
        self._toggle_multi_select(False)
        self._refresh_history()
        self._set_status(t("voice.status.deleted_count", count=deleted), "ok")

    # ── 聊天区右键菜单 ──────────────────────────────────────────────────────

    def _on_chat_context_menu(self, pos) -> None:
        cursor = self.chat_display.cursorForPosition(pos)
        block = cursor.block()
        text = block.text().strip()

        if not text:
            return

        is_system = text.startswith("── ")
        if is_system:
            return

        data = block.userData()
        msg_uuid = data.msg_uuid if isinstance(data, _BlockData) else ""

        is_assistant = text.startswith(_SUMMARY_PREFIX)

        for prefix in (_USER_PREFIX, _SUMMARY_PREFIX):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break

        menu = QMenu(self)
        menu.setObjectName("chatMenu")

        if self._editing_msg_uuid:
            save_action = menu.addAction(t("voice.menu.save"))
            cancel_action = menu.addAction(t("voice.menu.cancel_edit"))
            action = menu.exec(self.chat_display.mapToGlobal(pos))
            if action == save_action:
                self._save_edited_message()
            elif action == cancel_action:
                self._cancel_edit()
            return

        copy_action = menu.addAction(t("voice.menu.copy"))
        edit_action = menu.addAction(t("voice.menu.edit"))
        delete_action = menu.addAction(t("voice.menu.delete"))

        if is_assistant:
            action = menu.exec(self.chat_display.mapToGlobal(pos))
            if action == copy_action:
                self._copy_message_text(text)
            elif action == edit_action:
                self._start_edit_message(msg_uuid)
            elif action == delete_action:
                self._delete_message(msg_uuid)
            return

        menu.addSeparator()
        grammar_action = menu.addAction(t("voice.menu.grammar"))
        summary_action = menu.addAction(t("voice.menu.summarize"))
        action = menu.exec(self.chat_display.mapToGlobal(pos))

        if action == copy_action:
            self._copy_message_text(text)
        elif action == edit_action:
            self._start_edit_message(msg_uuid)
        elif action == delete_action:
            self._delete_message(msg_uuid)
        elif action == grammar_action:
            self._run_grammar_correction(text, msg_uuid)
        elif action == summary_action:
            self._run_summarization()

    def _start_edit_message(self, msg_uuid: str) -> None:
        if not msg_uuid or not self._current_conv_id:
            return
        self._editing_msg_uuid = msg_uuid
        self.chat_display.setReadOnly(False)
        self.chat_display.setFocus()
        self._set_status(t("voice.status.edit_mode"), "busy")

    def _save_edited_message(self) -> None:
        if not self._editing_msg_uuid or not self._current_conv_id:
            return

        conv = self._store.get_conversation(self._current_conv_id)
        if conv is None:
            self._editing_msg_uuid = None
            self.chat_display.setReadOnly(True)
            return

        msg_text = self._get_edited_text()
        if msg_text is None:
            return

        for msg in conv.messages:
            if msg.uuid == self._editing_msg_uuid:
                msg.content = msg_text
                conv.updated_at = datetime.now().timestamp()
                break

        self._store._save(conv)
        self._editing_msg_uuid = None
        self.chat_display.setReadOnly(True)
        self._reload_conversation_display(conv=conv)
        self._refresh_history()
        self._set_status(t("voice.status.msg_saved"), "ok")

    def _get_edited_text(self) -> Optional[str]:
        doc = self.chat_display.document()
        block = doc.firstBlock()
        while block.isValid():
            data = block.userData()
            if isinstance(data, _BlockData) and data.msg_uuid == self._editing_msg_uuid:
                text = block.text()
                for prefix in (_USER_PREFIX, _SUMMARY_PREFIX):
                    if text.startswith(prefix):
                        return text[len(prefix):]
                return text
            block = block.next()
        return None

    def _cancel_edit(self) -> None:
        if not self._editing_msg_uuid:
            return
        self._editing_msg_uuid = None
        self.chat_display.setReadOnly(True)
        self._reload_conversation_display()
        self._set_status(t("voice.status.edit_cancelled"), "info")

    def _copy_message_text(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self._set_status(t("voice.status.copied"), "ok")

    def _run_grammar_correction(self, text: str, original_uuid: str) -> None:
        self._set_status(t("voice.status.correcting"), "busy")
        provider = create_provider(self._settings)
        provider_name = self._settings.get("summarization.provider", "ollama")
        conv_id = self._current_conv_id

        custom_prompt = (
            "用户使用 faster-whisper 进行语音识别了一段录音,其中有些文字可能不正确。"
            "修正用户语音转写文本中的错别字和不通顺的语句，"
            "请以JSON格式输出，格式为："
            '{"source_text":"用户原文","update_text":"修正后的文本"}'
            "只输出JSON，不要添加任何其他内容。"
        )

        def _call():
            return provider.summarize(text=text, custom_prompt=custom_prompt, stream=False, is_use_gui_markdown_flag=False)

        self._start_api_call(_call, on_done=lambda c: self._on_grammar_done(c, provider_name, original_uuid, conv_id))

    def _on_grammar_done(self, result: str, provider_name: str, original_uuid: str, conv_id: Optional[str] = None) -> None:
        try:
            data = json.loads(result)
            display_text = data.get("update_text", result)
        except (json.JSONDecodeError, TypeError):
            display_text = result

        target_id = conv_id or self._current_conv_id
        modified_conv = None
        if target_id and original_uuid:
            conv = self._store.get_conversation(target_id)
            if conv:
                existing_msg = None
                for msg in conv.messages:
                    if msg.parent_uuid == original_uuid:
                        existing_msg = msg
                        break
                if existing_msg is not None:
                    existing_msg.content = display_text
                    existing_msg.corrected = True
                else:
                    new_msg = VoiceMessage(
                        role=provider_name,
                        content=display_text,
                        uuid=uuid.uuid4().hex[:16],
                        mode="realtime",
                        corrected=True,
                        parent_uuid=original_uuid,
                        timestamp=datetime.now().timestamp(),
                    )
                    conv.messages.append(new_msg)
                conv.updated_at = datetime.now().timestamp()
                self._store._save(conv)
                modified_conv = conv

        self._reload_conversation_display(conv=modified_conv)
        self._set_status(t("voice.status.correct_done"), "ok")

    def _delete_message(self, msg_uuid: str) -> None:
        if not self._current_conv_id or not msg_uuid:
            return
        reply = QMessageBox.question(
            self,
            t("voice.dialog.delete_msg.title"),
            t("voice.dialog.delete_msg.msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        conv = self._store.get_conversation(self._current_conv_id)
        if conv is None:
            return
        conv.messages = [m for m in conv.messages if m.uuid != msg_uuid]
        conv.updated_at = datetime.now().timestamp()
        self._store._save(conv)
        self._reload_conversation_display(conv=conv)
        self._refresh_history()
        self._set_status(t("voice.status.msg_deleted"), "ok")

    def _run_summarization(self) -> None:
        if self._current_conv_id is None:
            QMessageBox.warning(self, t("common.hint"), t("voice.dialog.select_conv_first"))
            return
        conv = self._store.get_conversation(self._current_conv_id)
        if conv is None or not conv.messages:
            return

        self._set_status(t("voice.status.summarizing"), "busy")
        provider = create_provider(self._settings)

        replaced_uuids = {m.parent_uuid for m in conv.messages if m.parent_uuid}
        effective_messages = [m for m in conv.messages if m.uuid not in replaced_uuids]
        effective_messages.sort(key=lambda m: m.timestamp)

        conversation_text = "\n".join(
            f"{'用户' if m.role == 'user' else '助手'}: {m.content}"
            for m in effective_messages
        )
        prompt = (
            "请将以下多轮对话内容归纳为一份结构清晰的 Markdown 文档。包含：\n"
            "输出要求：只输出 Markdown 内容本身，不要添加任何解释文字、前缀或后缀。\n\n"
            f"对话内容：\n{conversation_text}"
        )

        def _call():
            return provider.summarize(text=prompt, custom_prompt="", stream=False)

        self._start_api_call(_call, on_done=self._on_summary_done)

    def _on_summary_done(self, md_text: str) -> None:
        summary_dir = self._settings.get(
            "voice_to_text.summary_dir", "voice"
        )
        Path(summary_dir).mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = (
            f"{self._current_conv_id}_summary.md"
            if self._current_conv_id
            else f"voice_summary.md"
        )
        md_path = Path(summary_dir) / filename
        md_path.write_text(md_text, encoding="utf-8")

        self._append_assistant(
            t("voice.summary_saved", path=str(md_path)), label=_SUMMARY_PREFIX
        )
        if self._current_conv_id:
            self._store.update_summary_path(self._current_conv_id, str(md_path))
        self._set_status(t("voice.status.summary_done"), "ok")

    # ── 通用 API 调用 (threading.Thread, 无 QThread) ───────────────────────

    def _start_api_call(self, func, on_done) -> None:
        bridge = _SignalBridge()

        def _wrap_on_done(text: str) -> None:
            try:
                on_done(text)
            except Exception:
                logger.error("_start_api_call on_done 异常:\n%s",
                             traceback.format_exc())
            bridge.deleteLater()

        bridge.result.connect(_wrap_on_done)
        bridge.error.connect(
            lambda err: self._set_status(t("voice.status.op_fail", error=err), "error")
        )
        bridge.error.connect(bridge.deleteLater)

        def _worker():
            try:
                result = func()
                bridge.result.emit(result)
            except Exception as exc:
                bridge.error.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    # ── 模型加载 ──────────────────────────────────────────────────────────

    def load_model_async(self) -> None:
        """进入 VoiceToText 界面后异步加载模型"""
        if self._model_loaded:
            self._set_status(t("voice.status.ready"), "ok")
            return
        self._set_status(t("voice.status.loading_model"), "warn")
        self._preload_model()

    def _preload_model(self) -> None:
        bridge = _SignalBridge()

        def _on_loaded(_text: str) -> None:
            self._model_loaded = True
            self._set_status(t("voice.status.ready"), "ok")
            bridge.deleteLater()

        def _on_error(err: str) -> None:
            self._set_status(t("voice.status.model_load_fail", error=err), "error")
            bridge.deleteLater()

        bridge.result.connect(_on_loaded)
        bridge.error.connect(_on_error)

        def _worker():
            try:
                self._transcription.preload_model()
                bridge.result.emit("")
            except Exception as exc:
                bridge.error.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    # ── 返回主界面 ──────────────────────────────────────────────────────────

    def _on_back(self) -> None:
        if self._recording:
            self._stop_recording()
        self._cleanup_recorder()
        if self._editing_msg_uuid:
            self._editing_msg_uuid = None
            self.chat_display.setReadOnly(True)
        self.window()._on_back_to_main()

    # ── 背景图片 ──────────────────────────────────────────────────────────

    def _load_bg_settings(self) -> None:
        """从主界面配置加载背景图片设置（跟随主界面）"""
        try:
            path = self._settings.get("app.result_image_path", "")
            if path:
                p = Path(path)
                if not p.is_absolute():
                    p = _get_base_dir() / path
                if p.exists():
                    self._bg_pixmap = QPixmap(str(p))
                    self._bg_image_path = str(p)
                else:
                    self._bg_pixmap = None
                    self._bg_image_path = ""
            else:
                self._bg_pixmap = None
                self._bg_image_path = ""

            opacity_int = self._settings.get_int(
                "app.result_transparency", 100
            )
            self._bg_opacity = max(0.0, min(1.0, opacity_int / 255.0))
        except Exception:
            self._bg_pixmap = None
            self._bg_image_path = ""
            self._bg_opacity = 0.4

        if hasattr(self, "_bg_content"):
            self._bg_content.set_bg_pixmap(self._bg_pixmap)
            self._bg_content.set_bg_opacity(self._bg_opacity)
        self._apply_bg_transparency()

    def _apply_bg_transparency(self) -> None:
        """有背景图片时设置面板/控件透明，否则恢复默认样式"""
        has_bg = (
            self._bg_pixmap is not None
            and not self._bg_pixmap.isNull()
        )

        if has_bg:
            # 左侧 sidebar 组
            sidebar_group = self.findChild(QGroupBox, "sidebarGroup")
            if sidebar_group:
                sidebar_group.setStyleSheet(
                    "#sidebarGroup { background: transparent; border: 1px solid palette(mid); border-radius: 4px; }"
                )
            self.history_list.setStyleSheet(
                "#historyList { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }"
            )

            # 主区域（与底部控制区上下拼接，底部无圆角）
            main_group = self.findChild(QGroupBox, "mainAreaGroup")
            if main_group:
                main_group.setStyleSheet(
                    "#mainAreaGroup { background: transparent;"
                    " border: 1px solid palette(mid); border-radius: 4px;"
                    " border-bottom-left-radius: 0; border-bottom-right-radius: 0; }"
                )

            # 标题栏（在 mainAreaGroup 内部，只透明明无需单独外框）
            title_bar = self.findChild(QWidget, "titleBar")
            if title_bar:
                title_bar.setStyleSheet("#titleBar { background: transparent; }")

            # 聊天显示区
            self.chat_display.setStyleSheet(
                "#chatDisplay { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }"
            )
            if hasattr(self.chat_display, "viewport"):
                self.chat_display.viewport().setStyleSheet("background: transparent;")

            # 底部波形控制区（与主区域拼接，顶部无边框/圆角避免双线）
            control_group = self.findChild(QGroupBox, "controlGroup")
            if control_group:
                control_group.setStyleSheet(
                    "#controlGroup { background: transparent;"
                    " border: 1px solid palette(mid); border-radius: 4px;"
                    " border-top: none;"
                    " border-top-left-radius: 0; border-top-right-radius: 0; }"
                )
            # 波形控件
            waveform = self.findChild(QWidget, "waveformWidget")
            if waveform:
                waveform.setStyleSheet(
                    "#waveformWidget { background: transparent; border: 1px solid palette(mid); border-radius: 3px; }"
                )
        else:
            # 无背景图时恢复默认样式
            sidebar_group = self.findChild(QGroupBox, "sidebarGroup")
            if sidebar_group:
                sidebar_group.setStyleSheet("")
            self.history_list.setStyleSheet("")
            main_group = self.findChild(QGroupBox, "mainAreaGroup")
            if main_group:
                main_group.setStyleSheet("")
            title_bar = self.findChild(QWidget, "titleBar")
            if title_bar:
                title_bar.setStyleSheet("")
            self.chat_display.setStyleSheet("")
            if hasattr(self.chat_display, "viewport"):
                self.chat_display.viewport().setStyleSheet("")
            control_group = self.findChild(QGroupBox, "controlGroup")
            if control_group:
                control_group.setStyleSheet("")
            waveform = self.findChild(QWidget, "waveformWidget")
            if waveform:
                waveform.setStyleSheet("")

    def cleanup(self) -> None:
        self._closing = True
        if self._recording:
            self._stop_recording()
        self._cleanup_recorder()
        self._realtime_timer.stop()
