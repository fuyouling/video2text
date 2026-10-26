"""启动依赖确认对话框：模型下载 + DLL 依赖下载的分组选择。"""

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from src.i18n import t


class StartupConfirmDialog(QDialog):
    """启动依赖确认对话框：缺失项分组呈现，并提供下载/跳过选择。

    支持对 Faster-Whisper、FunASR Paraformer 以及 CUDA DLL 依赖进行独立勾选与检测。
    已完整的项以只读状态展示「已就绪」，避免用户误操作。
    """

    def __init__(
        self,
        model_missing: bool = False,
        dll_missing: bool = False,
        parent=None,
        *,
        whisper_missing: bool = False,
        funasr_missing: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(t("dialogs.dep.title"))
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(t("dialogs.dep.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # 兼容旧接口：若调用方只传了 model_missing 且未指定具体模型状态，将两个都视作可能缺失
        if model_missing and not whisper_missing and not funasr_missing:
            whisper_missing = True

        self._whisper_missing = whisper_missing
        self._funasr_missing = funasr_missing

        # 模型分组
        model_group = QGroupBox(t("dialogs.dep.model_group"))
        model_layout = QVBoxLayout(model_group)

        # Faster-Whisper
        if whisper_missing:
            self.whisper_cb = QCheckBox(t("dialogs.dep.whisper_opt"))
            self.whisper_cb.setChecked(True)
            model_layout.addWidget(self.whisper_cb)
        else:
            ready_whisper = QLabel(t("dialogs.dep.whisper_ready"))
            ready_whisper.setEnabled(False)
            model_layout.addWidget(ready_whisper)

        # FunASR Paraformer
        if funasr_missing:
            self.funasr_cb = QCheckBox(t("dialogs.dep.funasr_opt"))
            self.funasr_cb.setChecked(True)
            model_layout.addWidget(self.funasr_cb)
        else:
            ready_funasr = QLabel(t("dialogs.dep.funasr_ready"))
            ready_funasr.setEnabled(False)
            model_layout.addWidget(ready_funasr)

        layout.addWidget(model_group)

        # DLL 分组
        dll_group = QGroupBox(t("dialogs.dep.dll_group"))
        dll_layout = QVBoxLayout(dll_group)
        if dll_missing:
            dll_btn_group = QButtonGroup(self)
            self.dll_yes = QRadioButton(t("dialogs.dep.dll_yes"))
            self.dll_no = QRadioButton(t("dialogs.dep.dll_no"))
            dll_btn_group.addButton(self.dll_yes)
            dll_btn_group.addButton(self.dll_no)
            self.dll_yes.setChecked(True)
            dll_layout.addWidget(self.dll_yes)
            dll_layout.addWidget(self.dll_no)

            keep_layout = QHBoxLayout()
            keep_label = QLabel(t("dialogs.dep.keep_label"))
            self.keep_archive_yes = QRadioButton(t("dialogs.dep.keep_yes"))
            self.keep_archive_no = QRadioButton(t("dialogs.dep.keep_no"))
            keep_btn_group = QButtonGroup(self)
            keep_btn_group.addButton(self.keep_archive_yes)
            keep_btn_group.addButton(self.keep_archive_no)
            self.keep_archive_no.setChecked(True)
            keep_layout.addWidget(keep_label)
            keep_layout.addWidget(self.keep_archive_yes)
            keep_layout.addWidget(self.keep_archive_no)
            keep_layout.addStretch(1)
            dll_layout.addLayout(keep_layout)

            # 仅当选「下载 DLL」时，保留/删除压缩包才有意义
            self.dll_no.toggled.connect(
                lambda checked: (
                    self.keep_archive_yes.setDisabled(checked),
                    self.keep_archive_no.setDisabled(checked),
                )
            )
        else:
            ready = QLabel(t("dialogs.dep.dll_ready"))
            ready.setEnabled(False)
            dll_layout.addWidget(ready)
        layout.addWidget(dll_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(t("common.ok"))
        buttons.button(QDialogButtonBox.Cancel).setText(t("common.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_result(self) -> dict:
        download_whisper = (
            getattr(self, "whisper_cb", None) is not None
            and self.whisper_cb.isChecked()
        )
        download_funasr = (
            getattr(self, "funasr_cb", None) is not None
            and self.funasr_cb.isChecked()
        )
        download_dll = (
            getattr(self, "dll_yes", None) is not None and self.dll_yes.isChecked()
        )

        target_models = []
        if download_whisper:
            target_models.append("faster-whisper-large-v3-turbo-ct2")
        if download_funasr:
            target_models.append("paraformer-large-vad-punc")

        return {
            "download_model": bool(target_models),
            "target_models": target_models,
            "download_whisper": download_whisper,
            "download_funasr": download_funasr,
            "download_dll": download_dll,
            "keep_archive": download_dll
            and getattr(self, "keep_archive_yes", None) is not None
            and self.keep_archive_yes.isChecked(),
        }
