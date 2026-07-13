"""VoiceToText 主界面 Widget —— 语音录入、实时转写、对话管理"""

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

from PySide6.QtCore import QTimer, QObject, Signal, Qt, QEvent
from PySide6.QtGui import QPainter, QColor, QBrush, QTextCursor, QTextBlockUserData
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QComboBox,
)

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

logger = get_logger(__name__)

_USER_PREFIX = "[用户] "
_SUMMARY_PREFIX = "[归纳摘要] "

_RECORD_STYLE_IDLE = """
    #recordBtn {
        background: #1976d2;
        color: white;
        border: none;
        border-radius: 22px;
        min-width: 140px;
        min-height: 44px;
        font-size: 15px;
        font-weight: 600;
        padding: 8px 24px;
    }
    #recordBtn:hover { background: #1565c0; }
    #recordBtn:pressed { background: #0d47a1; }
"""

_RECORD_STYLE_ACTIVE = """
    #recordBtn {
        background: #f44336;
        color: white;
        border: none;
        border-radius: 22px;
        min-width: 140px;
        min-height: 44px;
        font-size: 15px;
        font-weight: 600;
        padding: 8px 24px;
    }
    #recordBtn:hover { background: #d32f2f; }
    #recordBtn:pressed { background: #b71c1c; }
"""


class _SignalBridge(QObject):
    result = Signal(str)
    error = Signal(str)


class _WaveformWidget(QWidget):
    """音频录入动态波纹控件 —— 柱状高度随录音音量实时变化"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._bars = 16
        self._values = [0.0] * self._bars
        self._target_volume = 0.0
        self._level_volume = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._update_values)
        self.setFixedHeight(52)

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
        self._target_volume = volume * 4.0
        self._level_volume = min(1.0, max(self._level_volume, self._target_volume * 0.6))

    def _update_values(self) -> None:
        resting = self._level_volume < 0.06
        if resting:
            self._level_volume *= 0.85
            for i in range(self._bars):
                self._values[i] += (0.0 - self._values[i]) * 0.25
        else:
            for i in range(self._bars):
                spread = self._level_volume * 0.7
                variation = random.uniform(-spread, spread)
                target = max(0.05, self._target_volume + variation)
                self._values[i] += (target - self._values[i]) * 0.35
            self._level_volume *= 0.92
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        gap = 3
        bar_w = max(2, (w - (self._bars + 1) * gap) / self._bars)
        painter.setPen(Qt.PenStyle.NoPen)
        for i, val in enumerate(self._values):
            x = gap + i * (bar_w + gap)
            if self._active:
                bar_h = max(3, val * (h - 6))
                alpha = int(80 + val * 175)
                color = QColor(25, 118, 210, alpha)
            else:
                bar_h = 3
                color = QColor(25, 118, 210, 60)
            y = (h - bar_h) / 2
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(int(x), int(y), int(bar_w), int(bar_h), 2, 2)
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
        self._mode: str = "normal"
        self._recording: bool = False
        self._multi_select_mode: bool = False
        self._editing_msg_uuid: Optional[str] = None

        self._recorder: Optional[VoiceRecorder] = None

        self._realtime_timer = QTimer(self)
        self._realtime_timer.setInterval(
            self._settings.get_int(
                "voice_to_text.realtime_auto_send_interval", 10
            )
            * 1000
        )
        self._realtime_timer.timeout.connect(self._on_realtime_chunk)

        self._recording_seconds: int = 0
        self._recording_timer = QTimer(self)
        self._recording_timer.setInterval(1000)
        self._recording_timer.timeout.connect(self._on_recording_tick)

        self._init_ui()
        self._refresh_history()
        self._update_record_btn_style(False)
        self._update_device_indicator_style(False)
        self.device_indicator.setText("● 加载模型中...")
        self.device_indicator.setStyleSheet(
            "font-size:11px;font-weight:500;padding:2px 8px;"
            "border-radius:10px;color:#f57c00;background:#fff3e0;"
        )
        self._preload_model()

    # ── UI 构建 ────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 0)
        root.setSpacing(0)

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

        root.addLayout(content_layout, 1)

    def _build_sidebar(self) -> QWidget:
        group = QGroupBox("会话列表")
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

        group.setFixedWidth(230)
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
            "点击右下角开始录音，开启语音转写"
        )
        layout.addWidget(self.chat_display, 1)

        return group

    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(8)

        self.back_btn = QPushButton("返回主界面")
        self.back_btn.setObjectName("backBtn")
        self.back_btn.clicked.connect(self._on_back)
        bar_layout.addWidget(self.back_btn)

        new_btn = QPushButton("新建")
        new_btn.setObjectName("newConvBtn")
        new_btn.clicked.connect(self._on_new_conversation)
        bar_layout.addWidget(new_btn)

        bar_layout.addSpacing(12)

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        bar_layout.addWidget(self.status_label)
        bar_layout.addStretch()

        return bar

    def _build_control_bar(self) -> QWidget:
        group = QGroupBox("控制区")
        group.setObjectName("controlGroup")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        top_widget = QWidget()
        top_widget.setObjectName("controlTopRow")
        top_row = QHBoxLayout(top_widget)
        top_row.setSpacing(12)

        mode_label = QLabel("录入模式:")
        mode_label.setObjectName("controlLabel")
        top_row.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("modeCombo")
        self.mode_combo.addItems(["普通录入", "实时录入"])
        self.mode_combo.setCurrentIndex(0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top_row.addWidget(self.mode_combo)

        self.noise_suppression_cb = QCheckBox("噪声抑制")
        self.noise_suppression_cb.setObjectName("noiseSuppressionCb")
        self.noise_suppression_cb.setToolTip(
            "启用噪声抑制，改善非真人发声（音箱、电子音等）录音效果"
        )
        self.noise_suppression_cb.setChecked(
            self._settings.get_bool("voice_to_text.noise_suppression", False)
        )
        top_row.addWidget(self.noise_suppression_cb)

        self.device_indicator = QLabel("● 设备就绪")
        self.device_indicator.setObjectName("deviceIndicator")
        top_row.addWidget(self.device_indicator)

        top_row.addStretch()

        self.recording_time_label = QLabel("00:00")
        self.recording_time_label.setObjectName("recordingTime")
        top_row.addWidget(self.recording_time_label)

        top_row.addStretch()

        self.record_btn = QPushButton("🎤 开始录音")
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.setMinimumSize(140, 44)
        self.record_btn.clicked.connect(self._on_record_toggled)
        top_row.addWidget(self.record_btn)

        layout.addWidget(top_widget)

        self.waveform = _WaveformWidget()
        self.waveform.setObjectName("waveformWidget")
        layout.addWidget(self.waveform)

        return group

    # ── 样式 ────────────────────────────────────────────────────────────────

    def _load_styles(self) -> None:
        style_path = Path(__file__).parent / "styles" / "voice_to_text.qss"
        if style_path.exists():
            self.setStyleSheet(style_path.read_text(encoding="utf-8"))

    def _update_record_btn_style(self, recording: bool) -> None:
        self.record_btn.setStyleSheet(
            _RECORD_STYLE_ACTIVE if recording else _RECORD_STYLE_IDLE
        )

    def _update_device_indicator_style(self, recording: bool) -> None:
        if recording:
            self.device_indicator.setText("● 录音中")
            color = "#d32f2f"
            bg = "#ffebee"
        else:
            self.device_indicator.setText("● 设备就绪")
            color = "#388e3c"
            bg = "#e8f5e9"
        self.device_indicator.setStyleSheet(
            f"font-size:11px;font-weight:500;padding:2px 8px;"
            f"border-radius:10px;color:{color};background:{bg};"
        )

    # ── 状态栏 ──────────────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

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
        self.chat_display.clear()
        self._recording_seconds = 0
        self.recording_time_label.setText("00:00")
        self._set_status("就绪")
        self._refresh_history()

    # ── 模式切换 ────────────────────────────────────────────────────────────

    def _on_mode_changed(self, index: int) -> None:
        self._mode = "normal" if index == 0 else "realtime"
        if self._recording:
            self._stop_recording()

    # ── 录音控制 ────────────────────────────────────────────────────────────

    def _on_record_toggled(self) -> None:
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        try:
            ns = self.noise_suppression_cb.isChecked()
            self._recorder = VoiceRecorder(
                sample_rate=16000, channels=1, settings=self._settings,
                noise_suppression=ns,
            )
            self._recorder.finished.connect(self._on_record_finished)
            self._recorder.error_occurred.connect(self._on_record_error)
            self._recorder.volume_changed.connect(self.waveform.update_volume)

            self._recording = True
            self._recording_seconds = 0
            self.recording_time_label.setText("00:00")
            self._recording_timer.start()

            self._update_record_btn_style(True)
            self._update_device_indicator_style(True)
            self.waveform.start()

            if self._mode == "realtime":
                self._realtime_timer.start()
                self.record_btn.setText("⏹ 停止实时录入")
                self._set_status("开始实时录入...")
            else:
                self.record_btn.setText("⏹ 停止录入")
                self._set_status("开始录音...")

            self._recorder.start()

        except Exception as exc:
            logger.error("启动录音失败: %s", exc)
            QMessageBox.warning(
                self, "录音失败", f"无法启动录音: {exc}"
            )
            self._recording = False
            self.record_btn.setText("🎤 开始录音")
            self._update_record_btn_style(False)
            self._update_device_indicator_style(False)
            self._recording_timer.stop()
            self.waveform.stop()

    def _stop_recording(self) -> None:
        self._recording = False
        self._realtime_timer.stop()
        self._recording_timer.stop()

        self._update_record_btn_style(False)
        self._update_device_indicator_style(False)
        self.waveform.stop()

        if self._recorder is not None:
            self._recorder.stop()
            ns = self.noise_suppression_cb.isChecked()
            if self._mode == "realtime":
                chunk_path = self._recorder.extract_chunk()
                if chunk_path:
                    self._transcribe_async(chunk_path, realtime=True)
                self.record_btn.setText("🎤 开始录音")
                self._set_status("实时录入已停止")
            else:
                self.record_btn.setText("🎤 开始录音")
                self._set_status("录音已停止，等待转写...")

    def _on_record_finished(self, wav_path: str) -> None:
        try:
            if self._mode == "normal":
                self.record_btn.setText("🎤 开始录音")
                self._update_record_btn_style(False)
                self._recording_timer.stop()
                self._update_device_indicator_style(False)
                self.waveform.stop()
                self._set_status("录音完成，正在转写...")
                self._transcribe_async(wav_path, realtime=False)
        except Exception:
            logger.error("on_record_finished 异常:\n%s", traceback.format_exc())

    def _on_record_error(self, msg: str) -> None:
        if "没有录制到音频数据" in msg and self._mode == "realtime":
            return
        try:
            self._recording = False
            self.record_btn.setText("🎤 开始录音")
            self._update_record_btn_style(False)
            self._update_device_indicator_style(False)
            self._realtime_timer.stop()
            self._recording_timer.stop()
            self.waveform.stop()
            QMessageBox.warning(self, "录音错误", msg)
        except Exception:
            logger.error("on_record_error 异常:\n%s", traceback.format_exc())

    def _cleanup_recorder(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()

    # ── 实时录入定时器 ──────────────────────────────────────────────────────

    def _on_realtime_chunk(self) -> None:
        if not self._recording or self._recorder is None:
            return
        chunk_path = self._recorder.extract_chunk()
        if chunk_path:
            self._transcribe_async(chunk_path, realtime=True)

    # ── 转写 (threading.Thread, 无 QThread) ────────────────────────────────

    def _transcribe_async(self, wav_path: str, realtime: bool) -> None:
        self._set_status("转写中...")
        bridge = _SignalBridge()
        ns = self.noise_suppression_cb.isChecked()

        def _on_done(text: str) -> None:
            try:
                self._on_transcribe_done(text, wav_path, realtime)
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
                    wav_path, noise_suppression=ns,
                )
                bridge.result.emit(result)
            except Exception as exc:
                bridge.error.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_transcribe_done(self, text: str, wav_path: str, realtime: bool) -> None:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception:
            pass

        if self._current_conv_id is None:
            mode = "realtime" if realtime else "normal"
            self._current_conv_id = self._store.create_conversation(text, mode=mode)
            self._refresh_history()
            conv = self._store.get_conversation(self._current_conv_id)
            msg_uuid = conv.messages[0].uuid if conv and conv.messages else ""
        else:
            msg = VoiceMessage(
                role="user",
                content=text,
                uuid=uuid.uuid4().hex[:16],
                mode="realtime" if realtime else "normal",
                timestamp=datetime.now().timestamp(),
            )
            self._store.append_message(self._current_conv_id, msg)
            msg_uuid = msg.uuid

        self._append_user(text, msg_uuid=msg_uuid)
        self._refresh_history()
        self._set_status("转写完成")

    def _on_transcribe_error(self, err: str, wav_path: str) -> None:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception:
            pass
        self._set_status(f"转写失败: {err}")

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
        meta = f"{time_str}  ·  {msg_count}条消息"
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
            self._set_status(f"已选中 {count} 个会话")
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
            exit_select_action = menu.addAction("退出多选模式")
            delete_selected_action = menu.addAction("删除选中的会话")
            select_all_action = menu.addAction("全选/取消全选")
            menu.addSeparator()
            delete_all_action = menu.addAction("全部删除")
        else:
            item = self.history_list.itemAt(pos)
            if item is None:
                multi_action = menu.addAction("多选")
                delete_all_action = menu.addAction("全部删除")
            else:
                conv_id = item.data(Qt.ItemDataRole.UserRole)
                multi_action = menu.addAction("多选")
                delete_action = menu.addAction("删除此会话")
                menu.addSeparator()
                delete_all_action = menu.addAction("全部删除")

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
                conv_id = item.data(Qt.ItemDataRole.UserRole)
                self._delete_conversation(conv_id)
            elif action == delete_all_action:
                self._delete_all_conversations()

    def _delete_conversation(self, conv_id: str) -> None:
        reply = QMessageBox.question(
            self,
            "删除会话",
            "确定要删除此会话吗？\n该操作会同时删除磁盘上的 JSON 文件，且无法恢复。",
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
                self._set_status("就绪")
            self._refresh_history()
            self._set_status("会话已删除")
        except Exception as exc:
            logger.error("删除会话失败 %s: %s", conv_id, exc)
            QMessageBox.warning(self, "删除失败", f"删除会话时出错: {exc}")

    def _toggle_multi_select(self, enable: bool) -> None:
        self._multi_select_mode = enable
        if enable:
            self.history_list.setSelectionMode(
                QListWidget.SelectionMode.MultiSelection
            )
            self._set_status("多选模式：点击列表项选中，右键菜单可批量删除")
        else:
            self.history_list.setSelectionMode(
                QListWidget.SelectionMode.SingleSelection
            )
            self.history_list.clearSelection()
            self._set_status("就绪")

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
            QMessageBox.information(self, "提示", "请先选择要删除的会话")
            return
        count = len(selected_items)
        reply = QMessageBox.question(
            self,
            "批量删除",
            f"确定要删除选中的 {count} 个会话吗？\n该操作不可恢复。",
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
                logger.error("删除会话失败 %s: %s", conv_id, exc)
        self._refresh_history()
        self._set_status(f"已删除 {deleted} 个会话")

    def _delete_all_conversations(self) -> None:
        count = self.history_list.count()
        if count == 0:
            QMessageBox.information(self, "提示", "当前没有会话")
            return
        reply = QMessageBox.question(
            self,
            "全部删除",
            f"确定要删除全部 {count} 个会话吗？\n该操作不可恢复。",
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
                logger.error("删除会话失败 %s: %s", conv["id"], exc)
        self._current_conv_id = None
        self.chat_display.clear()
        self._toggle_multi_select(False)
        self._refresh_history()
        self._set_status(f"已删除 {deleted} 个会话")

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
            save_action = menu.addAction("保存")
            cancel_action = menu.addAction("取消编辑")
            action = menu.exec(self.chat_display.mapToGlobal(pos))
            if action == save_action:
                self._save_edited_message()
            elif action == cancel_action:
                self._cancel_edit()
            return

        copy_action = menu.addAction("复制")
        edit_action = menu.addAction("编辑")
        delete_action = menu.addAction("删除")

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
        grammar_action = menu.addAction("纠正")
        summary_action = menu.addAction("总结")
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
        self._set_status("编辑模式：修改内容后右键选择[保存]完成")

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
        self._set_status("消息已保存")

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
        self._set_status("已取消编辑")

    def _copy_message_text(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self._set_status("已复制到剪贴板")

    def _run_grammar_correction(self, text: str, original_uuid: str) -> None:
        self._set_status("正在纠正语法...")
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
                        mode="normal",
                        corrected=True,
                        parent_uuid=original_uuid,
                        timestamp=datetime.now().timestamp(),
                    )
                    conv.messages.append(new_msg)
                conv.updated_at = datetime.now().timestamp()
                self._store._save(conv)
                modified_conv = conv

        self._reload_conversation_display(conv=modified_conv)
        self._set_status("语法纠正完成")

    def _delete_message(self, msg_uuid: str) -> None:
        if not self._current_conv_id or not msg_uuid:
            return
        reply = QMessageBox.question(
            self,
            "删除消息",
            "确定要删除此消息吗？",
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
        self._set_status("消息已删除")

    def _run_summarization(self) -> None:
        if self._current_conv_id is None:
            QMessageBox.warning(self, "提示", "请先选择一个对话会话")
            return
        conv = self._store.get_conversation(self._current_conv_id)
        if conv is None or not conv.messages:
            return

        self._set_status("正在总结...")
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
            f"总结完成，已保存至: {md_path}", label=_SUMMARY_PREFIX
        )
        if self._current_conv_id:
            self._store.update_summary_path(self._current_conv_id, str(md_path))
        self._set_status("总结完成")

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
            lambda err: self._set_status(f"操作失败: {err}")
        )
        bridge.error.connect(bridge.deleteLater)

        def _worker():
            try:
                result = func()
                bridge.result.emit(result)
            except Exception as exc:
                bridge.error.emit(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _preload_model(self) -> None:
        bridge = _SignalBridge()

        def _on_loaded(_text: str) -> None:
            self._update_device_indicator_style(False)
            bridge.deleteLater()

        def _on_error(err: str) -> None:
            self.device_indicator.setText("● 模型加载失败")
            self.device_indicator.setStyleSheet(
                "font-size:11px;font-weight:500;padding:2px 8px;"
                "border-radius:10px;color:#d32f2f;background:#ffebee;"
            )
            self._set_status(f"模型加载失败: {err}")
            bridge.deleteLater()

        bridge.result.connect(_on_loaded)
        bridge.error.connect(_on_error)

        def _worker():
            import struct
            import wave
            try:
                self._transcription._get_transcriber()
                sample_rate = 16000
                duration = 1
                silence = b'\x00\x00' * (sample_rate * duration)
                tmp = Path.cwd() / "voice" / "_warmup.wav"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(tmp), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(silence)
                self._transcription.transcribe_file(str(tmp))
                tmp.unlink(missing_ok=True)
                bridge.result.emit("")
            except Exception as exc:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
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

    def cleanup(self) -> None:
        if self._recording:
            self._stop_recording()
        self._cleanup_recorder()
        self._realtime_timer.stop()
