# -*- coding: utf-8 -*-
"""生成 ``assets/icon.ico``（品牌标记的多尺寸位图）。

形状的唯一来源是 ``flandora/branding.py``；``assets/icon.svg`` 是同一形状的矢量
版本，供面板 favicon 与文档使用，改形状时两处一起改（用例会比对两边的数字）。

    .venv\\Scripts\\python.exe tools\\make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flandora import branding  # noqa: E402  —— 上面那行负责让它 import 得到

ICON_PATH = ROOT / "assets" / "icon.ico"


def main() -> int:
    path = branding.save_ico(ICON_PATH)
    size = path.stat().st_size
    print(f"已写入 {path}（{size} 字节，尺寸 {', '.join(str(s) for s in branding.ICO_SIZES)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
