"""可复用背景图片内容容器"""

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap, QPaintEvent
from PySide6.QtWidgets import QWidget


class BackgroundContent(QWidget):
    """带背景图片的内容容器

    在 paintEvent 中绘制背景图片，支持缩放居中与透明度控制。
    子控件绘制在背景之上，因此子控件需要透明背景才能透出图片。
    增加背景图缩放缓存，仅在窗口尺寸变更或图片变更时重算，杜绝每帧双三次插值重绘。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bg_pixmap: Optional[QPixmap] = None
        self.bg_opacity: float = 0.4  # 0.0 ~ 1.0
        self._cached_scaled_pixmap: Optional[QPixmap] = None
        self._cached_pos: tuple[int, int] = (0, 0)

    def set_bg_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        self.bg_pixmap = pixmap
        self._cached_scaled_pixmap = None
        self._update_scaled_pixmap()
        self.update()

    def set_bg_opacity(self, opacity: float) -> None:
        self.bg_opacity = max(0.0, min(1.0, opacity))
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self) -> None:
        if self.bg_pixmap and not self.bg_pixmap.isNull() and self.width() > 0 and self.height() > 0:
            self._cached_scaled_pixmap = self.bg_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - self._cached_scaled_pixmap.width()) // 2
            y = (self.height() - self._cached_scaled_pixmap.height()) // 2
            self._cached_pos = (x, y)
        else:
            self._cached_scaled_pixmap = None
            self._cached_pos = (0, 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        if self.bg_pixmap and not self.bg_pixmap.isNull():
            if self._cached_scaled_pixmap is None or self._cached_scaled_pixmap.isNull():
                self._update_scaled_pixmap()
            if self._cached_scaled_pixmap and not self._cached_scaled_pixmap.isNull():
                painter = QPainter(self)
                painter.setOpacity(self.bg_opacity)
                painter.drawPixmap(self._cached_pos[0], self._cached_pos[1], self._cached_scaled_pixmap)
                painter.end()
        super().paintEvent(event)
