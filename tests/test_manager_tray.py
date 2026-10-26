# -*- coding: utf-8 -*-
"""托盘菜单：状态行、按状态禁用的项、单击打开面板、退出前停桥接。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from pystray import MenuItem

REPO_ROOT = Path(__file__).resolve().parents[1]
MANAGER_DIR = REPO_ROOT / "manager"
for folder in (str(REPO_ROOT), str(MANAGER_DIR)):
    if folder not in sys.path:
        sys.path.insert(0, folder)

import tray as tray_mod  # noqa: E402


class _FakeIcon:
    def __init__(self, name, icon=None, title=None, menu=None):
        self.name = name
        self.icon = icon
        self.title = title
        self.menu = menu
        self.notifications: list[tuple[str, str]] = []
        self.stopped = False
        self.visible = False

    def notify(self, message, title=None):
        self.notifications.append((title, message))

    def stop(self):
        self.stopped = True

    def run(self, setup=None):
        if setup is not None:
            setup(self)


class _FakeProcess:
    def __init__(self, running: bool = False):
        self.running = running
        self.starts = 0
        self.stops = 0
        self.restarts = 0

    def is_running(self) -> bool:
        return self.running

    def refresh_ports(self):
        return 7999, 4950

    def start(self):
        self.starts += 1
        self.running = True
        return True, "已启动（PID 1）"

    def stop(self):
        self.stops += 1
        self.running = False
        return True, "已停止桥接"

    def restart(self):
        self.restarts += 1
        return True, "已停止桥接 → 已启动（PID 1）"


@pytest.fixture()
def icon(monkeypatch):
    monkeypatch.setattr(tray_mod.pystray, "Icon", _FakeIcon)
    return _FakeIcon


def _items(tray: "tray_mod.ManagerTray") -> dict[str, MenuItem]:
    return {
        item.text: item
        for item in tray._icon.menu.items
        if isinstance(item, MenuItem) and item.text != "-"
    }


def test_menu_when_running(icon):
    tray = tray_mod.ManagerTray(_FakeProcess(True))
    items = _items(tray)
    assert items["运行中"].enabled is False
    assert "已停止" not in items
    assert items["打开面板"].default is True
    assert items["启动桥接"].enabled is False
    assert items["停止桥接"].enabled is True
    assert items["重启桥接"].enabled is True
    assert tray._icon.title == "Flandora 管理器 · 运行中"


def test_menu_when_stopped(icon):
    tray = tray_mod.ManagerTray(_FakeProcess(False))
    items = _items(tray)
    assert items["已停止"].enabled is False
    assert items["启动桥接"].enabled is True
    assert items["停止桥接"].enabled is False
    assert items["重启桥接"].enabled is False
    assert tray._icon.title == "Flandora 管理器 · 已停止"


def test_open_panel_when_stopped_says_why(icon, monkeypatch):
    opened = []
    monkeypatch.setattr(tray_mod.os, "startfile", lambda url: opened.append(url))
    tray = tray_mod.ManagerTray(_FakeProcess(False))
    tray._open_panel()
    assert opened == []
    assert any("没有在跑" in message for _title, message in tray._icon.notifications)


def test_open_panel_opens_the_configured_port(icon, monkeypatch):
    opened = []
    monkeypatch.setattr(tray_mod.os, "startfile", lambda url: opened.append(url))
    monkeypatch.setattr(tray_mod.bridge_mod, "port_is_listening", lambda _port: True)
    monkeypatch.setattr(tray_mod.paths, "IS_WINDOWS", True)
    tray = tray_mod.ManagerTray(_FakeProcess(True))
    tray._open_panel()
    assert opened == ["http://127.0.0.1:4950"]


def test_open_panel_waits_for_the_port(icon, monkeypatch):
    monkeypatch.setattr(tray_mod.os, "startfile", lambda _url: None)
    monkeypatch.setattr(tray_mod.bridge_mod, "port_is_listening", lambda _port: False)
    monkeypatch.setattr(tray_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(tray_mod, "PANEL_WAIT_SECONDS", 0.0)
    tray = tray_mod.ManagerTray(_FakeProcess(True))
    tray._open_panel()
    assert any("面板还没起来" in message for _title, message in tray._icon.notifications)


def test_quit_stops_the_bridge_before_dropping_the_icon(icon):
    process = _FakeProcess(True)
    tray = tray_mod.ManagerTray(process)
    tray._quit()
    assert process.stops == 1
    assert tray._icon.stopped is True


def test_launch_starts_the_bridge_and_refreshes_the_menu(icon):
    process = _FakeProcess(False)
    tray = tray_mod.ManagerTray(process)
    tray._launch()
    assert process.starts == 1
    assert "运行中" in _items(tray)
    assert tray._icon.title == "Flandora 管理器 · 运行中"


def test_launch_opens_the_panel_on_first_run(icon, monkeypatch):
    opened = []
    monkeypatch.setattr(tray_mod.os, "startfile", lambda url: opened.append(url))
    monkeypatch.setattr(tray_mod.bridge_mod, "port_is_listening", lambda _port: True)
    monkeypatch.setattr(tray_mod.paths, "IS_WINDOWS", True)
    tray = tray_mod.ManagerTray(_FakeProcess(False), open_panel_when_ready=True)
    tray._launch()
    assert opened == ["http://127.0.0.1:4950"]


def test_run_makes_the_icon_visible(icon):
    """传了 setup 回调时 pystray 不自己置 visible，图标得由我们点亮。"""
    tray = tray_mod.ManagerTray(_FakeProcess(True))
    tray.run()
    assert tray._icon.visible is True


def test_icon_image_uses_two_colours():
    running = tray_mod.make_icon_image(tray_mod.RUNNING_COLOUR)
    stopped = tray_mod.make_icon_image(tray_mod.STOPPED_COLOUR)
    assert running.getpixel((32, 58)) != stopped.getpixel((32, 58))


class _FakeUxtheme:
    def __init__(self):
        self.modes: list[int] = []
        self.flushed = 0

    def __getitem__(self, index):
        def set_mode(mode):
            self.modes.append(mode)
            return 0

        def flush():
            self.flushed += 1

        return set_mode if index == tray_mod._SET_PREFERRED_APP_MODE else flush


def test_dark_menus_follow_the_system_theme(monkeypatch):
    dll = _FakeUxtheme()
    monkeypatch.setattr(tray_mod.ctypes, "WinDLL", lambda *_args, **_kwargs: dll)
    assert tray_mod.enable_dark_menus() is True
    assert dll.modes == [tray_mod._ALLOW_DARK]
    assert dll.flushed == 1


def test_dark_menus_fall_back_without_uxtheme(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("uxtheme 不存在")

    monkeypatch.setattr(tray_mod.ctypes, "WinDLL", boom)
    assert tray_mod.enable_dark_menus() is False


def test_dark_menus_are_a_windows_thing(monkeypatch):
    monkeypatch.setattr(tray_mod, "os", types.SimpleNamespace(name="posix"))
    assert tray_mod.enable_dark_menus() is False


def test_manager_tray_asks_for_dark_menus(icon, monkeypatch):
    calls = []
    monkeypatch.setattr(tray_mod, "enable_dark_menus", lambda: calls.append(True) or True)
    tray_mod.ManagerTray(_FakeProcess(True))
    assert calls == [True]
