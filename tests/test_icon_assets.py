# -*- coding: utf-8 -*-
"""品牌图标：形状只有一个来源，三个 exe 与安装包都取它。

这里盯住三件事：
1. ``assets/icon.svg``（矢量源，面板 favicon 用它）与 ``flandora/branding.py``
   （栅格源，托盘与图标文件用它）的数字一致——改一边不改另一边会红；
2. 提交的 ``assets/icon.ico`` 与 ``branding.py`` 现在画出来的一模一样——
   形状改了却忘了重跑 ``tools/make_icon.py`` 会红；
3. 打包与安装脚本确实把图标接上了（三个 exe 的 ``--icon``、安装包的
   ``SetupIconFile``），不会打包打包着把图标漏掉。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from flandora import branding

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "icon.svg"
ICO = ROOT / "assets" / "icon.ico"


@pytest.fixture(scope="module")
def svg_text() -> str:
    return SVG.read_text(encoding="utf-8")


def _numbers(text: str, pattern: str) -> list[float]:
    return [float(value) for value in re.findall(pattern, text)]


def test_vector_and_raster_use_the_same_geometry(svg_text: str) -> None:
    view = re.search(r'viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"', svg_text)
    assert view is not None, "assets/icon.svg 里没有 viewBox"
    assert float(view.group(1)) == branding.CANVAS
    assert float(view.group(2)) == branding.CANVAS

    base = re.search(r'<rect width="40" height="40" rx="(\d+(?:\.\d+)?)"', svg_text)
    assert base is not None
    assert float(base.group(1)) == branding.CORNER_RADIUS

    rects = re.findall(
        r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" rx="([\d.]+)"',
        svg_text,
    )
    assert len(rects) == 2, f"气泡数量与 branding.py 对不上：svg 里 {len(rects)} 个"
    first = tuple(float(value) for value in rects[0])
    second = tuple(float(value) for value in rects[1])
    assert first == branding.BUBBLE
    assert second == branding.SECOND_BUBBLE

    paths = re.findall(
        r'<path d="M([\d.]+) ([\d.]+)L([\d.]+) ([\d.]+)L([\d.]+) ([\d.]+)Z"', svg_text
    )
    assert len(paths) == 2
    tail = tuple((float(paths[0][i]), float(paths[0][i + 1])) for i in range(0, 6, 2))
    second_tail = tuple((float(paths[1][i]), float(paths[1][i + 1])) for i in range(0, 6, 2))
    assert tail == branding.TAIL
    assert second_tail == branding.SECOND_TAIL

    assert _numbers(svg_text, r'opacity="([\d.]+)"') == [branding.SECOND_ALPHA]
    assert (
        f'fill="#{branding.BRAND_GREEN[0]:02X}{branding.BRAND_GREEN[1]:02X}{branding.BRAND_GREEN[2]:02X}"'
        in svg_text
    )


def test_committed_icon_matches_the_code(tmp_path: Path) -> None:
    assert ICO.is_file(), f"缺少 {ICO}：跑 python tools/make_icon.py 生成并提交"
    fresh = branding.save_ico(tmp_path / "icon.ico")
    assert fresh.read_bytes() == ICO.read_bytes(), (
        "assets/icon.ico 与 flandora/branding.py 对不上：跑 python tools/make_icon.py 重新生成并提交"
    )


def test_icon_carries_every_size() -> None:
    from PIL import Image

    with Image.open(ICO) as image:
        sizes = {size for size in image.info.get("sizes", set())}
    assert sizes == {(size, size) for size in branding.ICO_SIZES}


def test_brand_colours_differ_and_fill_the_canvas() -> None:
    green = branding.icon_image(branding.BRAND_GREEN, 32)
    grey = branding.icon_image(branding.BRAND_GREY, 32)

    def close(pixel: tuple[int, ...], colour: tuple[int, int, int, int]) -> bool:
        # 缩图时边缘会有一两级的过渡，实心区域的取值允许每通道差 2。
        return all(abs(pixel[i] - colour[i]) <= 2 for i in range(4))

    assert green.getpixel((6, 26)) != grey.getpixel((6, 26))
    assert close(green.getpixel((6, 26)), branding.BRAND_GREEN)
    assert close(grey.getpixel((6, 26)), branding.BRAND_GREY)
    assert green.getpixel((0, 0))[3] == 0, "圆角之外应当透明"
    assert green.getpixel((16, 16)) == branding.WHITE, "两个气泡的实心部分应当是白色"


def test_build_passes_the_icon_to_every_exe() -> None:
    text = (ROOT / "build.py").read_text(encoding="utf-8")
    assert '"--icon", str(ICON)' in text
    assert 'ICON = ROOT / "assets" / "icon.ico"' in text
    assert "icon_problems()" in text, "build.py --check 应当现场重画比对图标"


def test_installer_uses_the_same_icon() -> None:
    text = (ROOT / "installer" / "Flandora.iss").read_text(encoding="utf-8")
    assert "SetupIconFile=..\\assets\\icon.ico" in text


def test_icon_generation_is_reachable_from_the_repo() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    import make_icon

    assert make_icon.ICON_PATH == ICO
