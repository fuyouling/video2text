"""启动依赖确认对话框：模型下载 + DLL 依赖下载的分组选择。"""

from PySide6.QtWidgets import (
    QButtonGroup,
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

    仅对实际缺失的依赖开放选择；已完整的项以只读状态展示「已就绪」，
    避免用户误操作或被冗余选项干扰。

    注意：模型组、DLL 下载组、保留/删除压缩包组分别使用独立的 QButtonGroup，
    否则同属一个隐式按钮组的单选按钮会互相排斥，导致选中的选项莫名消失。
    """

    def __init__(self, model_missing: bool, dll_missing: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("dialogs.dep.title"))
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(t("dialogs.dep.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # 模型分组
        model_group = QGroupBox(t("dialogs.dep.model_group"))
        model_layout = QVBoxLayout(model_group)
        if model_missing:
            model_btn_group = QButtonGroup(self)
            self.model_yes = QRadioButton(t("dialogs.dep.model_yes"))
            self.model_no = QRadioButton(t("dialogs.dep.model_no"))
            model_btn_group.addButton(self.model_yes)
            model_btn_group.addButton(self.model_no)
            self.model_yes.setChecked(True)
            model_layout.addWidget(self.model_yes)
            model_layout.addWidget(self.model_no)
        else:
            ready = QLabel(t("dialogs.dep.model_ready"))
            ready.setEnabled(False)
            model_layout.addWidget(ready)
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
        download_dll = (
            getattr(self, "dll_yes", None) is not None and self.dll_yes.isChecked()
        )
        return {
            "download_model": getattr(self, "model_yes", None) is not None
            and self.model_yes.isChecked(),
            "download_dll": download_dll,
            "keep_archive": download_dll
            and getattr(self, "keep_archive_yes", None) is not None
            and self.keep_archive_yes.isChecked(),
        }
