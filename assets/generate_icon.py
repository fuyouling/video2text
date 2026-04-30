from PIL import Image, ImageDraw, ImageFilter
import math
import random
import os

SIZE = 512
PADDING = 40
CORNER_RADIUS = 90


def create_rounded_rect(size, radius):
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def gradient_fill(size, color1, color2, angle=135):
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


def draw_rounded_line(draw, x1, y1, x2, y2, width, fill):
    draw.line([(x1, y1), (x2, y2)], fill=fill, width=width)
    r = width // 2
    draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=fill)
    draw.ellipse([x2 - r, y2 - r, x2 + r, y2 + r], fill=fill)


def draw_text_lines(draw, x, y, widths, line_height, color, thickness=10):
    for i, w in enumerate(widths):
        ly = y + i * line_height
        draw_rounded_line(draw, x, ly, x + w, ly, thickness, color)


def draw_curved_arrow(draw, x1, y1, x2, y2, color, width=5, head_size=16):
    cx = (x1 + x2) / 2
    cy = min(y1, y2) - 30
    points = []
    for i in range(50):
        t = i / 49.0
        px = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t**2 * x2
        py = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t**2 * y2
        points.append((px, py))

    for i in range(len(points) - 1):
        draw_rounded_line(
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


def create_icon():
    size = SIZE

    bg = gradient_fill(size, (30, 60, 180, 255), (80, 20, 140, 255), angle=135)

    mask = create_rounded_rect(size, CORNER_RADIUS)
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

    play_grad = gradient_fill(size, (255, 100, 80, 255), (255, 60, 120, 255), angle=90)
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
    draw_text_lines(draw, text_x, text_y, line_widths, 22, (60, 80, 140, 255), 7)

    draw_curved_arrow(
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

    border_outer = create_rounded_rect(size, CORNER_RADIUS)
    border_inner = create_rounded_rect(size - 4, CORNER_RADIUS - 2)
    border_inner_shifted = Image.new("L", (size, size), 0)
    border_inner_shifted.paste(border_inner, (2, 2))

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


def save_as_ico(img, ico_path):
    sizes = [16, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        resized = img.resize((s, s), Image.LANCZOS)
        images.append(resized.copy())
    img_256 = images[-1]
    img_256.save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )


if __name__ == "__main__":
    print("Generating video2text icon...")
    icon = create_icon()

    base = os.path.dirname(os.path.abspath(__file__))
    png_path = os.path.join(base, "video2text_logo.png")
    ico_path = os.path.join(base, "video2text_logo.ico")

    icon.save(png_path, "PNG")
    print(f"Saved PNG: {png_path}")

    save_as_ico(icon, ico_path)
    print(f"Saved ICO: {ico_path}")
    print("Done!")
