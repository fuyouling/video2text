"""图标生成工具 —— 生成 video2text 应用图标（PNG + ICO）"""

import math
import os
import random
import sys
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.utils.logger import get_logger, setup_logger

setup_logger("generate_icon", log_to_console=True, log_to_file=False)
logger = get_logger("generate_icon")

# ============================================================
# 全局常量
# ============================================================

# 主图标（video2text_logo）画布尺寸
SIZE = 512
PADDING = 40
CORNER_RADIUS = 90

# 箭头/关闭符号图标画布尺寸
ARROW_CANVAS_SIZE = 512
ARROW_PADDING = 64
ARROW_FILL_COLOR = "#333333"
ARROW_STROKE_WIDTH = 48


# ============================================================
# 私有辅助函数 - 主图标
# ============================================================


def _create_rounded_rect_mask(size: int, radius: int) -> Image.Image:
    """创建圆角矩形遮罩。

    Args:
        size: 图像尺寸（正方形边长）
        radius: 圆角半径

    Returns:
        灰度遮罩图像
    """
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _gradient_fill(
    size: int,
    color1: Tuple[int, int, int, int],
    color2: Tuple[int, int, int, int],
    angle: int = 135,
) -> Image.Image:
    """创建线性渐变填充图像。

    Args:
        size: 图像尺寸
        color1: 起始颜色 (R, G, B, A)
        color2: 结束颜色 (R, G, B, A)
        angle: 渐变角度（度）

    Returns:
        带透明度的渐变图像
    """
    img = Image.new("RGBA", (size, size))
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    max_proj = size * math.sqrt(2) / 2
    half = size / 2
    pixels = img.load()
    for y in range(size):
        for x in range(size):
            proj = (x - half) * cos_a + (y - half) * sin_a
            t = max(0, min(1, (proj + max_proj) / (2 * max_proj)))
            r = int(color1[0] + (color2[0] - color1[0]) * t)
            g = int(color1[1] + (color2[1] - color1[1]) * t)
            b = int(color1[2] + (color2[2] - color1[2]) * t)
            pixels[x, y] = (r, g, b, 255)
    return img


def _draw_rounded_line(
    draw: ImageDraw.ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int,
    fill: Tuple[int, int, int, int],
) -> None:
    """绘制带圆端点的线段。"""
    draw.line([(x1, y1), (x2, y2)], fill=fill, width=width)
    r = width // 2
    draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=fill)
    draw.ellipse([x2 - r, y2 - r, x2 + r, y2 + r], fill=fill)


def _draw_text_lines(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    widths: list,
    line_height: int,
    color: Tuple[int, int, int, int],
    thickness: int = 10,
) -> None:
    """绘制多行文本模拟线条。"""
    for i, w in enumerate(widths):
        ly = y + i * line_height
        _draw_rounded_line(draw, x, ly, x + w, ly, thickness, color)


def _draw_curved_arrow(
    draw: ImageDraw.ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: Tuple[int, int, int, int],
    width: int = 5,
    head_size: int = 16,
) -> None:
    """绘制带箭头的二次贝塞尔曲线。"""
    cx = (x1 + x2) / 2
    cy = min(y1, y2) - 30
    points = []
    for i in range(50):
        t = i / 49.0
        px = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t**2 * x2
        py = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t**2 * y2
        points.append((px, py))

    for i in range(len(points) - 1):
        _draw_rounded_line(
            draw,
            int(points[i][0]),
            int(points[i][1]),
            int(points[i + 1][0]),
            int(points[i + 1][1]),
            width,
            color,
        )

    tip_x, tip_y = points[-1]
    prev_x, prev_y = points[-2]
    angle = math.atan2(tip_y - prev_y, tip_x - prev_x)
    p1 = (
        tip_x - head_size * math.cos(angle - 0.4),
        tip_y - head_size * math.sin(angle - 0.4),
    )
    p2 = (
        tip_x - head_size * math.cos(angle + 0.4),
        tip_y - head_size * math.sin(angle + 0.4),
    )
    draw.polygon([(tip_x, tip_y), p1, p2], fill=color)


# ============================================================
# 私有辅助函数 - 箭头图标
# ============================================================


def _new_arrow_canvas() -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    """创建箭头图标用的透明画布。"""
    img = Image.new("RGBA", (ARROW_CANVAS_SIZE, ARROW_CANVAS_SIZE), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


# ============================================================
# 主图标生成
# ============================================================


def create_icon() -> Image.Image:
    """生成 video2text 应用图标。

    布局包含：
    1. 圆角矩形背景 + 渐变
    2. 左侧：屏幕框 + 音频波形条
    3. 右上：播放按钮三角形
    4. 右下：文档框 + 折角 + 文本行
    5. 弯曲箭头连接文档到播放按钮
    6. 装饰性光点
    7. 半透明边框

    Returns:
        512x512 RGBA 图标图像
    """
    size = SIZE

    bg = _gradient_fill(size, (30, 60, 180, 255), (80, 20, 140, 255), angle=135)

    mask = _create_rounded_rect_mask(size, CORNER_RADIUS)
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(bg, mask=mask)
    draw = ImageDraw.Draw(icon)

    P = PADDING

    screen_x = P + 30
    screen_y = P + 60
    screen_w = 210
    screen_h = 170
    screen_r = 18

    draw.rounded_rectangle(
        [screen_x + 6, screen_y + 6, screen_x + screen_w + 6, screen_y + screen_h + 6],
        radius=screen_r,
        fill=(0, 0, 0, 60),
    )
    draw.rounded_rectangle(
        [screen_x, screen_y, screen_x + screen_w, screen_y + screen_h],
        radius=screen_r,
        fill=(20, 20, 40, 220),
    )

    inner_x = screen_x + 15
    inner_y = screen_y + 15
    inner_w = screen_w - 30
    inner_h = screen_h - 30

    wave_color = (100, 200, 255, 200)
    num_bars = 35
    bar_w = 3
    bar_gap = (inner_w - num_bars * bar_w) / (num_bars - 1)
    random.seed(42)
    for i in range(num_bars):
        bx = inner_x + i * (bar_w + bar_gap)
        dist = abs(i - num_bars / 2) / (num_bars / 2)
        amp = inner_h * 0.35 * (1 - dist * 0.7)
        h = amp * (0.4 + 0.6 * random.random())
        by1 = inner_y + inner_h / 2 - h / 2
        by2 = inner_y + inner_h / 2 + h / 2
        draw.rounded_rectangle([bx, by1, bx + bar_w, by2], radius=1, fill=wave_color)

    play_x = screen_x + screen_w + 35
    play_y = screen_y + 20
    play_size = 100

    tri_pts = [
        (play_x, play_y),
        (play_x + play_size, play_y + play_size // 2),
        (play_x, play_y + play_size),
    ]
    shadow_pts = [(p[0] + 4, p[1] + 4) for p in tri_pts]

    shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_draw.polygon(shadow_pts, fill=(0, 0, 0, 80))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(8))
    icon = Image.alpha_composite(icon, shadow_layer)
    draw = ImageDraw.Draw(icon)

    play_grad = _gradient_fill(size, (255, 100, 80, 255), (255, 60, 120, 255), angle=90)
    play_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(play_mask).polygon(tri_pts, fill=255)
    play_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    play_layer.paste(play_grad, mask=play_mask)
    icon = Image.alpha_composite(icon, play_layer)
    draw = ImageDraw.Draw(icon)

    arrow_cx = play_x + play_size // 2 - 4
    arrow_cy = play_y + play_size // 2
    as2 = 28
    draw.polygon(
        [
            (arrow_cx - as2 // 2, arrow_cy - as2 // 2),
            (arrow_cx + as2 // 2, arrow_cy),
            (arrow_cx - as2 // 2, arrow_cy + as2 // 2),
        ],
        fill=(255, 255, 255, 230),
    )

    doc_x = P + 50
    doc_y = P + 270
    doc_w = 180
    doc_h = 160
    doc_r = 12

    draw.rounded_rectangle(
        [doc_x + 5, doc_y + 5, doc_x + doc_w + 5, doc_y + doc_h + 5],
        radius=doc_r,
        fill=(0, 0, 0, 60),
    )
    draw.rounded_rectangle(
        [doc_x, doc_y, doc_x + doc_w, doc_y + doc_h],
        radius=doc_r,
        fill=(255, 255, 255, 240),
    )

    fold = 28
    draw.polygon(
        [
            (doc_x + doc_w - fold, doc_y),
            (doc_x + doc_w, doc_y + fold),
            (doc_x + doc_w - fold, doc_y + fold),
        ],
        fill=(200, 210, 230, 255),
    )
    draw.line(
        [(doc_x + doc_w - fold, doc_y), (doc_x + doc_w - fold, doc_y + fold)],
        fill=(180, 190, 210, 255),
        width=1,
    )
    draw.line(
        [(doc_x + doc_w - fold, doc_y + fold), (doc_x + doc_w, doc_y + fold)],
        fill=(180, 190, 210, 255),
        width=1,
    )

    text_x = doc_x + 22
    text_y = doc_y + 28
    line_widths = [120, 100, 135, 90, 110, 70]
    _draw_text_lines(draw, text_x, text_y, line_widths, 22, (60, 80, 140, 255), 7)

    _draw_curved_arrow(
        draw,
        doc_x + doc_w + 15,
        doc_y + doc_h // 2 - 25,
        play_x - 15,
        screen_y + screen_h // 2 + 40,
        (255, 200, 60, 200),
        width=5,
        head_size=16,
    )

    gx, gy = play_x + play_size + 10, play_y - 15
    draw.ellipse([gx, gy, gx + 16, gy + 16], fill=(100, 220, 255, 150))
    draw.ellipse([gx + 50, gy + 30, gx + 55, gy + 35], fill=(100, 220, 255, 100))
    draw.ellipse([gx + 25, gy - 20, gx + 31, gy - 14], fill=(255, 200, 100, 120))

    border_only = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    border_draw = ImageDraw.Draw(border_only)
    border_draw.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=CORNER_RADIUS,
        outline=(255, 255, 255, 40),
        width=2,
    )
    icon = Image.alpha_composite(icon, border_only)

    return icon


def save_as_ico(img: Image.Image, ico_path: Path) -> None:
    """将图像保存为多尺寸 ICO 文件。

    Args:
        img: 源图像
        ico_path: 输出 ICO 文件路径
    """
    sizes = [16, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        resized = img.resize((s, s), Image.LANCZOS)
        images.append(resized.copy())
    img_256 = images[-1]
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    img_256.save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )


# ============================================================
# 箭头图标生成
# ============================================================


def gen_arrow_down(output_dir: Optional[Path] = None) -> Path:
    """生成向下箭头 PNG。

    Args:
        output_dir: 输出目录，默认为项目 assets/ 目录

    Returns:
        生成的文件路径
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_arrow_canvas()
    base_length = ARROW_CANVAS_SIZE - 2 * ARROW_PADDING
    triangle_height = base_length * math.sqrt(3) / 2
    draw.polygon(
        [
            (ARROW_PADDING, ARROW_PADDING),
            (ARROW_CANVAS_SIZE - ARROW_PADDING, ARROW_PADDING),
            (ARROW_CANVAS_SIZE // 2, ARROW_PADDING + int(triangle_height)),
        ],
        fill=ARROW_FILL_COLOR,
    )
    path = output_dir / "arrow_down.png"
    img.save(path)
    logger.info("已生成向下箭头: %s", path)
    return path


def gen_arrow_up(output_dir: Optional[Path] = None) -> Path:
    """生成向上箭头 PNG。

    Args:
        output_dir: 输出目录，默认为项目 assets/ 目录

    Returns:
        生成的文件路径
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_arrow_canvas()
    base_length = ARROW_CANVAS_SIZE - 2 * ARROW_PADDING
    triangle_height = base_length * math.sqrt(3) / 2
    draw.polygon(
        [
            (ARROW_PADDING, ARROW_CANVAS_SIZE - ARROW_PADDING),
            (ARROW_CANVAS_SIZE - ARROW_PADDING, ARROW_CANVAS_SIZE - ARROW_PADDING),
            (ARROW_CANVAS_SIZE // 2, ARROW_CANVAS_SIZE - ARROW_PADDING - int(triangle_height)),
        ],
        fill=ARROW_FILL_COLOR,
    )
    path = output_dir / "arrow_up.png"
    img.save(path)
    logger.info("已生成向上箭头: %s", path)
    return path


def gen_tree_closed(output_dir: Optional[Path] = None) -> Path:
    """生成树形控件折叠状态（右向三角）图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = (90, 107, 123, 255)
    draw.polygon([(170, 120), (170, 392), (370, 256)], fill=c)
    path = output_dir / "tree_closed.png"
    img.save(path)
    logger.info("已生成树形折叠图标: %s", path)
    return path


def gen_tree_open(output_dir: Optional[Path] = None) -> Path:
    """生成树形控件展开状态（向下三角）图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = (90, 107, 123, 255)
    draw.polygon([(120, 170), (392, 170), (256, 370)], fill=c)
    path = output_dir / "tree_open.png"
    img.save(path)
    logger.info("已生成树形展开图标: %s", path)
    return path


def gen_check(output_dir: Optional[Path] = None) -> Path:
    """生成复选框勾选标记（白色对勾）图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    w = 56
    draw.line([(130, 270), (230, 380), (400, 140)], fill=(255, 255, 255, 255), width=w)
    path = output_dir / "check.png"
    img.save(path)
    logger.info("已生成勾选标记图标: %s", path)
    return path


def gen_close(output_dir: Optional[Path] = None) -> Path:
    """生成关闭符号 PNG。

    Args:
        output_dir: 输出目录，默认为项目 assets/ 目录

    Returns:
        生成的文件路径
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_arrow_canvas()
    inset = ARROW_PADDING + ARROW_STROKE_WIDTH // 2
    end = ARROW_CANVAS_SIZE - inset
    draw.line([(inset, inset), (end, end)], fill=ARROW_FILL_COLOR, width=ARROW_STROKE_WIDTH)
    draw.line([(end, inset), (inset, end)], fill=ARROW_FILL_COLOR, width=ARROW_STROKE_WIDTH)
    path = output_dir / "close.png"
    img.save(path)
    logger.info("已生成关闭符号: %s", path)
    return path


def gen_refresh(output_dir: Optional[Path] = None) -> Path:
    """生成「重新测试」刷新图标 PNG（环形箭头）。

    用于 Nvidia API 响应测试对话框中每个模型卡片上的重新测试按钮。

    Args:
        output_dir: 输出目录，默认为项目 assets/ 目录

    Returns:
        生成的文件路径
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import math as _m

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (120, 170, 255, 255)  # 与主题蓝一致

    cx = cy = size // 2
    r = 150
    bbox = [cx - r, cy - r, cx + r, cy + r]
    width = 56
    # 环形箭头主体（约 280° 弧，顺时针）
    draw.arc(bbox, start=50, end=330, fill=color, width=width)

    # 箭头头部：位于弧末端（angle=330°，顺时针切线方向）
    end_angle = _m.radians(330)
    bx = cx + r * _m.cos(end_angle)
    by = cy + r * _m.sin(end_angle)
    dx = -_m.sin(end_angle)  # 顺时针切线方向
    dy = _m.cos(end_angle)
    rx = _m.cos(end_angle)   # 径向方向
    ry = _m.sin(end_angle)
    head_len = 96
    hw = 46
    tip = (bx + dx * head_len, by + dy * head_len)
    c1 = (bx - dx * hw + rx * hw, by - dy * hw + ry * hw)
    c2 = (bx - dx * hw - rx * hw, by - dy * hw - ry * hw)
    draw.polygon([tip, c1, c2], fill=color)

    path = output_dir / "refresh.png"
    img.save(path)
    logger.info("已生成刷新图标: %s", path)
    return path


# ============================================================
# 菜单图标生成（设置 / 工具 / 帮助 等）
# ============================================================

_MENU_BLUE = (120, 170, 255, 255)
_MENU_BLUE_DARK = (56, 118, 224, 255)
_MENU_GRAY = (90, 107, 123, 255)


def _new_canvas(size: int = 512) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    """创建透明画布（菜单图标统一画布）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _star_points(cx: int, cy: int, outer: int, inner: int, n: int = 5) -> list:
    """生成 n 角星的顶点列表。"""
    pts = []
    for i in range(n * 2):
        r = outer if i % 2 == 0 else inner
        ang = -math.pi / 2 + i * math.pi / n
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def gen_settings(output_dir: Optional[Path] = None) -> Path:
    """生成「设置」齿轮图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    cx = cy = 256
    R = 180
    teeth = 8
    r = R * 0.74
    step = math.pi / teeth
    ang0 = -math.pi / 2
    pts = []
    for i in range(teeth * 2):
        rad = R if i % 2 == 0 else r
        ang = ang0 + i * step
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=_MENU_BLUE)
    hole = R * 0.36
    draw.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=(0, 0, 0, 0))
    path = output_dir / "settings.png"
    img.save(path)
    logger.info("已生成设置图标: %s", path)
    return path


def gen_tools(output_dir: Optional[Path] = None) -> Path:
    """生成「工具」扳手图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    # 手柄
    _draw_rounded_line(draw, 150, 370, 330, 190, 66, _MENU_BLUE)
    # 头部圆环
    hx, hy, hr = 330, 190, 104
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=_MENU_BLUE)
    draw.ellipse([hx - hr * 0.52, hy - hr * 0.52, hx + hr * 0.52, hy + hr * 0.52],
                 fill=(0, 0, 0, 0))
    # 开口（楔形擦除）
    draw.polygon([(hx, hy), (hx + hr, hy - hr), (hx + hr, hy + hr)],
                 fill=(0, 0, 0, 0))
    path = output_dir / "tools.png"
    img.save(path)
    logger.info("已生成工具图标: %s", path)
    return path


def gen_help(output_dir: Optional[Path] = None) -> Path:
    """生成「帮助」问号图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    # 顶部弧线
    draw.arc([166, 120, 346, 320], start=200, end=340, fill=_MENU_BLUE, width=46)
    # 竖线
    draw.line([(256, 318), (256, 372)], fill=_MENU_BLUE, width=46)
    # 点
    draw.ellipse([238, 396, 274, 432], fill=_MENU_BLUE)
    path = output_dir / "help.png"
    img.save(path)
    logger.info("已生成帮助图标: %s", path)
    return path


def gen_edit_config(output_dir: Optional[Path] = None) -> Path:
    """生成「编辑配置」滑块图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    ys = [170, 256, 342]
    xs = [300, 180, 340]
    for y in ys:
        draw.line([(110, y), (402, y)], fill=_MENU_GRAY, width=26)
    for y, x in zip(ys, xs):
        draw.ellipse([x - 46, y - 46, x + 46, y + 46], fill=_MENU_BLUE)
    path = output_dir / "edit_config.png"
    img.save(path)
    logger.info("已生成编辑配置图标: %s", path)
    return path


def gen_bg_image(output_dir: Optional[Path] = None) -> Path:
    """生成「背景图片」图片框图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    draw.rounded_rectangle([110, 120, 402, 392], radius=40,
                           outline=_MENU_BLUE, width=26)
    draw.ellipse([150, 160, 214, 224], fill=_MENU_BLUE)
    draw.polygon([(110, 392), (224, 252), (300, 332), (360, 272), (402, 392)],
                 fill=_MENU_BLUE)
    path = output_dir / "bg_image.png"
    img.save(path)
    logger.info("已生成背景图片图标: %s", path)
    return path


def gen_favorite(output_dir: Optional[Path] = None) -> Path:
    """生成「常用目录」星形图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    pts = _star_points(256, 262, 190, 76)
    draw.polygon(pts, fill=_MENU_BLUE)
    path = output_dir / "favorite.png"
    img.save(path)
    logger.info("已生成常用目录图标: %s", path)
    return path


def gen_api_key(output_dir: Optional[Path] = None) -> Path:
    """生成「API Key 管理」钥匙图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    hx, hy, hr = 168, 256, 92
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=_MENU_BLUE)
    draw.ellipse([hx - hr * 0.42, hy - hr * 0.42, hx + hr * 0.42, hy + hr * 0.42],
                 fill=(0, 0, 0, 0))
    draw.line([(250, 256), (404, 256)], fill=_MENU_BLUE, width=46)
    draw.line([(340, 256), (340, 322)], fill=_MENU_BLUE, width=30)
    draw.line([(382, 256), (382, 322)], fill=_MENU_BLUE, width=30)
    path = output_dir / "api_key.png"
    img.save(path)
    logger.info("已生成 API Key 图标: %s", path)
    return path


def gen_voice(output_dir: Optional[Path] = None) -> Path:
    """生成「语音转文字」麦克风图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    draw.rounded_rectangle([196, 120, 316, 300], radius=60, fill=_MENU_BLUE)
    draw.line([(256, 300), (256, 384)], fill=_MENU_BLUE, width=30)
    draw.arc([150, 300, 362, 512], start=180, end=360, fill=_MENU_BLUE, width=40)
    draw.line([(170, 470), (342, 470)], fill=_MENU_BLUE, width=40)
    path = output_dir / "voice.png"
    img.save(path)
    logger.info("已生成语音转文字图标: %s", path)
    return path


def gen_api_test(output_dir: Optional[Path] = None) -> Path:
    """生成「Nvidia API 测试」烧瓶图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    draw.rectangle([236, 108, 276, 162], fill=_MENU_BLUE)
    draw.polygon([(236, 162), (276, 162), (360, 392), (152, 392)],
                 fill=_MENU_BLUE)
    draw.polygon([(196, 300), (316, 300), (360, 392), (152, 392)],
                 fill=_MENU_BLUE_DARK)
    draw.ellipse([214, 326, 240, 352], fill=(255, 255, 255, 200))
    draw.ellipse([286, 344, 306, 364], fill=(255, 255, 255, 200))
    path = output_dir / "api_test.png"
    img.save(path)
    logger.info("已生成 API 测试图标: %s", path)
    return path


def gen_about(output_dir: Optional[Path] = None) -> Path:
    """生成「关于」信息圆圈图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    draw.ellipse([110, 110, 402, 402], outline=_MENU_BLUE, width=26)
    draw.ellipse([238, 372, 274, 408], fill=_MENU_BLUE)
    draw.line([(256, 150), (256, 344)], fill=_MENU_BLUE, width=46)
    path = output_dir / "about.png"
    img.save(path)
    logger.info("已生成关于图标: %s", path)
    return path


def gen_arrow_right(output_dir: Optional[Path] = None) -> Path:
    """生成子菜单右向箭头图标 PNG。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img, draw = _new_canvas()
    draw.polygon([(190, 150), (190, 362), (382, 256)], fill=_MENU_BLUE)
    path = output_dir / "arrow_right.png"
    img.save(path)
    logger.info("已生成右向箭头图标: %s", path)
    return path


def gen_donate(output_dir: Optional[Path] = None) -> Path:
    """生成「捐赠」爱心图标 PNG。

    用于帮助菜单中的捐赠入口（独立于 donate.png，后者为对话框内的二维码/图片）。

    Args:
        output_dir: 输出目录，默认为项目 assets/ 目录

    Returns:
        生成的文件路径
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = _MENU_BLUE

    cx = 256
    top_y = 150
    lobe = 96
    pts = []
    steps = 200
    for i in range(steps + 1):
        t = i / steps
        if t < 0.5:
            # 左半：从顶部凹陷到左叶底再到底部尖
            u = t * 2
            x = cx - 150 * math.sin(math.pi * u)
            y = top_y + (362 - top_y) * (1 - math.cos(math.pi * u)) / 2
        else:
            u = (t - 0.5) * 2
            x = cx + 150 * math.sin(math.pi * u)
            y = top_y + (362 - top_y) * (1 + math.cos(math.pi * u)) / 2
        pts.append((x, y))
    draw.polygon(pts, fill=color)

    # 中心高光（与菜单图标一致的镂空白边风格）
    hl_pts = []
    hl_cx = 256
    for i in range(steps + 1):
        t = i / steps
        if t < 0.5:
            u = t * 2
            x = hl_cx - 110 * math.sin(math.pi * u)
            y = top_y + 18 + (320 - top_y - 18) * (1 - math.cos(math.pi * u)) / 2
        else:
            u = (t - 0.5) * 2
            x = hl_cx + 110 * math.sin(math.pi * u)
            y = top_y + 18 + (320 - top_y - 18) * (1 + math.cos(math.pi * u)) / 2
        hl_pts.append((x, y))
    draw.polygon(hl_pts, fill=(0, 0, 0, 0))

    path = output_dir / "heart.png"
    img.save(path)
    logger.info("已生成捐赠图标: %s", path)
    return path


# ============================================================
# 公共 API
# ============================================================


def generate_icon_files(output_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """生成主图标文件并保存到指定目录。

    Args:
        output_dir: 输出目录，默认为项目 assets/ 目录

    Returns:
        (png_path, ico_path) 元组
    """
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "assets"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    png_path = output_dir / "video2text_logo.png"
    ico_path = output_dir / "video2text_logo.ico"

    logger.info("生成 video2text 图标...")
    icon = create_icon()

    icon.save(png_path, "PNG")
    logger.info("已生主图标 PNG: %s", png_path)

    save_as_ico(icon, ico_path)
    logger.info("已生主图标 ICO: %s", ico_path)

    return png_path, ico_path


# ============================================================
# CLI 入口
# ============================================================


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="图标生成工具")
    parser.add_argument("--arrows", action="store_true", help="仅生成箭头/关闭符号图标")
    parser.add_argument("--widgets", action="store_true", help="仅生成控件图标（树形折叠/展开、勾选标记、刷新）")
    parser.add_argument("--main", action="store_true", help="仅生成主图标")
    parser.add_argument(
        "--menu",
        action="store_true",
        help="仅生成菜单图标（设置/工具/帮助及其子项）",
    )
    parser.add_argument("--all", action="store_true", help="生成所有图标（默认行为）")
    args = parser.parse_args()

    if not args.arrows and not args.main and not args.widgets and not args.menu:
        generate_icon_files()
        gen_arrow_down()
        gen_arrow_up()
        gen_close()
        gen_tree_closed()
        gen_tree_open()
        gen_check()
        gen_refresh()
        gen_settings()
        gen_tools()
        gen_help()
        gen_edit_config()
        gen_bg_image()
        gen_favorite()
        gen_api_key()
        gen_voice()
        gen_api_test()
        gen_about()
        gen_arrow_right()
        gen_donate()

    else:
        if args.main or args.all:
            generate_icon_files()
        if args.arrows or args.all:
            gen_arrow_down()
            gen_arrow_up()
            gen_close()
        if args.widgets or args.all:
            gen_tree_closed()
            gen_tree_open()
            gen_check()
            gen_refresh()
        if args.menu or args.all:
            gen_settings()
            gen_tools()
            gen_help()
            gen_edit_config()
            gen_bg_image()
            gen_favorite()
            gen_api_key()
            gen_voice()
            gen_api_test()
            gen_about()
            gen_arrow_right()
            gen_donate()