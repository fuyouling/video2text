"""背景图片透明度调整对话框，支持滑块拖动实时预览与取消还原"""

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.i18n import t


class BackgroundOpacityDialog(QDialog):
    """背景透明度实时调节对话框

    支持：
    - QSlider 与 QSpinBox 双向联动
    - 拖动滑块时通过 on_preview 回调实时更新宿主窗口的背景透明度
    - 点击取消或关闭窗口时，自动恢复至打开前的初始透明度
    - 快捷预设按钮（20%, 40%, 60%, 80%, 100%）
    - 恢复默认按钮（100 / 255）
    - 未设置背景图片时的友好提示与一键选择入口
    """

    DEFAULT_OPACITY_VALUE: int = 100  # 约 40% (100 / 255)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        current_value: int = 100,
        has_bg_image: bool = True,
        on_preview: Optional[Callable[[int], None]] = None,
        on_choose_image: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)

        self._initial_value: int = max(0, min(255, int(current_value)))
        self._current_value: int = self._initial_value
        self._has_bg_image: bool = has_bg_image
        self._on_preview: Optional[Callable[[int], None]] = on_preview
        self._on_choose_image: Optional[Callable[[], None]] = on_choose_image

        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle(t("dialogs.bg_opacity.title"))
        self.setMinimumWidth(380)
        # 去除问号按钮
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # 1. 顶部提示文本
        hint_label = QLabel(t("dialogs.bg_opacity.hint"))
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: palette(window-text); opacity: 0.85;")
        layout.addWidget(hint_label)

        # 2. 若未设置背景图片，展示友好提示条与选择按钮
        self._no_bg_widget = QFrame()
        self._no_bg_widget.setFrameShape(QFrame.Shape.StyledPanel)
        self._no_bg_widget.setStyleSheet(
            "QFrame { background-color: rgba(255, 165, 0, 0.12); "
            "border: 1px solid rgba(255, 165, 0, 0.4); "
            "border-radius: 4px; padding: 6px; }"
        )
        no_bg_layout = QHBoxLayout(self._no_bg_widget)
        no_bg_layout.setContentsMargins(6, 4, 6, 4)
        no_bg_layout.setSpacing(8)

        warn_icon_label = QLabel("💡")
        no_bg_text = QLabel(t("dialogs.bg_opacity.no_bg_hint"))
        no_bg_text.setWordWrap(True)
        no_bg_layout.addWidget(warn_icon_label)
        no_bg_layout.addWidget(no_bg_text, 1)

        if self._on_choose_image:
            choose_btn = QPushButton(t("dialogs.bg_opacity.choose_image"))
            choose_btn.clicked.connect(self._on_choose_image_clicked)
            no_bg_layout.addWidget(choose_btn)

        layout.addWidget(self._no_bg_widget)
        self._no_bg_widget.setVisible(not self._has_bg_image)

        # 3. 滑块与微调输入框行
        slider_layout = QHBoxLayout()
        slider_layout.setSpacing(8)

        lbl_opacity = QLabel(t("dialogs.bg_opacity.opacity_label"))
        slider_layout.addWidget(lbl_opacity)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 255)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(15)
        self.slider.setValue(self._initial_value)
        self.slider.setTracking(True)
        self.slider.valueChanged.connect(self._on_slider_changed)
        slider_layout.addWidget(self.slider, 1)

        self.spin = QSpinBox()
        self.spin.setRange(0, 255)
        self.spin.setValue(self._initial_value)
        self.spin.setFixedWidth(55)
        self.spin.valueChanged.connect(self._on_spin_changed)
        slider_layout.addWidget(self.spin)

        self.percent_label = QLabel()
        self.percent_label.setFixedWidth(45)
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider_layout.addWidget(self.percent_label)

        layout.addLayout(slider_layout)

        # 4. 快捷预设按钮行
        preset_layout = QHBoxLayout()
        preset_layout.setSpacing(6)
        preset_title = QLabel(t("dialogs.bg_opacity.preset_label"))
        preset_title.setStyleSheet("color: palette(window-text); opacity: 0.8;")
        preset_layout.addWidget(preset_title)

        # 预设档位: 20%, 40%(默认), 60%, 80%, 100%
        presets = [
            ("20%", 51),
            (f"40% ({t('dialogs.bg_opacity.preset_default')})", 102),
            ("60%", 153),
            ("80%", 204),
            ("100%", 255),
        ]
        for label_text, val in presets:
            btn = QPushButton(label_text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("padding: 2px 6px; font-size: 11px;")
            btn.clicked.connect(lambda _, v=val: self.set_value(v))
            preset_layout.addWidget(btn)

        preset_layout.addStretch(1)
        layout.addLayout(preset_layout)

        # 5. 底部按钮行（重置默认 + 确定 / 取消）
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(8)

        reset_btn = QPushButton(t("dialogs.bg_opacity.reset"))
        reset_btn.setToolTip("恢复为默认透明度 (100 / ~40%)")
        reset_btn.clicked.connect(lambda: self.set_value(self.DEFAULT_OPACITY_VALUE))
        bottom_layout.addWidget(reset_btn)

        bottom_layout.addStretch(1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText(t("common.ok"))
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText(t("common.cancel"))

        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        bottom_layout.addWidget(self.button_box)

        layout.addLayout(bottom_layout)

        # 初始化百分比显示
        self._update_percent_text(self._initial_value)

    def _on_choose_image_clicked(self) -> None:
        """点击选择图片"""
        if self._on_choose_image:
            self._on_choose_image()
            parent = self.parent()
            has_bg = False
            if parent and hasattr(parent, "_bg_pixmap"):
                has_bg = bool(parent._bg_pixmap and not parent._bg_pixmap.isNull())
            if has_bg:
                self._has_bg_image = True
                self._no_bg_widget.setVisible(False)
            if self._on_preview:
                self._on_preview(self._current_value)

    def _on_slider_changed(self, val: int) -> None:
        if self.spin.value() != val:
            self.spin.blockSignals(True)
            self.spin.setValue(val)
            self.spin.blockSignals(False)
        self._apply_val(val)

    def _on_spin_changed(self, val: int) -> None:
        if self.slider.value() != val:
            self.slider.blockSignals(True)
            self.slider.setValue(val)
            self.slider.blockSignals(False)
        self._apply_val(val)

    def _update_percent_text(self, val: int) -> None:
        pct = round(val / 255.0 * 100)
        self.percent_label.setText(f"{pct}%")

    def _apply_val(self, val: int) -> None:
        self._current_value = val
        self._update_percent_text(val)
        if self._on_preview:
            self._on_preview(val)

    def set_value(self, val: int) -> None:
        """设置当前值并触发预览更新"""
        clamped = max(0, min(255, int(val)))
        if self.slider.value() == clamped:
            self._apply_val(clamped)
        else:
            self.slider.setValue(clamped)

    def get_value(self) -> int:
        """获取当前确认的透明度整数 (0~255)"""
        return self._current_value

    def get_opacity(self) -> float:
        """获取当前确认的透明度浮点数 (0.0~1.0)"""
        return max(0.0, min(1.0, self._current_value / 255.0))

    def accept(self) -> None:
        """确认保存"""
        if self._on_preview:
            self._on_preview(self._current_value)
        super().accept()

    def reject(self) -> None:
        """取消：恢复至打开前的初始值"""
        if self._on_preview:
            self._on_preview(self._initial_value)
        super().reject()
