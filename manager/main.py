# -*- coding: utf-8 -*-
"""Flandora 管理器：任务栏一个托盘图标 + 右键菜单，没有窗口。

菜单里能做的：打开面板、启动/停止/重启桥接、打开日志、退出。
界面本身在浏览器里的网页面板，这里只负责进程的生命周期。

重复双击是常态（用户记不住管理器已经在托盘里）：第二次启动不弹第二个图标，
桥接在跑就把面板叫出来，桥接没跑就安静退出。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bridge as bridge_mod  # noqa: E402
import paths  # noqa: E402
import tray as tray_mod  # noqa: E402


def acquire_single_instance():
    """管理器自己的单实例锁。

    与桥接那把不同名：管理器开着再去拉起桥接是正常工作形态，
    两者抢同一个名字会让被拉起的桥接把自己判成重复实例而退出。
    """
    if str(paths.BRIDGE_DIR) not in sys.path:
        sys.path.insert(0, str(paths.BRIDGE_DIR))
    from flandora.single_instance import MANAGER_MUTEX_NAME, acquire_mutex

    return acquire_mutex(MANAGER_MUTEX_NAME)


def open_running_panel() -> None:
    """管理器已经在托盘里：桥接在跑就把面板叫出来，否则什么也不做。"""
    _, web_port = paths.read_config_ports()
    if not bridge_mod.port_is_listening(web_port):
        return
    if paths.IS_WINDOWS:
        os.startfile(f"http://127.0.0.1:{web_port}")  # noqa: S606


def main() -> int:
    _, already_running = acquire_single_instance()
    if already_running:
        open_running_panel()
        return 0
    # 还没有 config.json 说明是第一次启动：管理器顺手把桥接拉起来之后打开面板
    first_run = not paths.CONFIG_FILE.exists()
    tray_mod.ManagerTray(
        bridge_mod.BridgeProcess(),
        open_panel_when_ready=first_run,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
