"""QComboBox 统一辅助配置模块 —— 规范所有下拉框的尺寸、项高度与悬停高亮。

功能特性：
1. 统一绑定 QStyledItemDelegate，确保 Windows 原生平台下 hover 与 selected 伪类完美触发。
2. 统一设置 maxVisibleItems(7)，避免选项过多时跌出屏幕被截断，并自动启用平滑滚动。
3. 动态计算或约束下拉框宽度，防止在 QFormLayout 中被拉伸至 800px+ 巨无霸。
4. 针对内容超长的下拉项，重写或适配 showPopup 自适应弹出层宽度，保证文本完整显示不被截断。
"""

from typing import Optional
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate


class CompactComboDelegate(QStyledItemDelegate):
    """紧凑行高委托，确保行高稳定在 26px，不随 DPI 或外部 QSS 异常膨胀。"""

    def __init__(self, row_height: int = 26, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._row_height = row_height

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(self._row_height)
        return size


class AutoWidthPopupFilter(QObject):
    """弹出列表宽度自适应过滤器。

    当弹出列表选项文字长于下拉框本体宽度时，在展开前自动将视图 minimumWidth
    扩展至适合长文本的尺寸，避免文字被截断。
    """

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress):
            if isinstance(obj, QComboBox) and obj.count() > 0:
                view = obj.view()
                if view is not None:
                    hint = view.sizeHintForColumn(0)
                    sb_w = (
                        view.verticalScrollBar().sizeHint().width()
                        if obj.count() > obj.maxVisibleItems()
                        else 0
                    )
                    # 适当加 24px padding + scrollbar 空间
                    needed_w = max(obj.width(), hint + sb_w + 24)
                    view.setMinimumWidth(needed_w)
        return super().eventFilter(obj, event)


def setup_combo(
    combo: QComboBox,
    max_width: Optional[int] = None,
    max_visible_items: int = 7,
    row_height: int = 26,
    auto_width_popup: bool = True,
) -> QComboBox:
    """为 QComboBox 应用统一标准配置：委托、可见项数、最大宽度与弹出层自适应。

    Args:
        combo: 需要配置的 QComboBox 实例。
        max_width: 可选的最大宽度约束（如布尔项 140px，一般配置 280px）。
        max_visible_items: 展开列表最大可见项数，默认 7 项。
        row_height: 单项行高，默认 26px。
        auto_width_popup: 是否在文本过长时自适应拓宽弹出菜单宽度。

    Returns:
        传入的 QComboBox 实例（便于链式调用）。
    """
    combo.setItemDelegate(CompactComboDelegate(row_height, combo))
    combo.setMaxVisibleItems(max_visible_items)
    if max_width is not None and max_width > 0:
        combo.setMaximumWidth(max_width)

    if auto_width_popup:
        filt = AutoWidthPopupFilter(combo)
        combo.installEventFilter(filt)

        # 同时也劫持 showPopup 以支持代码调用 showPopup() 时的宽度适配
        orig_show_popup = combo.showPopup

        def _custom_show_popup():
            view = combo.view()
            if view is not None and combo.count() > 0:
                hint = view.sizeHintForColumn(0)
                sb_w = (
                    view.verticalScrollBar().sizeHint().width()
                    if combo.count() > combo.maxVisibleItems()
                    else 0
                )
                needed_w = max(combo.width(), hint + sb_w + 24)
                view.setMinimumWidth(needed_w)
            orig_show_popup()

        combo.showPopup = _custom_show_popup

    return combo

