import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
if not ASSETS_DIR.exists() and getattr(sys, "frozen", False):
    ASSETS_DIR = Path(sys.executable).parent / "assets"


class DonateDialog(QDialog):
    """捐赠对话框 —— 显示捐赠二维码图片，支持自适应缩放。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("捐赠支持")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        if parent:
            geo = parent.geometry()
            self.resize(geo.width(), geo.height())
        else:
            self.resize(480, 600)
        self._pixmap = QPixmap(str(ASSETS_DIR / "donate.png"))
        self._setup_ui()

    def _setup_ui(self):
        """初始化 UI：创建图片标签并加载捐赠二维码。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._img_label = QLabel()
        if self._pixmap.isNull():
            self._img_label.setText("图片未找到: assets/donate.png")
            self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self._update_pixmap()
        layout.addWidget(self._img_label)

    def _update_pixmap(self):
        """按窗口大小等比缩放捐赠图片并居中显示。"""
        self._img_label.setPixmap(
            self._pixmap.scaled(
                self._img_label.width(),
                self._img_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        if not self._pixmap.isNull():
            self._update_pixmap()
