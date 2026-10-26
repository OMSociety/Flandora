"""隔离桌面上的微信刚启动时停在登录界面：等它自己进入主界面再绑定。

微信重启后先显示「正在进入」，十几秒后才切到主界面。这段过渡里状态仍是 login，
发送方若立刻判「请先在面板里完成登录」，读日志的人会以为要重扫二维码。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flandora import uia_sender  # noqa: E402
from flandora.uia_sender import UiaSender  # noqa: E402


class _Control:
    def __init__(self, state):
        self.state = state
        self.exists = True

    def Exists(self, timeout=0):  # noqa: N802 - 与被替身的控件接口同名
        return True


class _Auto:
    """按顺序吐出窗口状态：先 login，再 chat。"""

    def __init__(self, states):
        self.states = list(states)
        self.controls = []

    def ControlFromHandle(self, hwnd):  # noqa: N802
        state = self.states[min(len(self.controls), len(self.states) - 1)]
        control = _Control(state)
        self.controls.append(control)
        return control


def _sender(monkeypatch, auto, enum_windows):
    sender = object.__new__(UiaSender)
    sender._auto = auto
    sender.isolated_desktop = "FlandoraSandbox"
    sender._window = None
    sender._hwnd = 0
    monkeypatch.setattr(uia_sender.win32_windows, "enum_wechat_windows", enum_windows)
    monkeypatch.setattr(uia_sender.time, "sleep", lambda _s: None)
    monkeypatch.setattr(UiaSender, "_window_state", staticmethod(lambda control: control.state))
    return sender


def test_login_screen_waits_until_the_window_becomes_chat(monkeypatch):
    auto = _Auto(["login", "login", "chat"])
    sender = _sender(
        monkeypatch,
        auto,
        lambda desktop: [{"hwnd": 4242, "rect": (0, 0, 800, 600)}],
    )

    assert sender._find_window_isolated() is True
    assert sender._hwnd == 4242
    assert sender._window is not None


def test_a_window_that_stays_on_login_is_refused(monkeypatch):
    auto = _Auto(["login"])
    sender = _sender(
        monkeypatch,
        auto,
        lambda desktop: [{"hwnd": 4242, "rect": (0, 0, 800, 600)}],
    )
    monkeypatch.setattr(uia_sender, "ISOLATED_ENTER_SECONDS", 0.0)

    assert sender._find_window_isolated() is False
    assert sender._window is None
