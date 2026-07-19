import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
if not ASSETS_DIR.exists() and getattr(sys, "frozen", False):
    ASSETS_DIR = Path(sys.executable).parent / "assets"


class DonateDialog(QDialog):
    """捐赠对话框 —— 按图片原始尺寸显示，不缩放。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("捐赠支持")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._pixmap = QPixmap(str(ASSETS_DIR / "donate.png"))

        # 计算可用屏幕尺寸（留出边距，避免超出屏幕）
        max_w, max_h = 480, 600
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_w = int(avail.width() * 0.9)
            max_h = int(avail.height() * 0.9)

        if self._pixmap.isNull():
            if parent:
                geo = parent.geometry()
                self.resize(min(geo.width(), max_w), min(geo.height(), max_h))
            else:
                self.resize(min(480, max_w), min(600, max_h))
        else:
            # 图片超过屏幕时按比例缩放
            if (
                self._pixmap.width() > max_w
                or self._pixmap.height() > max_h
            ):
                self._pixmap = self._pixmap.scaled(
                    max_w,
                    max_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            pm_size = self._pixmap.size()
            self.resize(pm_size.width(), pm_size.height())
        self._setup_ui()

    def _setup_ui(self):
        """初始化 UI：创建图片标签并显示原始图片。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._img_label = QLabel()
        if self._pixmap.isNull():
            self._img_label.setText("图片未找到: assets/donate.png")
            self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self._img_label.setPixmap(self._pixmap)
            self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._img_label)
