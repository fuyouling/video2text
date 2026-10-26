"""转写配置 Tab —— Faster-Whisper / FunASR 独立单选切换与参数配置表单"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings
from src.i18n import t


class TranscriptionTab(QWidget):
    """转写配置 Tab —— 参照 SummarizationTab 架构设计。

    顶部单选切换 Faster-Whisper 与 FunASR 模型体系；
    下方独立呈现各自专属的参数面板，支持设备精度联动与自定义模型路径浏览。
    """

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._section_edits: dict[str, QWidget] = {}
        self._init_ui()

    def get_section_edits(self) -> dict[str, QWidget]:
        """返回 transcription section 的所有编辑控件，供 ConfigEditorDialog 统一保存"""
        return self._section_edits

    def get_engine(self) -> str:
        """获取当前选中的转写引擎 ('faster-whisper' / 'funasr')"""
        if self._radio_funasr.isChecked():
            return "funasr"
        return "faster-whisper"

    def set_engine(self, engine: str) -> None:
        """设置当前选中的转写引擎 (供重置使用)"""
        is_funasr = engine in ("funasr", "paraformer")
        self._radio_funasr.setChecked(is_funasr)
        self._radio_whisper.setChecked(not is_funasr)

    @staticmethod
    def _set_widget_text(widget: QWidget, value: str) -> None:
        if isinstance(widget, QComboBox):
            val_str = str(value).strip()
            for i in range(widget.count()):
                data = widget.itemData(i)
                if data is not None and str(data).lower() == val_str.lower():
                    widget.setCurrentIndex(i)
                    return
            idx = widget.findText(val_str, Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                widget.setCurrentIndex(idx)
            else:
                widget.setCurrentText(val_str)
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value))

    def _create_combo(self, options: list[str], default_val: str, tooltip: str = "") -> QComboBox:
        combo = QComboBox()
        for opt in options:
            combo.addItem(opt)
        self._set_widget_text(combo, default_val)
        if tooltip:
            combo.setToolTip(tooltip)
        return combo

    def _create_bool_combo(self, default_val: str, tooltip: str = "") -> QComboBox:
        combo = QComboBox()
        combo.addItem(t("common.yes"), "True")
        combo.addItem(t("common.no"), "False")
        # 根据 default_val 匹配
        val_bool = str(default_val).strip().lower() in ("true", "1", "yes")
        combo.setCurrentIndex(0 if val_bool else 1)
        if tooltip:
            combo.setToolTip(tooltip)
        return combo

    def _create_path_widget(
        self,
        combo_options: list[str],
        default_val: str,
        tooltip: str = "",
    ) -> tuple[QWidget, QComboBox]:
        """创建带文件夹浏览按钮的模型选择组合控件"""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        combo = QComboBox()
        combo.setEditable(True)
        for opt in combo_options:
            combo.addItem(opt)
        combo.setEditText(default_val)
        if tooltip:
            combo.setToolTip(tooltip)

        browse_btn = QPushButton("📁")
        browse_btn.setObjectName("_browse_btn")
        browse_btn.setToolTip(t("dialogs.config_editor.browse_tooltip_dir"))

        def _on_browse():
            current = combo.currentText().strip()
            folder = QFileDialog.getExistingDirectory(self, t("dialogs.config_editor.browse_dir"), current)
            if folder:
                from src.utils.paths import get_base_dir

                try:
                    rel = Path(folder).relative_to(get_base_dir() / "models")
                    combo.setEditText(str(rel))
                except ValueError:
                    combo.setEditText(folder)

        browse_btn.clicked.connect(_on_browse)

        layout.addWidget(combo, 1)
        layout.addWidget(browse_btn)
        return container, combo

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(12)

        # ---- 1. 转写引擎 / 模型选择 ----
        engine_group = QGroupBox(t("transcription_tab.engine_group"))
        engine_layout = QHBoxLayout(engine_group)
        self._radio_whisper = QRadioButton(t("transcription_tab.radio_whisper"))
        self._radio_funasr = QRadioButton(t("transcription_tab.radio_funasr"))

        current_engine = self._settings.get("transcription.engine", "faster-whisper").lower().strip()
        if current_engine in ("funasr", "paraformer"):
            self._radio_funasr.setChecked(True)
        else:
            self._radio_whisper.setChecked(True)

        engine_layout.addWidget(self._radio_whisper)
        engine_layout.addWidget(self._radio_funasr)
        main_layout.addWidget(engine_group)

        # ---- 2. Faster-Whisper 专属参数区域 ----
        self._whisper_group = QGroupBox(t("transcription_tab.whisper_group"))
        whisper_form = QFormLayout(self._whisper_group)
        whisper_form.setContentsMargins(10, 14, 10, 12)
        whisper_form.setSpacing(10)
        whisper_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        whisper_form.setHorizontalSpacing(14)

        # Whisper 模型
        whisper_model_val = (
            self._settings.get("transcription.whisper_model")
            or self._settings.get("transcription.model_path", "faster-whisper-large-v3-turbo-ct2")
        )
        whisper_model_box, self._whisper_model_combo = self._create_path_widget(
            ["faster-whisper-large-v3-turbo-ct2", "large-v3"],
            whisper_model_val,
            t("tx_tooltips.whisper_model"),
        )
        whisper_form.addRow(f"{t('tx_labels.whisper_model')}:", whisper_model_box)
        self._section_edits["whisper_model"] = self._whisper_model_combo

        # Whisper 设备
        whisper_device_val = (
            self._settings.get("transcription.whisper_device")
            or self._settings.get("transcription.device", "cuda")
        )
        self._whisper_device_combo = self._create_combo(
            ["cuda", "cpu", "auto"],
            whisper_device_val,
            t("tx_tooltips.whisper_device"),
        )
        whisper_form.addRow(f"{t('tx_labels.whisper_device')}:", self._whisper_device_combo)
        self._section_edits["whisper_device"] = self._whisper_device_combo

        # Whisper 计算精度
        whisper_compute_val = (
            self._settings.get("transcription.whisper_compute_type")
            or self._settings.get("transcription.compute_type", "float16")
        )
        self._whisper_compute_combo = self._create_combo(
            ["float16", "int8", "float32"],
            whisper_compute_val,
            t("tx_tooltips.whisper_compute_type"),
        )
        whisper_form.addRow(f"{t('tx_labels.whisper_compute_type')}:", self._whisper_compute_combo)
        self._section_edits["whisper_compute_type"] = self._whisper_compute_combo

        # Whisper 语言
        whisper_lang_val = (
            self._settings.get("transcription.whisper_language")
            or self._settings.get("transcription.language", "auto")
        )
        whisper_lang_edit = QLineEdit(whisper_lang_val)
        whisper_lang_edit.setToolTip(t("tx_tooltips.whisper_language"))
        whisper_form.addRow(f"{t('tx_labels.whisper_language')}:", whisper_lang_edit)
        self._section_edits["whisper_language"] = whisper_lang_edit

        # Whisper 线程数
        whisper_workers_val = (
            self._settings.get("transcription.whisper_num_workers")
            or self._settings.get("transcription.num_workers", "1")
        )
        whisper_workers_edit = QLineEdit(str(whisper_workers_val))
        whisper_workers_edit.setToolTip(t("tx_tooltips.whisper_num_workers"))
        whisper_form.addRow(f"{t('tx_labels.whisper_num_workers')}:", whisper_workers_edit)
        self._section_edits["whisper_num_workers"] = whisper_workers_edit

        # 解码参数
        whisper_items = [
            ("whisper_beam_size", "beam_size", "5"),
            ("whisper_best_of", "best_of", "5"),
            ("whisper_temperature", "temperature", "0.0,0.2,0.4"),
            ("whisper_vad_threshold", "vad_threshold", "0.5"),
            ("whisper_vad_min_silence_ms", "vad_min_silence_ms", "2000"),
            ("whisper_vad_speech_pad_ms", "vad_speech_pad_ms", "400"),
            ("whisper_vad_max_speech_s", "vad_max_speech_s", "0"),
            ("whisper_compression_ratio_threshold", "compression_ratio_threshold", "2.4"),
            ("whisper_log_prob_threshold", "log_prob_threshold", "-1.0"),
            ("whisper_no_speech_threshold", "no_speech_threshold", "0.6"),
            ("whisper_repetition_penalty", "repetition_penalty", "1.0"),
            ("whisper_no_repeat_ngram_size", "no_repeat_ngram_size", "3"),
        ]

        # VAD 开关
        whisper_vad_val = (
            self._settings.get("transcription.whisper_vad_filter")
            or self._settings.get("transcription.vad_filter", "True")
        )
        whisper_vad_combo = self._create_bool_combo(whisper_vad_val, t("tx_tooltips.whisper_vad_filter"))
        whisper_form.addRow(f"{t('tx_labels.whisper_vad_filter')}:", whisper_vad_combo)
        self._section_edits["whisper_vad_filter"] = whisper_vad_combo

        # 前文条件开关
        whisper_cond_val = (
            self._settings.get("transcription.whisper_condition_on_previous_text")
            or self._settings.get("transcription.condition_on_previous_text", "False")
        )
        whisper_cond_combo = self._create_bool_combo(whisper_cond_val, t("tx_tooltips.whisper_condition_on_previous_text"))
        whisper_form.addRow(f"{t('tx_labels.whisper_condition_on_previous_text')}:", whisper_cond_combo)
        self._section_edits["whisper_condition_on_previous_text"] = whisper_cond_combo

        # 词级时间戳开关
        whisper_word_val = (
            self._settings.get("transcription.whisper_word_timestamps")
            or self._settings.get("transcription.word_timestamps", "False")
        )
        whisper_word_combo = self._create_bool_combo(whisper_word_val, t("tx_tooltips.whisper_word_timestamps"))
        whisper_form.addRow(f"{t('tx_labels.whisper_word_timestamps')}:", whisper_word_combo)
        self._section_edits["whisper_word_timestamps"] = whisper_word_combo

        for full_key_name, fallback_key, default_str in whisper_items:
            val = self._settings.get(f"transcription.{full_key_name}")
            if val is None or val == "":
                val = self._settings.get(f"transcription.{fallback_key}", default_str)
            edit = QLineEdit(str(val))
            label_text = t(f"tx_labels.{full_key_name}")
            edit.setToolTip(t(f"tx_tooltips.{full_key_name}"))
            whisper_form.addRow(f"{label_text}:", edit)
            self._section_edits[full_key_name] = edit

        main_layout.addWidget(self._whisper_group)

        # ---- 3. FunASR Paraformer 专属参数区域 ----
        self._funasr_group = QGroupBox(t("transcription_tab.funasr_group"))
        funasr_form = QFormLayout(self._funasr_group)
        funasr_form.setContentsMargins(10, 14, 10, 12)
        funasr_form.setSpacing(10)
        funasr_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        funasr_form.setHorizontalSpacing(14)

        # FunASR 模型
        funasr_model_val = (
            self._settings.get("transcription.funasr_model")
            or self._settings.get("transcription.model_path", "paraformer-large-vad-punc")
        )
        funasr_model_box, self._funasr_model_combo = self._create_path_widget(
            ["paraformer-large-vad-punc"],
            funasr_model_val,
            t("tx_tooltips.funasr_model"),
        )
        funasr_form.addRow(f"{t('tx_labels.funasr_model')}:", funasr_model_box)
        self._section_edits["funasr_model"] = self._funasr_model_combo

        # FunASR 设备
        funasr_device_val = (
            self._settings.get("transcription.funasr_device")
            or self._settings.get("transcription.device", "auto")
        )
        self._funasr_device_combo = self._create_combo(
            ["auto", "cuda", "cpu"],
            funasr_device_val,
            t("tx_tooltips.funasr_device"),
        )
        funasr_form.addRow(f"{t('tx_labels.funasr_device')}:", self._funasr_device_combo)
        self._section_edits["funasr_device"] = self._funasr_device_combo

        # FunASR 语言
        funasr_lang_val = (
            self._settings.get("transcription.funasr_language")
            or self._settings.get("transcription.language", "zh")
        )
        funasr_lang_edit = QLineEdit(funasr_lang_val)
        funasr_lang_edit.setToolTip(t("tx_tooltips.funasr_language"))
        funasr_form.addRow(f"{t('tx_labels.funasr_language')}:", funasr_lang_edit)
        self._section_edits["funasr_language"] = funasr_lang_edit

        # FunASR 推理线程数
        funasr_workers_val = (
            self._settings.get("transcription.funasr_num_workers")
            or self._settings.get("transcription.num_workers", "4")
        )
        funasr_workers_edit = QLineEdit(str(funasr_workers_val))
        funasr_workers_edit.setToolTip(t("tx_tooltips.funasr_num_workers"))
        funasr_form.addRow(f"{t('tx_labels.funasr_num_workers')}:", funasr_workers_edit)
        self._section_edits["funasr_num_workers"] = funasr_workers_edit

        # FunASR 计算精度
        funasr_compute_val = (
            self._settings.get("transcription.funasr_compute_type")
            or self._settings.get("transcription.funasr_quantize")
            or "int8"
        )
        if str(funasr_compute_val).lower() in ("true", "1", "yes"):
            funasr_compute_val = "int8"
        elif str(funasr_compute_val).lower() in ("false", "0", "no"):
            funasr_compute_val = "float32"

        self._funasr_compute_combo = self._create_combo(
            ["float16", "int8", "float32"],
            funasr_compute_val,
            t("tx_tooltips.funasr_compute_type"),
        )
        funasr_form.addRow(f"{t('tx_labels.funasr_compute_type')}:", self._funasr_compute_combo)
        self._section_edits["funasr_compute_type"] = self._funasr_compute_combo

        # FunASR 标点恢复开关
        funasr_punc_val = (
            self._settings.get("transcription.funasr_punc_filter")
            or "True"
        )
        funasr_punc_combo = self._create_bool_combo(funasr_punc_val, t("tx_tooltips.funasr_punc_filter"))
        funasr_form.addRow(f"{t('tx_labels.funasr_punc_filter')}:", funasr_punc_combo)
        self._section_edits["funasr_punc_filter"] = funasr_punc_combo

        # FunASR VAD 开关
        funasr_vad_val = (
            self._settings.get("transcription.funasr_vad_filter")
            or self._settings.get("transcription.vad_filter", "True")
        )
        funasr_vad_combo = self._create_bool_combo(funasr_vad_val, t("tx_tooltips.funasr_vad_filter"))
        funasr_form.addRow(f"{t('tx_labels.funasr_vad_filter')}:", funasr_vad_combo)
        self._section_edits["funasr_vad_filter"] = funasr_vad_combo

        # FunASR VAD 最大静音时长 (断句阈值)
        funasr_vad_sil_val = (
            self._settings.get("transcription.funasr_vad_max_end_sil")
            or "800"
        )
        funasr_vad_sil_edit = QLineEdit(str(funasr_vad_sil_val))
        funasr_vad_sil_edit.setToolTip(t("tx_tooltips.funasr_vad_max_end_sil"))
        funasr_form.addRow(f"{t('tx_labels.funasr_vad_max_end_sil')}:", funasr_vad_sil_edit)
        self._section_edits["funasr_vad_max_end_sil"] = funasr_vad_sil_edit

        main_layout.addWidget(self._funasr_group)
        main_layout.addStretch()

        # ---- 4. 信号连接 ----
        self._radio_whisper.toggled.connect(self._on_engine_changed)
        self._radio_funasr.toggled.connect(self._on_engine_changed)
        self._whisper_device_combo.currentTextChanged.connect(self._update_compute_options)
        self._funasr_device_combo.currentTextChanged.connect(self._update_funasr_compute_options)

        self._on_engine_changed()
        self._update_compute_options()
        self._update_funasr_compute_options()

    def _on_engine_changed(self) -> None:
        """切换 Faster-Whisper 与 FunASR 专属参数面板的显示/隐藏"""
        is_whisper = self._radio_whisper.isChecked()
        self._whisper_group.setVisible(is_whisper)
        self._funasr_group.setVisible(not is_whisper)

    def _update_compute_options(self, _text: str = "") -> None:
        """Whisper 选择 cpu 时禁止 float16"""
        dev = self._whisper_device_combo.currentText().strip().lower()
        comp_widget = self._whisper_compute_combo
        current_comp = comp_widget.currentText().strip().lower()

        comp_widget.blockSignals(True)
        comp_widget.clear()
        if dev == "cpu":
            comp_widget.addItems(["int8", "float32"])
            if current_comp in ("int8", "float32"):
                comp_widget.setCurrentText(current_comp)
            else:
                comp_widget.setCurrentText("int8")
        else:
            comp_widget.addItems(["float16", "int8", "float32"])
            if current_comp in ("float16", "int8", "float32"):
                comp_widget.setCurrentText(current_comp)
            else:
                comp_widget.setCurrentText("float16")
        comp_widget.blockSignals(False)

    def _update_funasr_compute_options(self, _text: str = "") -> None:
        """FunASR 选择 cpu 时禁止 float16"""
        dev = self._funasr_device_combo.currentText().strip().lower()
        comp_widget = self._funasr_compute_combo
        current_comp = comp_widget.currentText().strip().lower()

        comp_widget.blockSignals(True)
        comp_widget.clear()
        if dev == "cpu":
            comp_widget.addItems(["int8", "float32"])
            if current_comp in ("int8", "float32"):
                comp_widget.setCurrentText(current_comp)
            else:
                comp_widget.setCurrentText("int8")
        else:
            comp_widget.addItems(["float16", "int8", "float32"])
            if current_comp in ("float16", "int8", "float32"):
                comp_widget.setCurrentText(current_comp)
            else:
                comp_widget.setCurrentText("int8")
        comp_widget.blockSignals(False)
