"""可复用背景图片内容容器"""

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap, QPaintEvent
from PySide6.QtWidgets import QWidget


class BackgroundContent(QWidget):
    """带背景图片的内容容器

    在 paintEvent 中绘制背景图片，支持缩放居中与透明度控制。
    子控件绘制在背景之上，因此子控件需要透明背景才能透出图片。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bg_pixmap: Optional[QPixmap] = None
        self.bg_opacity: float = 0.4  # 0.0 ~ 1.0

    def set_bg_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        self.bg_pixmap = pixmap
        self.update()

    def set_bg_opacity(self, opacity: float) -> None:
        self.bg_opacity = max(0.0, min(1.0, opacity))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if self.bg_pixmap and not self.bg_pixmap.isNull():
            painter = QPainter(self)
            painter.setOpacity(self.bg_opacity)
            scaled = self.bg_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.end()
        super().paintEvent(event)
