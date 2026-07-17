import math
from PIL import Image, ImageDraw

# ========== 可自定义参数 ==========
CANVAS_SIZE = 512    # 画布正方形边长（像素）
PADDING = 64         # 图标距离画布边缘的内边距
FILL_COLOR = "#333333"  # 三角形填充色（深灰色，匹配示例第一个）
BG_TRANSPARENT = True   # 是否使用透明背景
# ==================================

# 创建画布
if BG_TRANSPARENT:
    img = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
else:
    img = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), "white")

draw = ImageDraw.Draw(img)

# 计算等边倒三角的三个顶点坐标（底边在上，顶点在下，水平居中）
base_length = CANVAS_SIZE - 2 * PADDING
triangle_height = base_length * math.sqrt(3) / 2  # 等边三角形高度公式

point_top_left = (PADDING, PADDING)
point_top_right = (CANVAS_SIZE - PADDING, PADDING)
point_bottom = (CANVAS_SIZE // 2, PADDING + triangle_height)

# 绘制实心三角形
draw.polygon(
    [point_top_left, point_top_right, point_bottom],
    fill=FILL_COLOR
)

# 保存文件
output_name = "inverted_triangle.png"
img.save(output_name)
print(f"倒三角图标已生成，保存为：{output_name}")

# 如需直接预览，取消下面注释
# img.show()