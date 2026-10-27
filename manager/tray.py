# -*- coding: utf-8 -*-
"""托盘图标与右键菜单。

没有窗口：状态看图标颜色（运行中绿、已停止灰）和悬停提示，操作全在菜单里。
菜单动作由 pystray 在独立线程里执行，动作结束立刻刷新一次状态。

左键单击触发菜单的默认项 = 打开面板。pystray 没有"双击"这种事件，托盘上能挂的
只有单击，所以面板就是单击的默认动作；桥接没在跑时给一条气泡提示，不开空白页。
"""

from __future__ import annotations

import ctypes
import os
import threading
import time

import bridge as bridge_mod
import paths
import pystray
from PIL import Image
from pystray import Menu, MenuItem

paths.ensure_bridge_importable()

from flandora import branding  # noqa: E402  —— 上一行负责让它 import 得到

RUNNING_COLOUR = branding.BRAND_GREEN
STOPPED_COLOUR = branding.BRAND_GREY
POLL_SECONDS = 2.0
PANEL_WAIT_SECONDS = 10.0

# uxtheme.dll 的序号导出（未公开，但 Windows 10/11 上稳定）：让经典 Win32 菜单
# 跟随系统深浅色。135 = SetPreferredAppMode，136 = FlushMenuThemes；
# 模式 1 = AllowDark，即"系统用深色就跟，系统用浅色就浅"。
_SET_PREFERRED_APP_MODE = 135
_FLUSH_MENU_THEMES = 136
_ALLOW_DARK = 1


def enable_dark_menus() -> bool:
    """让右键菜单按系统的深浅色主题绘制；系统不支持时返回 False 保持默认外观。

    经典菜单的颜色由 uxtheme 决定，进程不声明偏好就一律画成浅色。这条调用必须在
    创建图标（也就是第一次建菜单）之前完成，否则已经建好的菜单不会重画。
    """
    if os.name != "nt":
        return False
    try:
        uxtheme = ctypes.WinDLL("uxtheme", use_last_error=True)
        set_preferred = uxtheme[_SET_PREFERRED_APP_MODE]
        set_preferred.argtypes = [ctypes.c_int]
        set_preferred.restype = ctypes.c_int
        flush = uxtheme[_FLUSH_MENU_THEMES]
        flush.argtypes = []
        flush.restype = None
        set_preferred(_ALLOW_DARK)
        flush()
    except (OSError, AttributeError, TypeError):
        return False
    return True


def make_icon_image(colour: tuple[int, int, int, int], size: int = 64) -> Image.Image:
    """托盘图标：与 ``assets/icon.svg``、可执行文件同源的标记，只换底色。"""
    return branding.icon_image(colour, size)


class ManagerTray:
    """一个托盘图标：图标颜色 + 悬停提示 + 右键菜单，就这些。"""

    def __init__(
        self,
        process: bridge_mod.BridgeProcess,
        *,
        open_panel_when_ready: bool = False,
    ) -> None:
        """``open_panel_when_ready``：管理器自己把桥接拉起来之后顺手打开面板。

        安装完第一次运行时用得上——否则用户只有一个托盘图标，不知道该去浏览器里
        敲地址。
        """
        self.process = process
        self._open_panel_when_ready = open_panel_when_ready
        self._running = process.is_running()
        enable_dark_menus()
        self._icon = pystray.Icon(
            "flandora-manager",
            icon=self._image(),
            title=self._title(),
            menu=self._menu(),
        )

    # ---------- 组装 ----------
    def _image(self) -> Image.Image:
        return make_icon_image(RUNNING_COLOUR if self._running else STOPPED_COLOUR)

    def _title(self) -> str:
        return f"Flandora 管理器 · {'运行中' if self._running else '已停止'}"

    def _menu(self) -> Menu:
        running = self._running
        return Menu(
            MenuItem("运行中" if running else "已停止", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("打开面板", self._open_panel, default=True),
            MenuItem("启动桥接", self._start, enabled=not running),
            MenuItem("停止桥接", self._stop, enabled=running),
            MenuItem("重启桥接", self._restart, enabled=running),
            MenuItem("打开日志", self._open_log),
            MenuItem("退出程序", self._quit),
        )

    # ---------- 菜单动作 ----------
    def _open_panel(self, *_args) -> None:
        if not self.process.is_running():
            self._notify("桥接没有在跑，先在菜单里点「启动桥接」")
            return
        _, web_port = self.process.refresh_ports()
        if not self._wait_web_port(web_port, PANEL_WAIT_SECONDS):
            self._notify("面板还没起来，看一眼「打开日志」")
            return
        if paths.IS_WINDOWS:
            os.startfile(f"http://127.0.0.1:{web_port}")  # noqa: S606

    def _open_log(self, *_args) -> None:
        target = paths.BRIDGE_LOG if paths.BRIDGE_LOG.exists() else paths.DATA_DIR
        if paths.IS_WINDOWS and target.exists():
            os.startfile(str(target))  # noqa: S606

    def _start(self, *_args) -> None:
        self._after_action(*self.process.start())

    def _stop(self, *_args) -> None:
        self._after_action(*self.process.stop())

    def _restart(self, *_args) -> None:
        self._after_action(*self.process.restart())

    def _quit(self, *_args) -> None:
        """停桥接再退管理器：托盘图标一消失，用户就没有别的地方能停它了。"""
        self.process.stop()
        self._icon.stop()

    def _after_action(self, ok: bool, message: str) -> None:
        """动作之后刷新状态；失败用气泡提示说一声（没有窗口可写）。"""
        if not ok:
            self._notify(message)
        self._refresh(force=True)

    def _notify(self, message: str) -> None:
        try:
            self._icon.notify(message, "Flandora 管理器")
        except Exception:  # noqa: BLE001  —— 系统不支持气泡提示就算了
            pass

    @staticmethod
    def _wait_web_port(port: int, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if bridge_mod.port_is_listening(port):
                return True
            time.sleep(0.25)
        return False

    # ---------- 启动与刷新 ----------
    def _launch(self) -> None:
        """管理器起来就把桥接拉起来；第一次运行顺手打开面板。"""
        if self.process.is_running():
            self._refresh(force=True)
            return
        ok, message = self.process.start()
        self._refresh(force=True)
        if not ok:
            self._notify(message)
            return
        if self._open_panel_when_ready:
            self._open_panel()

    def _refresh(self, *, force: bool = False) -> None:
        try:
            running = self.process.is_running()
        except Exception:  # noqa: BLE001
            return
        if running == self._running and not force:
            return
        self._running = running
        try:
            self._icon.icon = self._image()
            self._icon.title = self._title()
            self._icon.menu = self._menu()
        except Exception:  # noqa: BLE001
            pass

    def _poll_loop(self) -> None:
        while True:
            time.sleep(POLL_SECONDS)
            self._refresh()

    def _setup(self, icon: pystray.Icon) -> None:
        # 传了 setup 回调，pystray 就不自己置 visible，图标会注册不上（隐藏窗口建了、
        # 通知区域里什么都没有）。
        icon.visible = True
        threading.Thread(target=self._poll_loop, daemon=True, name="tray-poll").start()
        threading.Thread(target=self._launch, daemon=True, name="tray-launch").start()

    def run(self) -> None:
        self._icon.run(setup=self._setup)
