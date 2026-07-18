import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
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
        if self._pixmap.isNull():
            if parent:
                geo = parent.geometry()
                self.resize(geo.width(), geo.height())
            else:
                self.resize(480, 600)
        else:
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
