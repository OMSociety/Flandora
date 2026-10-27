# -*- coding: utf-8 -*-
"""产品标记：几何与栅格化只此一处。

托盘图标、可执行文件与安装包的图标都取自这里；形状与 ``assets/icon.svg`` 一致
（40 单位画布、圆角方底、两个白色气泡）。改了这里的数字或色值，重新生成图标并
提交，否则 exe 与安装包会停在旧样子：

    .venv\\Scripts\\python.exe tools\\make_icon.py

``assets/icon.svg`` 是同一形状的矢量版（面板 favicon 用它），两边的数字由
``tests/test_icon_assets.py`` 对表；``build.py --check`` 也会现场重画比一遍。

底色是唯一随状态变化的部分：托盘用绿表示运行中、灰表示已停止。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

CANVAS = 40.0
CORNER_RADIUS = 12.0
BUBBLE = (8.25, 10.75, 15.5, 11.5, 5.2)
TAIL = ((13.45, 22.25), (10.25, 25.35), (10.25, 21.15))
SECOND_BUBBLE = (16.75, 14.75, 15.0, 11.5, 5.2)
SECOND_TAIL = ((26.55, 26.25), (28.75, 29.25), (28.75, 25.75))
SECOND_ALPHA = 0.72

BRAND_GREEN = (7, 193, 96, 255)
BRAND_GREY = (150, 150, 150, 255)
WHITE = (255, 255, 255, 255)

# ICO 里带的尺寸：从任务栏到资源管理器大图标都覆盖。
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

_SUPERSAMPLE = 4


def _bubble(
    draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float, float], unit: float
) -> None:
    x, y, w, h, radius = box
    draw.rounded_rectangle(
        (round(x * unit), round(y * unit), round((x + w) * unit), round((y + h) * unit)),
        radius=max(1, round(radius * unit)),
        fill=WHITE,
    )


def _tail(draw: ImageDraw.ImageDraw, points: tuple[tuple[float, float], ...], unit: float) -> None:
    draw.polygon([(round(x * unit), round(y * unit)) for x, y in points], fill=WHITE)


def icon_image(colour: tuple[int, int, int, int] = BRAND_GREEN, size: int = 64) -> Image.Image:
    """画一枚方形标记。``size`` 是边长（像素），返回 RGBA 图。

    先在放大尺寸上画再缩回来：PIL 画圆角没有抗锯齿，直接按最终尺寸画会有毛边。
    """
    size = max(1, int(size))
    scale = max(size, 64) * _SUPERSAMPLE
    unit = scale / CANVAS
    img = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, scale - 1, scale - 1), radius=round(CORNER_RADIUS * unit), fill=colour
    )
    _bubble(draw, BUBBLE, unit)
    _tail(draw, TAIL, unit)

    overlay = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    _bubble(odraw, SECOND_BUBBLE, unit)
    _tail(odraw, SECOND_TAIL, unit)
    alpha = overlay.getchannel("A").point(lambda a: int(a * SECOND_ALPHA))
    overlay.putalpha(alpha)
    img.alpha_composite(overlay)

    if scale != size:
        img = img.resize((size, size), Image.LANCZOS)
    return img


def save_ico(path: str | Path, colour: tuple[int, int, int, int] = BRAND_GREEN) -> Path:
    """写多尺寸 ``.ico``，供可执行文件与安装包使用。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    largest = max(ICO_SIZES)
    icon_image(colour, largest).save(target, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    return target
