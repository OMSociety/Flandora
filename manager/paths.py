# -*- coding: utf-8 -*-
"""管理器要知道的路径、端口与解释器。

这里只回答"东西在哪儿"，不掺行为：进程启停在 bridge.py，托盘在 tray.py。
开机自启不归管理器管 —— 那个开关在网页面板里，实现在 flandora/autostart.py。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

DEFAULT_OB_PORT = 7999
DEFAULT_WEB_PORT = 4950

IS_WINDOWS = sys.platform == "win32"


def install_dir() -> Path:
    """安装根目录：打包后是 exe 所在目录，源码形态是仓库根。

    onefile 打包把代码解到临时目录，``__file__`` 指向那里面，不能用来定位桥接与
    ``data/``；冻结形态一律问 ``sys.executable``。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return HERE.parent


BRIDGE_DIR = install_dir()
DATA_DIR = BRIDGE_DIR / "data"
BRIDGE_LOG = DATA_DIR / "flandora.log"
CONFIG_FILE = DATA_DIR / "config.json"


def bridge_exe() -> Path:
    """打包后的桥接主程序（与 data/ 同级）。"""
    name = "Flandora-Bridge.exe" if IS_WINDOWS else "Flandora-Bridge"
    return BRIDGE_DIR / name


def bridge_main() -> Path:
    """桥接入口：有打包好的 exe 就用它，源码形态跑仓库根的 run_bridge.py。"""
    exe = bridge_exe()
    return exe if exe.exists() else (BRIDGE_DIR / "run_bridge.py")


def _port_from(raw: dict, key: str, fallback: int) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value if 1 <= value <= 65535 else fallback


def read_config_ports() -> tuple[int, int]:
    """读桥接的 data/config.json，返回 (OneBot 端口, 面板端口)。

    文件不存在、不是 JSON、值不合法都退回默认值：校验归桥接自己
    （flandora/config/fields.py），管理器只按同一份文件走。
    """
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  —— 读不到就按默认端口
        return DEFAULT_OB_PORT, DEFAULT_WEB_PORT
    if not isinstance(raw, dict):
        return DEFAULT_OB_PORT, DEFAULT_WEB_PORT
    return (
        _port_from(raw, "ob_server_port", DEFAULT_OB_PORT),
        _port_from(raw, "web_port", DEFAULT_WEB_PORT),
    )


def python_for_scripts() -> Path:
    """跑 run_bridge.py 用的解释器：管理器自己跑在哪个解释器上就用哪个。

    优先同级目录的 pythonw.exe（源码形态下不会弹控制台窗口）。
    """
    exe = Path(sys.executable)
    if IS_WINDOWS:
        windowed = exe.with_name("pythonw.exe")
        if windowed.exists():
            return windowed
    return exe


def ensure_bridge_importable() -> None:
    """把安装根放进 sys.path，让管理器用得上 ``flandora`` 包里的模块。

    源码形态下 ``flandora/`` 与 ``manager/`` 是兄弟目录，不放进来就 import 不到；
    打包形态下 PyInstaller 已经把包收进 exe，这一步是空操作。
    """
    root = str(BRIDGE_DIR)
    if root not in sys.path:
        sys.path.insert(0, root)
