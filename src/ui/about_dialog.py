"""关于对话框 —— 展示版本、描述、技术栈、QQ交流群及相关链接"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.config.version import APP_VERSION
from src.i18n import t


class AboutDialog(QDialog):
    """关于对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("about.title"))
        self.setMinimumWidth(480)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self._setup_ui()

    def _create_separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # 头部：应用名称与版本
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        title_label = QLabel("<h2 style='margin:0;'>🎬 Video2Text</h2>")
        title_label.setTextFormat(Qt.TextFormat.RichText)
        version_text = t("about.version", version=APP_VERSION)
        version_label = QLabel(f"<p style='color:#666;margin:0;'>{version_text}</p>")
        version_label.setTextFormat(Qt.TextFormat.RichText)

        header_layout.addWidget(title_label)
        header_layout.addWidget(version_label)
        layout.addWidget(header_widget)

        # 分割线
        layout.addWidget(self._create_separator())

        # 描述信息
        desc_label = QLabel(t("about.desc"))
        desc_label.setWordWrap(True)
        desc_label.setTextFormat(Qt.TextFormat.RichText)
        desc_label.setStyleSheet("font-size: 13px; line-height: 1.4;")
        layout.addWidget(desc_label)

        # 分割线
        layout.addWidget(self._create_separator())

        # 详细信息网格
        grid = QGridLayout()
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(16)

        label_style = "color: #888; font-size: 13px;"
        val_style = "font-size: 13px;"

        row = 0
        # 作者
        lbl_author = QLabel(t("about.author_label"))
        lbl_author.setStyleSheet(label_style)
        val_author = QLabel(t("about.author_value"))
        val_author.setStyleSheet(val_style)
        grid.addWidget(lbl_author, row, 0)
        grid.addWidget(val_author, row, 1)

        row += 1
        # 许可证
        lbl_license = QLabel(t("about.license_label"))
        lbl_license.setStyleSheet(label_style)
        val_license = QLabel(t("about.license_value"))
        val_license.setStyleSheet(val_style)
        grid.addWidget(lbl_license, row, 0)
        grid.addWidget(val_license, row, 1)

        row += 1
        # 技术栈
        lbl_tech = QLabel(t("about.tech_label"))
        lbl_tech.setStyleSheet(label_style)
        val_tech = QLabel(t("about.tech_value"))
        val_tech.setStyleSheet(val_style)
        grid.addWidget(lbl_tech, row, 0)
        grid.addWidget(val_tech, row, 1)

        row += 1
        # 邮箱
        lbl_email = QLabel(t("about.email_label"))
        lbl_email.setStyleSheet(label_style)
        val_email = QLabel(
            "<a href='mailto:admin@video2text.dpdns.org'>admin@video2text.dpdns.org</a>"
        )
        val_email.setStyleSheet(val_style)
        val_email.setOpenExternalLinks(True)
        grid.addWidget(lbl_email, row, 0)
        grid.addWidget(val_email, row, 1)

        row += 1
        # QQ交流群 + 复制按钮
        lbl_qq = QLabel(t("about.qq_group_label"))
        lbl_qq.setStyleSheet(label_style)

        qq_container = QWidget()
        qq_layout = QHBoxLayout(qq_container)
        qq_layout.setContentsMargins(0, 0, 0, 0)
        qq_layout.setSpacing(10)

        self.qq_number = t("about.qq_group_value")
        self.qq_val_label = QLabel(self.qq_number)
        self.qq_val_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.qq_val_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.copy_btn = QPushButton(t("about.copy_btn"))
        self.copy_btn.setToolTip(t("about.copy_tooltip"))
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setFixedHeight(24)
        self.copy_btn.setStyleSheet(
            "QPushButton {"
            "   padding: 2px 10px;"
            "   font-size: 12px;"
            "   border: 1px solid #c0c4cc;"
            "   border-radius: 4px;"
            "   background-color: #f4f6fa;"
            "}"
            "QPushButton:hover {"
            "   background-color: #e4e7ed;"
            "   color: #409eff;"
            "   border-color: #b3d8ff;"
            "}"
        )
        self.copy_btn.clicked.connect(self._on_copy_qq_group)

        qq_layout.addWidget(self.qq_val_label)
        qq_layout.addWidget(self.copy_btn)
        qq_layout.addStretch(1)

        grid.addWidget(lbl_qq, row, 0)
        grid.addWidget(qq_container, row, 1)

        layout.addLayout(grid)

        # 分割线
        layout.addWidget(self._create_separator())

        # 外部链接
        links_html = (
            f'<a href="https://github.com/fuyouling/video2text">{t("about.repo_link")}</a> · '
            f'<a href="https://video2text.dpdns.org/en/docs/">{t("about.docs_link")}</a> · '
            f'<a href="https://video2text.dpdns.org/">{t("about.website_link")}</a> · '
            f'<a href="https://video2text.dpdns.org/zh/blog/access-github-download/">{t("about.netdisk_link")}</a>'
        )
        links_label = QLabel(links_html)
        links_label.setOpenExternalLinks(True)
        links_label.setTextFormat(Qt.TextFormat.RichText)
        links_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(links_label)

        # 版权声明
        copyright_label = QLabel(t("about.copyright"))
        copyright_label.setStyleSheet("color: #999; font-size: 12px;")
        layout.addWidget(copyright_label)

    def _on_copy_qq_group(self) -> None:
        """点击复制群号到系统剪贴板并提供视觉反馈"""
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.qq_number)
        self.copy_btn.setText(f"✓ {t('about.copied')}")
        self.copy_btn.setStyleSheet(
            "QPushButton {"
            "   padding: 2px 10px;"
            "   font-size: 12px;"
            "   border: 1px solid #67c23a;"
            "   border-radius: 4px;"
            "   background-color: #f0f9eb;"
            "   color: #67c23a;"
            "   font-weight: bold;"
            "}"
        )
        QTimer.singleShot(1500, self._reset_copy_btn)

    def _reset_copy_btn(self) -> None:
        """恢复复制按钮初始状态"""
        try:
            self.copy_btn.setText(t("about.copy_btn"))
            self.copy_btn.setStyleSheet(
                "QPushButton {"
                "   padding: 2px 10px;"
                "   font-size: 12px;"
                "   border: 1px solid #c0c4cc;"
                "   border-radius: 4px;"
                "   background-color: #f4f6fa;"
                "}"
                "QPushButton:hover {"
                "   background-color: #e4e7ed;"
                "   color: #409eff;"
                "   border-color: #b3d8ff;"
                "}"
            )
        except RuntimeError:
            # 窗口已被销毁时忽略
            pass

