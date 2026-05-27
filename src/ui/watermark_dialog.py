"""去水印对话框 —— 支持单图/批量图片水印去除

处理模式：blur（高斯模糊）、fill（色块填充）、inpaint（图像补全）
绘制模式：rect（矩形框选）、freehand（任意圈选）
"""

from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QThread, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings

# Region: rect=(x, y, w, h) 4-int tuple; polygon=((x0,y0),(x1,y1),...) N-point tuple
Region = Union[tuple[int, int, int, int], tuple[tuple[int, int], ...]]


def _is_rect(r: Region) -> bool:
    return len(r) == 4 and isinstance(r[0], int)


# ── 中文路径兼容 I/O ────────────────────────────────────────────────────


def imread(path: str, flags: int = cv2.IMREAD_UNCHANGED) -> Optional[np.ndarray]:
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except Exception:
        return None


def imwrite(path: str, img: np.ndarray) -> bool:
    suffix = Path(path).suffix.lower()
    ok, buf = cv2.imencode(suffix, img)
    if not ok:
        return False
    try:
        buf.tofile(path)
        return True
    except Exception:
        return False


# ── mask 构建工具 ──────────────────────────────────────────────────────


def _build_region_mask(h: int, w: int, regions: list[Region]) -> np.ndarray:
    """将 rect + polygon regions 合成为一张 uint8 mask（选区内=255）"""
    mask = np.zeros((h, w), dtype=np.uint8)
    for r in regions:
        if _is_rect(r):
            x, y, rw, rh = r
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + rw), min(h, y + rh)
            mask[y0:y1, x0:x1] = 255
        else:
            pts = np.array(r, dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)
    return mask


# ── 处理算法 ───────────────────────────────────────────────────────────


def apply_blur(img: np.ndarray, regions: list[Region], ksize: int) -> np.ndarray:
    ksize = ksize | 1
    h, w = img.shape[:2]
    img_f = img.astype(np.float32)
    blurred = cv2.GaussianBlur(img_f, (ksize, ksize), 0)

    mask_u8 = _build_region_mask(h, w, regions)
    mask = mask_u8.astype(np.float32) / 255.0
    mask = cv2.GaussianBlur(mask, (15, 15), 0)
    mask3 = np.stack([mask] * 3, axis=-1)
    result = img_f * (1 - mask3) + blurred * mask3
    return np.clip(result, 0, 255).astype(np.uint8)


def _sample_outer_color(
    img: np.ndarray, mask_u8: np.ndarray, region: Region
) -> np.ndarray:
    """从选区外围取中位色，fallback：四角 → 全图中位色"""
    h, w = img.shape[:2]

    if _is_rect(region):
        x, y, rw, rh = region
        bx = min(10, max(1, rw // 4))
        by = min(10, max(1, rh // 4))
        outer = mask_u8.copy()
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + rw), min(h, y + rh)
        outer[y0:y1, x0:x1] = 0
        dilate_k = max(bx, by) * 2 + 1
        kernel = np.ones((dilate_k, dilate_k), np.uint8)
        ring = cv2.dilate(mask_u8, kernel, iterations=1)
        ring[y0:y1, x0:x1] = 0
        outer_pixels = img[ring == 255]
        if len(outer_pixels) == 0:
            ring2 = cv2.dilate(mask_u8, kernel, iterations=2)
            ring2[mask_u8 == 255] = 0
            outer_pixels = img[ring2 == 255]
    else:
        pts = np.array(region, dtype=np.int32)
        xs, ys = pts[:, 0], pts[:, 1]
        cx, cy = int(np.mean(xs)), int(np.mean(ys))
        border = max(1, min(10, int(cv2.contourArea(pts) ** 0.5) // 4))
        kernel_sz = border * 2 + 1
        kernel = np.ones((kernel_sz, kernel_sz), np.uint8)
        ring = cv2.dilate(mask_u8, kernel, iterations=1)
        ring[mask_u8 == 255] = 0
        outer_pixels = img[ring == 255]

    if len(outer_pixels) > 0:
        return np.median(outer_pixels, axis=0)

    corners = []
    for cx, cy in [(5, 5), (w - 6, 5), (5, h - 6), (w - 6, h - 6)]:
        cx = max(0, min(cx, w - 1))
        cy = max(0, min(cy, h - 1))
        corners.append(img[cy, cx])
    corner_pixels = np.array(corners)
    if len(corner_pixels) > 0:
        return np.median(corner_pixels, axis=0)
    return np.median(img.reshape(-1, img.shape[2]), axis=0)


def apply_fill(img: np.ndarray, regions: list[Region]) -> np.ndarray:
    result = img.copy()
    h, w = img.shape[:2]
    mask_u8 = _build_region_mask(h, w, regions)

    for r in regions:
        fill_color = _sample_outer_color(img, mask_u8, r)
        if _is_rect(r):
            x, y, rw, rh = r
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + rw), min(h, y + rh)
            result[y0:y1, x0:x1] = fill_color.astype(np.uint8)
        else:
            pts = np.array(r, dtype=np.int32)
            cv2.fillPoly(result, [pts], tuple(int(c) for c in fill_color))
    return result


def apply_inpaint(img: np.ndarray, regions: list[Region], radius: int) -> np.ndarray:
    h, w = img.shape[:2]
    mask = _build_region_mask(h, w, regions)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=2)
    return cv2.inpaint(img, mask, radius, cv2.INPAINT_TELEA)


def apply_watermark_removal(
    img: np.ndarray, regions: list[Region], mode: str, params: dict
) -> np.ndarray:
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.ndim == 3 and img.shape[2] == 1:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    has_alpha = img.ndim == 3 and img.shape[2] == 4
    if has_alpha:
        bgr = img[:, :, :3]
        alpha = img[:, :, 3]
    else:
        bgr = img
        alpha = None

    if mode == "blur":
        result = apply_blur(bgr, regions, params.get("ksize", 51))
    elif mode == "fill":
        result = apply_fill(bgr, regions)
    elif mode == "inpaint":
        result = apply_inpaint(bgr, regions, params.get("radius", 5))
    else:
        result = bgr

    if alpha is not None:
        result = cv2.merge([result, alpha])
    return result


# ── CanvasWidget ────────────────────────────────────────────────────────


class CanvasWidget(QWidget):
    regions_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._img_size: tuple[int, int] = (0, 0)
        self._regions: list[Region] = []
        self._draw_mode = "rect"  # "rect" | "freehand" | "polygon"
        self._drawing = False
        self._start_pos: Optional[QPoint] = None
        self._current_rect: Optional[QRect] = None
        self._freehand_widget_pts: list[QPointF] = []
        self._freehand_img_pts: list[tuple[int, int]] = []
        self._polygon_widget_pts: list[QPointF] = []
        self._polygon_img_pts: list[tuple[int, int]] = []
        self._polygon_cursor: Optional[QPointF] = None
        self.setMinimumSize(200, 200)

    def set_draw_mode(self, mode: str) -> None:
        self._draw_mode = mode

    def set_cv_image(self, cv_img: np.ndarray) -> None:
        from PySide6.QtGui import QImage

        h, w = cv_img.shape[:2]
        self._img_size = (w, h)

        if cv_img.ndim == 3 and cv_img.shape[2] == 4:
            cv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGRA2RGBA)
            fmt = QImage.Format.Format_RGBA8888
        elif cv_img.ndim == 3:
            cv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
            fmt = QImage.Format.Format_RGB888
        else:
            cv_img = cv2.cvtColor(cv_img, cv2.COLOR_GRAY2RGB)
            fmt = QImage.Format.Format_RGB888

        bytes_per_line = cv_img.strides[0]
        qimg = QImage(cv_img.data, w, h, bytes_per_line, fmt).copy()
        self._pixmap = QPixmap.fromImage(qimg)
        self.update()

    def add_region(self, region: Region) -> None:
        self._regions.append(region)
        self.regions_changed.emit()
        self.update()

    def get_regions(self) -> list[Region]:
        return list(self._regions)

    def get_regions_as_tuples(self) -> list[Region]:
        return list(self._regions)

    def set_regions(self, regions: list[Region]) -> None:
        self._regions = list(regions)
        self.regions_changed.emit()
        self.update()

    def clear_regions(self) -> None:
        self._regions.clear()
        self.regions_changed.emit()
        self.update()

    def undo_last_region(self) -> None:
        if self._regions:
            self._regions.pop()
            self.regions_changed.emit()
            self.update()

    def _compute_display_rect(self) -> tuple[QRect, float, float]:
        if self._pixmap is None or self._pixmap.isNull():
            return QRect(0, 0, 0, 0), 0.0, 0.0

        ww, wh = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw == 0 or ph == 0:
            return QRect(0, 0, 0, 0), 0.0, 0.0

        scale = min(ww / pw, wh / ph)
        scaled_w = int(pw * scale)
        scaled_h = int(ph * scale)
        x_off = (ww - scaled_w) / 2
        y_off = (wh - scaled_h) / 2
        return QRect(int(x_off), int(y_off), scaled_w, scaled_h), scale, 0.0

    def _widget_to_image(self, point: QPoint) -> Optional[QPoint]:
        if self._pixmap is None or self._pixmap.isNull():
            return None
        rect, _, _ = self._compute_display_rect()
        if rect.width() == 0 or rect.height() == 0:
            return None

        wx, wy = point.x(), point.y()
        if not (rect.x() <= wx <= rect.x() + rect.width()):
            return None
        if not (rect.y() <= wy <= rect.y() + rect.height()):
            return None

        iw, ih = self._img_size
        ix = (wx - rect.x()) * iw / rect.width()
        iy = (wy - rect.y()) * ih / rect.height()
        return QPoint(round(ix), round(iy))

    def _widget_to_image_f(self, point: QPointF) -> Optional[tuple[int, int]]:
        rect, _, _ = self._compute_display_rect()
        if rect.width() == 0 or rect.height() == 0:
            return None
        wx, wy = point.x(), point.y()
        if not (rect.x() <= wx <= rect.x() + rect.width()):
            return None
        if not (rect.y() <= wy <= rect.y() + rect.height()):
            return None
        iw, ih = self._img_size
        ix = (wx - rect.x()) * iw / rect.width()
        iy = (wy - rect.y()) * ih / rect.height()
        return (round(ix), round(iy))

    def _image_to_widget_rect(self, irect: QRect) -> Optional[QRect]:
        rect, _, _ = self._compute_display_rect()
        if rect.width() == 0 or rect.height() == 0:
            return None
        iw, ih = self._img_size
        if iw == 0 or ih == 0:
            return None

        sx = rect.width() / iw
        sy = rect.height() / ih
        wx = rect.x() + irect.x() * sx
        wy = rect.y() + irect.y() * sy
        ww = irect.width() * sx
        wh = irect.height() * sy
        return QRect(round(wx), round(wy), round(ww), round(wh))

    def _image_pt_to_widget(self, ix: int, iy: int) -> Optional[QPointF]:
        rect, _, _ = self._compute_display_rect()
        iw, ih = self._img_size
        if iw == 0 or ih == 0:
            return None
        sx = rect.width() / iw
        sy = rect.height() / ih
        return QPointF(rect.x() + ix * sx, rect.y() + iy * sy)

    def _widget_to_image_rect(self, wrect: QRect) -> Optional[QRect]:
        p1 = self._widget_to_image(wrect.topLeft())
        p2 = self._widget_to_image(wrect.bottomRight())
        if p1 is None or p2 is None:
            return None
        x = min(p1.x(), p2.x())
        y = min(p1.y(), p2.y())
        w = abs(p2.x() - p1.x())
        h = abs(p2.y() - p1.y())
        iw, ih = self._img_size
        x = max(0, min(x, iw))
        y = max(0, min(y, ih))
        if x + w > iw:
            w = iw - x
        if y + h > ih:
            h = ih - y
        return QRect(x, y, w, h)

    def _region_to_path(self, region: Region) -> Optional[QPainterPath]:
        path = QPainterPath()
        if _is_rect(region):
            x, y, w, h = region
            wrect = self._image_to_widget_rect(QRect(x, y, w, h))
            if wrect is None:
                return None
            path.addRect(QRectF(wrect))
        else:
            wpts = []
            for ix, iy in region:
                wp = self._image_pt_to_widget(ix, iy)
                if wp is None:
                    return None
                wpts.append(wp)
            if len(wpts) < 3:
                return None
            poly = QPolygonF(wpts)
            path.addPolygon(poly)
            path.closeSubpath()
        return path

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#2b2b2b"))

        if self._pixmap is None or self._pixmap.isNull():
            painter.end()
            return

        rect, _, _ = self._compute_display_rect()
        painter.drawPixmap(rect, self._pixmap)

        for region in self._regions:
            path = self._region_to_path(region)
            if path is None:
                continue
            painter.setBrush(QColor(255, 0, 0, 60))
            painter.setPen(QPen(QColor(255, 0, 0, 180), 2))
            painter.drawPath(path)

        if self._drawing:
            if self._draw_mode == "rect" and self._current_rect is not None:
                painter.setBrush(QColor(0, 100, 255, 40))
                painter.setPen(QPen(QColor(0, 100, 255, 200), 1, Qt.PenStyle.DashLine))
                painter.drawRect(self._current_rect)
            elif self._draw_mode == "freehand" and len(self._freehand_widget_pts) >= 2:
                painter.setBrush(QColor(0, 100, 255, 40))
                painter.setPen(QPen(QColor(0, 100, 255, 200), 1, Qt.PenStyle.DashLine))
                poly = QPolygonF(self._freehand_widget_pts)
                path = QPainterPath()
                path.addPolygon(poly)
                painter.drawPath(path)
            elif self._draw_mode == "polygon" and len(self._polygon_widget_pts) >= 1:
                painter.setBrush(QColor(0, 100, 255, 40))
                painter.setPen(QPen(QColor(0, 100, 255, 200), 1, Qt.PenStyle.DashLine))
                pts = list(self._polygon_widget_pts)
                if self._polygon_cursor is not None:
                    pts.append(self._polygon_cursor)
                if len(pts) >= 2:
                    painter.drawPolyline(QPolygonF(pts))
                for p in self._polygon_widget_pts:
                    painter.setBrush(QColor(0, 100, 255, 200))
                    painter.drawEllipse(p, 3, 3)

        painter.end()

    def mousePressEvent(self, event) -> None:
        if self._pixmap is None:
            return

        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            img_pt = self._widget_to_image(pos)
            if img_pt is None:
                return

            if self._draw_mode == "rect":
                self._drawing = True
                self._start_pos = pos
                self._current_rect = QRect(pos, pos)
            elif self._draw_mode == "freehand":
                self._drawing = True
                self._freehand_widget_pts = [QPointF(pos)]
                self._freehand_img_pts = [self._widget_to_image_f(QPointF(pos))]
            elif self._draw_mode == "polygon":
                self._polygon_widget_pts.append(QPointF(pos))
                pt = self._widget_to_image_f(QPointF(pos))
                if pt is not None:
                    self._polygon_img_pts.append(pt)
                self._drawing = True
                self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            if self._draw_mode == "polygon" and self._polygon_widget_pts:
                self._polygon_widget_pts.clear()
                self._polygon_img_pts.clear()
                self._polygon_cursor = None
                self._drawing = False
                self.update()
                return
            pos = event.pos()
            img_pt = self._widget_to_image(pos)
            if img_pt is None:
                return
            self._remove_region_at(img_pt)

    def mouseMoveEvent(self, event) -> None:
        if self._draw_mode == "rect" and self._drawing and self._start_pos is not None:
            self._current_rect = QRect(self._start_pos, event.pos()).normalized()
            self.update()
        elif self._draw_mode == "freehand" and self._drawing:
            self._freehand_widget_pts.append(QPointF(event.pos()))
            pt = self._widget_to_image_f(QPointF(event.pos()))
            if pt is not None:
                self._freehand_img_pts.append(pt)
            self.update()
        elif self._draw_mode == "polygon" and self._polygon_widget_pts:
            self._polygon_cursor = QPointF(event.pos())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._draw_mode == "rect":
            if not self._drawing:
                return
            self._drawing = False
            if self._current_rect is not None:
                w, h = self._current_rect.width(), self._current_rect.height()
                if w >= 5 and h >= 5:
                    img_rect = self._widget_to_image_rect(self._current_rect)
                    if (
                        img_rect is not None
                        and img_rect.width() >= 5
                        and img_rect.height() >= 5
                    ):
                        self.add_region(
                            (
                                img_rect.x(),
                                img_rect.y(),
                                img_rect.width(),
                                img_rect.height(),
                            )
                        )
            self._current_rect = None
        elif self._draw_mode == "freehand":
            if not self._drawing:
                return
            self._drawing = False
            pts = self._freehand_img_pts
            if len(pts) >= 10:
                valid_pts = [p for p in pts if p is not None]
                unique = [valid_pts[0]]
                for p in valid_pts[1:]:
                    if p != unique[-1]:
                        unique.append(p)
                if len(unique) >= 3:
                    self.add_region(tuple(unique))
            self._freehand_widget_pts.clear()
            self._freehand_img_pts.clear()
        # polygon mode: no action on release (points added on click)

        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        if self._draw_mode == "polygon" and self._polygon_widget_pts:
            # remove duplicate points from the double-click press events
            pts = self._polygon_img_pts
            if len(pts) >= 2 and pts[-1] == pts[-2]:
                pts.pop()
                self._polygon_widget_pts.pop()
            self._finalize_polygon()
        else:
            super().mouseDoubleClickEvent(event)

    def _finalize_polygon(self) -> None:
        pts = self._polygon_img_pts
        if len(pts) >= 3:
            self.add_region(tuple(pts))
        self._polygon_widget_pts.clear()
        self._polygon_img_pts.clear()
        self._polygon_cursor = None
        self._drawing = False
        self.update()

    def _remove_region_at(self, img_pt: QPoint) -> None:
        candidates = []
        for i, r in enumerate(self._regions):
            if _is_rect(r):
                x, y, w, h = r
                rect = QRect(x, y, w, h)
                if rect.contains(img_pt):
                    candidates.append((w * h, i))
            else:
                pts = np.array(r, dtype=np.int32)
                dist = cv2.pointPolygonTest(
                    pts, (float(img_pt.x()), float(img_pt.y())), True
                )
                if dist >= -2:
                    xs = [p[0] for p in r]
                    ys = [p[1] for p in r]
                    area = (max(xs) - min(xs)) * (max(ys) - min(ys))
                    candidates.append((area, i))
        if candidates:
            candidates.sort()
            idx = candidates[0][1]
            self._regions.pop(idx)
            self.regions_changed.emit()
            self.update()

    def resizeEvent(self, _event) -> None:
        self.update()


# ── WatermarkRemovalDialog ──────────────────────────────────────────────


class WatermarkRemovalDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = Settings()
        self._mode = "single"
        self._process_mode = "blur"
        self._cv_img: Optional[np.ndarray] = None
        self._original_img: Optional[np.ndarray] = None
        self._result_img: Optional[np.ndarray] = None
        self._file_path: Optional[str] = None
        self._batch_files: list[str] = []
        self._batch_regions: dict[str, list[Region]] = {}
        self._worker_thread: Optional[QThread] = None
        self._worker = None
        self._batch_done_count = 0
        self._batch_fail_count = 0
        self._batch_skip_count = 0

        self._init_ui()
        self._on_mode_changed(0)

    def _init_ui(self) -> None:
        self.setWindowTitle("去水印")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(1000, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.setContentsMargins(0, 0, 0, 0)

        self._open_btn = QPushButton("打开图片")
        self._open_btn.clicked.connect(self._open_image)
        toolbar.addWidget(self._open_btn)

        self._batch_btn = QPushButton("批量模式")
        self._batch_btn.clicked.connect(self._switch_to_batch)
        toolbar.addWidget(self._batch_btn)

        self._single_btn = QPushButton("单图模式")
        self._single_btn.clicked.connect(self._switch_to_single)
        self._single_btn.setVisible(False)
        toolbar.addWidget(self._single_btn)

        self._batch_add_btn = QPushButton("批量添加")
        self._batch_add_btn.clicked.connect(self._add_batch_files)
        self._batch_add_btn.setVisible(False)
        toolbar.addWidget(self._batch_add_btn)

        self._batch_folder_btn = QPushButton("添加文件夹")
        self._batch_folder_btn.clicked.connect(self._add_batch_folder)
        self._batch_folder_btn.setVisible(False)
        toolbar.addWidget(self._batch_folder_btn)

        self._batch_clear_btn = QPushButton("清空列表")
        self._batch_clear_btn.clicked.connect(self._clear_batch)
        self._batch_clear_btn.setVisible(False)
        toolbar.addWidget(self._batch_clear_btn)

        self._process_all_btn = QPushButton("全部处理")
        self._process_all_btn.clicked.connect(self._process_all)
        self._process_all_btn.setVisible(False)
        toolbar.addWidget(self._process_all_btn)

        _lbl_draw = QLabel("绘制:")
        _lbl_draw.setStyleSheet("QLabel { margin: 0 1px; padding-left: 6px; }")
        toolbar.addWidget(_lbl_draw)
        self._draw_combo = QComboBox()
        self._draw_combo.addItem("矩形框选", "rect")
        self._draw_combo.addItem("任意圈选", "freehand")
        self._draw_combo.addItem("多点连线", "polygon")
        self._draw_combo.currentIndexChanged.connect(self._on_draw_mode_changed)
        toolbar.addWidget(self._draw_combo)

        _lbl_process = QLabel("处理:")
        _lbl_process.setStyleSheet("QLabel { margin: 0 1px; padding-left: 6px; }")
        toolbar.addWidget(_lbl_process)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("高斯模糊", "blur")
        self._mode_combo.addItem("色块填充", "fill")
        self._mode_combo.addItem("图像补全", "inpaint")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        toolbar.addWidget(self._mode_combo)

        self._blur_label = QLabel("模糊核:")
        self._blur_label.setStyleSheet("QLabel { margin: 0 1px; padding-left: 6px; }")
        toolbar.addWidget(self._blur_label)
        self._blur_spin = QSpinBox()
        self._blur_spin.setRange(3, 99)
        self._blur_spin.setValue(51)
        self._blur_spin.setSingleStep(2)
        toolbar.addWidget(self._blur_spin)

        self._inpaint_label = QLabel("修复半径:")
        self._inpaint_label.setStyleSheet(
            "QLabel { margin: 0 1px; padding-left: 6px; }"
        )
        toolbar.addWidget(self._inpaint_label)
        self._inpaint_spin = QSpinBox()
        self._inpaint_spin.setRange(1, 20)
        self._inpaint_spin.setValue(5)
        toolbar.addWidget(self._inpaint_spin)

        self._process_btn = QPushButton("处理")
        self._process_btn.clicked.connect(self._process_current)
        toolbar.addWidget(self._process_btn)

        self._save_btn = QPushButton("保存")
        self._save_btn.clicked.connect(self._save_result)
        self._save_btn.setEnabled(False)
        toolbar.addWidget(self._save_btn)

        self._reset_btn = QPushButton("重置")
        self._reset_btn.clicked.connect(self._reset_image)
        self._reset_btn.setEnabled(False)
        toolbar.addWidget(self._reset_btn)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self._cancel_batch)
        self._cancel_btn.setVisible(False)
        toolbar.addWidget(self._cancel_btn)

        layout.addLayout(toolbar)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._file_list = QListWidget()
        self._file_list.setMinimumWidth(180)
        self._file_list.currentItemChanged.connect(self._on_file_selected)
        self._file_list.setVisible(False)
        self._splitter.addWidget(self._file_list)

        self._canvas = CanvasWidget()
        self._splitter.addWidget(self._canvas)

        self._splitter.setSizes([0, 1])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        layout.addWidget(self._splitter, 1)

        self._status = QStatusBar()
        layout.addWidget(self._status)

        QShortcut(QKeySequence("Ctrl+Z"), self, self._canvas.undo_last_region)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, self._on_enter_pressed)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._on_escape_pressed)
        self._canvas.regions_changed.connect(self._update_status)

    def _on_draw_mode_changed(self, idx: int) -> None:
        self._canvas.set_draw_mode(self._draw_combo.currentData())

    def _on_enter_pressed(self) -> None:
        if self._canvas._polygon_widget_pts:
            self._canvas._finalize_polygon()

    def _on_escape_pressed(self) -> None:
        if self._canvas._polygon_widget_pts:
            self._canvas._polygon_widget_pts.clear()
            self._canvas._polygon_img_pts.clear()
            self._canvas._polygon_cursor = None
            self._canvas._drawing = False
            self._canvas.update()

    def _on_mode_changed(self, _idx: int) -> None:
        self._process_mode = self._mode_combo.currentData()
        self._blur_label.setVisible(self._process_mode == "blur")
        self._blur_spin.setVisible(self._process_mode == "blur")
        self._inpaint_label.setVisible(self._process_mode == "inpaint")
        self._inpaint_spin.setVisible(self._process_mode == "inpaint")

    def _update_status(self) -> None:
        regions = self._canvas.get_regions()
        parts = []
        if self._file_path:
            parts.append(f"已加载 {Path(self._file_path).name}")
            if self._cv_img is not None:
                h, w = self._cv_img.shape[:2]
                parts.append(f"({w}x{h})")
        parts.append(f"已选中 {len(regions)} 个区域")
        self._status.showMessage(" | ".join(parts))

    def _update_button_states(self) -> None:
        is_batch = self._mode == "batch"
        processing = self._worker_thread is not None and self._worker_thread.isRunning()

        self._save_btn.setVisible(not is_batch)
        self._reset_btn.setVisible(not is_batch)
        self._cancel_btn.setVisible(is_batch)
        self._batch_add_btn.setVisible(is_batch)
        self._batch_folder_btn.setVisible(is_batch)
        self._batch_clear_btn.setVisible(is_batch)
        self._process_all_btn.setVisible(is_batch)

        if not is_batch:
            self._save_btn.setEnabled(self._result_img is not None)
            self._reset_btn.setEnabled(self._original_img is not None)

        if processing:
            self._process_btn.setEnabled(False)
            self._open_btn.setEnabled(False)
            self._batch_btn.setEnabled(False)
            self._batch_add_btn.setEnabled(False)
            self._batch_folder_btn.setEnabled(False)
            self._batch_clear_btn.setEnabled(False)
            self._process_all_btn.setEnabled(False)
            self._mode_combo.setEnabled(False)
        else:
            self._process_btn.setEnabled(True)
            self._open_btn.setEnabled(True)
            self._batch_btn.setEnabled(True)
            self._batch_add_btn.setEnabled(True)
            self._batch_folder_btn.setEnabled(True)
            self._batch_clear_btn.setEnabled(True)
            self._process_all_btn.setEnabled(True)
            self._mode_combo.setEnabled(True)

    # ── 单图模式 ──

    def _open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.webp *.tiff);;所有文件 (*.*)",
        )
        if not path:
            return
        self._load_single_image(path)

    def _load_single_image(self, path: str) -> None:
        img = imread(path)
        if img is None:
            QMessageBox.warning(self, "错误", f"无法读取图片: {path}")
            return
        self._cv_img = img
        self._original_img = img.copy()
        self._result_img = None
        self._file_path = path
        self._canvas.clear_regions()
        self._canvas.set_cv_image(img)
        self._update_status()
        self._update_button_states()

    def _process_current(self) -> None:
        if self._cv_img is None:
            QMessageBox.information(self, "提示", "请先打开图片。")
            return
        regions = self._canvas.get_regions_as_tuples()
        if not regions:
            QMessageBox.information(self, "提示", "请先框选水印区域。")
            return

        params = self._get_params()
        try:
            result = apply_watermark_removal(
                self._cv_img, regions, self._process_mode, params
            )
            self._result_img = result
            self._cv_img = result
            self._canvas.set_cv_image(result)
            self._update_status()
            self._update_button_states()
        except Exception as e:
            QMessageBox.warning(self, "处理失败", str(e))

    def _save_result(self) -> None:
        if self._result_img is None or self._file_path is None:
            return
        default_name = (
            Path(self._file_path).stem + "_nowm" + Path(self._file_path).suffix
        )
        default_dir = str(Path(self._file_path).parent)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存图片",
            str(Path(default_dir) / default_name),
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.webp);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            imwrite(path, self._result_img)
            self._status.showMessage(f"已保存: {path}", 5000)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def _reset_image(self) -> None:
        if self._original_img is None:
            return
        self._cv_img = self._original_img.copy()
        self._result_img = None
        self._canvas.clear_regions()
        self._canvas.set_cv_image(self._cv_img)
        self._update_status()
        self._update_button_states()

    # ── 模式切换 ──

    def _switch_to_batch(self) -> None:
        self._mode = "batch"
        self._batch_btn.setVisible(False)
        self._single_btn.setVisible(True)
        self._file_list.setVisible(True)
        self._splitter.setSizes([200, 600])
        self._update_button_states()

    def _switch_to_single(self) -> None:
        self._mode = "single"
        self._batch_btn.setVisible(True)
        self._single_btn.setVisible(False)
        self._file_list.setVisible(False)
        self._splitter.setSizes([0, 1])
        self._update_button_states()

    # ── 批量模式 ──

    def _add_batch_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.webp *.tiff);;所有文件 (*.*)",
        )
        if not paths:
            return

        existing = set(self._batch_files)
        new_paths = [p for p in paths if p not in existing]

        total = len(self._batch_files) + len(new_paths)
        max_batch = self.settings.get_int("tools.watermark_max_batch", 200)
        if total > max_batch:
            reply = QMessageBox.question(
                self,
                "确认",
                f"添加后共 {total} 张图片（上限 {max_batch}），是否继续？",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._batch_files.extend(new_paths)
        for p in new_paths:
            item = QListWidgetItem(Path(p).name)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self._file_list.addItem(item)

        if self._file_list.count() > 0 and self._file_list.currentItem() is None:
            self._file_list.setCurrentRow(0)

        self._status.showMessage(f"已加载 {len(self._batch_files)} 张图片")

    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".tif"}

    def _add_batch_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not folder:
            return
        found = []
        for f in Path(folder).rglob("*"):
            if f.is_file() and f.suffix.lower() in self._IMAGE_EXTS:
                found.append(str(f))
        if not found:
            QMessageBox.information(self, "提示", "该文件夹中未找到图片文件。")
            return
        existing = set(self._batch_files)
        new_paths = [p for p in found if p not in existing]
        self._batch_files.extend(new_paths)
        for p in new_paths:
            item = QListWidgetItem(Path(p).name)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self._file_list.addItem(item)
        if self._file_list.count() > 0 and self._file_list.currentItem() is None:
            self._file_list.setCurrentRow(0)
        self._status.showMessage(f"已加载 {len(self._batch_files)} 张图片")

    def _clear_batch(self) -> None:
        self._save_current_batch_regions()
        self._batch_files.clear()
        self._batch_regions.clear()
        self._file_list.clear()
        self._cv_img = None
        self._original_img = None
        self._result_img = None
        self._file_path = None
        self._canvas.clear_regions()
        self._canvas._pixmap = None
        self._canvas.update()
        self._update_status()
        self._update_button_states()

    def _on_file_selected(self, current: Optional[QListWidgetItem], _prev) -> None:
        if current is None:
            return
        self._save_current_batch_regions()

        path = current.data(Qt.ItemDataRole.UserRole)
        img = imread(path)
        if img is None:
            self._status.showMessage(f"无法读取: {path}")
            return

        self._cv_img = img
        self._original_img = img.copy()
        self._result_img = None
        self._file_path = path
        self._canvas.clear_regions()

        saved = self._batch_regions.get(path, [])
        self._canvas.set_regions(saved)
        self._canvas.set_cv_image(img)
        self._update_status()
        self._update_button_states()

    def _save_current_batch_regions(self) -> None:
        if self._file_path and self._mode == "batch":
            self._batch_regions[self._file_path] = self._canvas.get_regions_as_tuples()

    def _process_all(self) -> None:
        self._save_current_batch_regions()

        tasks = []
        for path in self._batch_files:
            regions = self._batch_regions.get(path, [])
            tasks.append((path, regions))

        if not tasks:
            QMessageBox.information(self, "提示", "没有待处理的文件。")
            return

        from src.ui.watermark_worker import WatermarkWorker

        mode = self._process_mode
        params = self._get_params()
        output_dir = self.settings.get("tools.watermark_output_dir", "nowm")

        self._batch_done_count = 0
        self._batch_fail_count = 0
        self._batch_skip_count = 0

        self._worker = WatermarkWorker(
            tasks=tasks,
            mode=mode,
            params=params,
            output_base=output_dir,
            use_flat_output=False,
        )

        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_batch_progress)
        self._worker.file_done.connect(self._on_batch_done)
        self._worker.file_skipped.connect(self._on_batch_skipped)
        self._worker.file_error.connect(self._on_batch_error)
        self._worker.finished.connect(self._on_batch_finished)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._on_thread_cleanup)

        self._update_button_states()
        self._worker_thread.start()

    def _cancel_batch(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self._status.showMessage("正在取消...")

    def _get_params(self) -> dict:
        return {
            "ksize": self._blur_spin.value() | 1,
            "radius": self._inpaint_spin.value(),
        }

    # ── Worker 回调 ──

    def _on_batch_progress(self, done: int, total: int) -> None:
        self._status.showMessage(f"处理中: {done}/{total}")

    def _on_batch_done(self, filename: str) -> None:
        self._batch_done_count += 1
        self._status.showMessage(f"完成: {filename}")

    def _on_batch_skipped(self, filename: str) -> None:
        self._batch_skip_count += 1
        self._status.showMessage(f"跳过（无选区）: {filename}")

    def _on_batch_error(self, filename: str, msg: str) -> None:
        self._batch_fail_count += 1
        self._status.showMessage(f"失败: {filename} — {msg}")

    def _on_batch_finished(self) -> None:
        msg = (
            f"批量处理完成 — 成功: {self._batch_done_count}, "
            f"失败: {self._batch_fail_count}, 跳过: {self._batch_skip_count}"
        )
        self._status.showMessage(msg, 10000)
        self._update_button_states()

    def _on_thread_cleanup(self) -> None:
        self._worker = None
        self._worker_thread = None
        self._update_button_states()

    # ── 生命周期 ──

    def closeEvent(self, event) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "确认关闭",
                "批量处理正在进行中，确定关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._worker is not None:
                self._worker.cancel()
            self._worker_thread.quit()
            if not self._worker_thread.wait(3000):
                self._worker_thread.terminate()
                self._worker_thread.wait(1000)
        event.accept()
