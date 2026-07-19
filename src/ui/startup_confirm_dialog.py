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


class StartupConfirmDialog(QDialog):
    """启动依赖确认对话框：缺失项分组呈现，并提供下载/跳过选择。

    仅对实际缺失的依赖开放选择；已完整的项以只读状态展示「已就绪」，
    避免用户误操作或被冗余选项干扰。

    注意：模型组、DLL 下载组、保留/删除压缩包组分别使用独立的 QButtonGroup，
    否则同属一个隐式按钮组的单选按钮会互相排斥，导致选中的选项莫名消失。
    """

    def __init__(self, model_missing: bool, dll_missing: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("依赖下载确认")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "检测到以下运行依赖不完整，转写功能可能无法正常工作。\n"
            "请选择需要下载的项目（也可点击「取消」跳过，之后在「设置」中手动处理）："
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # 模型分组
        model_group = QGroupBox("语音识别模型")
        model_layout = QVBoxLayout(model_group)
        if model_missing:
            model_btn_group = QButtonGroup(self)
            self.model_yes = QRadioButton("是，下载模型（首次使用必需）")
            self.model_no = QRadioButton("否，跳过（转写将无法运行）")
            model_btn_group.addButton(self.model_yes)
            model_btn_group.addButton(self.model_no)
            self.model_yes.setChecked(True)
            model_layout.addWidget(self.model_yes)
            model_layout.addWidget(self.model_no)
        else:
            ready = QLabel("✓ 模型已完整，无需处理")
            ready.setEnabled(False)
            model_layout.addWidget(ready)
        layout.addWidget(model_group)

        # DLL 分组
        dll_group = QGroupBox("DLL 依赖（cuBLAS / cuDNN，用于 GPU 加速）")
        dll_layout = QVBoxLayout(dll_group)
        if dll_missing:
            dll_btn_group = QButtonGroup(self)
            self.dll_yes = QRadioButton("是，下载 DLL（推荐有独显的用户）")
            self.dll_no = QRadioButton("否，跳过（使用 CPU 运行，速度较慢）")
            dll_btn_group.addButton(self.dll_yes)
            dll_btn_group.addButton(self.dll_no)
            self.dll_yes.setChecked(True)
            dll_layout.addWidget(self.dll_yes)
            dll_layout.addWidget(self.dll_no)

            keep_layout = QHBoxLayout()
            keep_label = QLabel("下载后：")
            self.keep_archive_yes = QRadioButton("保留压缩包")
            self.keep_archive_no = QRadioButton("删除（节省空间）")
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
            ready = QLabel("✓ DLL 已完整，无需处理")
            ready.setEnabled(False)
            dll_layout.addWidget(ready)
        layout.addWidget(dll_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_result(self) -> dict:
        download_dll = getattr(self, "dll_yes", None) is not None and self.dll_yes.isChecked()
        return {
            "download_model": getattr(self, "model_yes", None) is not None
            and self.model_yes.isChecked(),
            "download_dll": download_dll,
            "keep_archive": download_dll
            and getattr(self, "keep_archive_yes", None) is not None
            and self.keep_archive_yes.isChecked(),
        }
