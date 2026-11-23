"""隔离桌面上的微信刚启动时停在登录界面：等它自己进入主界面再绑定。

微信重启后先显示「正在进入」，十几秒后才切到主界面；自动登录还会停在「进入微信」
这个确认按钮上等一次点击。这段过渡里状态仍是 login，发送方若立刻判「请先在面板里
完成登录」，读日志的人会以为要重扫二维码。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flandora import uia_sender  # noqa: E402
from flandora.uia_sender import UiaSender  # noqa: E402


class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _Button:
    Name = "进入微信"
    ControlTypeName = "ButtonControl"
    BoundingRectangle = _Rect(1153, 928, 1405, 982)

    def GetChildren(self):  # noqa: N802
        return []


class _Control:
    BoundingRectangle = _Rect(1069, 514, 1489, 1084)

    def __init__(self, state, enter_button=None):
        self.state = state
        self.enter_button = enter_button

    def Exists(self, timeout=0):  # noqa: N802 - 与被替身的控件接口同名
        return True

    def GetChildren(self):  # noqa: N802 - 登录界面的按钮埋在子控件里
        return [self.enter_button] if self.enter_button is not None else []


class _Auto:
    """按顺序吐出窗口状态：先 login，再 chat。"""

    def __init__(self, states, enter_button=None):
        self.states = list(states)
        self.enter_button = enter_button
        self.controls = []

    def ControlFromHandle(self, hwnd):  # noqa: N802
        state = self.states[min(len(self.controls), len(self.states) - 1)]
        control = _Control(state, self.enter_button)
        self.controls.append(control)
        return control


def _sender(monkeypatch, auto):
    sender = object.__new__(UiaSender)
    sender._auto = auto
    sender.isolated_desktop = "FlandoraSandbox"
    sender._window = None
    sender._hwnd = 0
    monkeypatch.setattr(
        uia_sender.win32_windows,
        "enum_wechat_windows",
        lambda desktop: [{"hwnd": 4242, "rect": (0, 0, 800, 600)}],
    )
    monkeypatch.setattr(uia_sender.time, "sleep", lambda _s: None)
    monkeypatch.setattr(UiaSender, "_window_state", staticmethod(lambda control: control.state))
    return sender


def test_login_screen_waits_until_the_window_becomes_chat(monkeypatch):
    sender = _sender(monkeypatch, _Auto(["login", "login", "chat"]))

    assert sender._find_window_isolated() is True
    assert sender._hwnd == 4242
    assert sender._window is not None


def test_login_screen_with_enter_wechat_is_clicked(monkeypatch):
    """自动登录停在「进入微信」上：投一次左键点击，随后窗口变 chat。"""
    clicked: list[tuple[int, int, int]] = []
    sender = _sender(monkeypatch, _Auto(["login", "login", "chat"], _Button()))
    monkeypatch.setattr(uia_sender.win32_windows, "join_thread_to_desktop", lambda name: True)
    monkeypatch.setattr(uia_sender, "ensure_com", lambda: None)
    monkeypatch.setattr(
        UiaSender,
        "_post_left_click",
        staticmethod(lambda hwnd, x, y: clicked.append((hwnd, x, y)) or True),
    )

    assert sender._find_window_isolated() is True
    assert clicked == [(4242, 210, 441)]
    assert sender._window.state == "chat"


def test_a_window_that_stays_on_login_is_refused(monkeypatch):
    sender = _sender(monkeypatch, _Auto(["login"]))
    monkeypatch.setattr(uia_sender, "ISOLATED_ENTER_SECONDS", 0.0)

    assert sender._find_window_isolated() is False
    assert sender._window is None


def test_client_point_takes_the_centre_of_the_button():
    button = _Rect(1153, 928, 1405, 982)
    window = _Rect(1069, 514, 1489, 1084)

    assert UiaSender._client_point(button, window) == (210, 441)


def test_the_handle_taken_after_entering_is_the_one_bound(monkeypatch):
    """点过「进入微信」微信会换窗口：绑新句柄，旧句柄配新控件会一直校验失败。"""
    clicked: list[tuple[int, int, int]] = []
    sender = _sender(monkeypatch, _Auto(["login", "login", "chat"], _Button()))

    def _enum(desktop):
        # 点过之后微信换了一个窗口，旧句柄不再存在
        return [{"hwnd": 5353 if clicked else 4242, "rect": (0, 0, 800, 600)}]

    monkeypatch.setattr(uia_sender.win32_windows, "enum_wechat_windows", _enum)
    monkeypatch.setattr(uia_sender.win32_windows, "join_thread_to_desktop", lambda name: True)
    monkeypatch.setattr(uia_sender, "ensure_com", lambda: None)
    monkeypatch.setattr(
        UiaSender,
        "_post_left_click",
        staticmethod(lambda hwnd, x, y: clicked.append((hwnd, x, y)) or True),
    )

    assert sender._find_window_isolated() is True
    assert clicked == [(4242, 210, 441)]
    assert sender._hwnd == 5353


class _NamelessWindow:
    """窗口能枚举到，但读属性时连不上提供方（E_NOCONNECTION）。"""

    def Exists(self, timeout=0):  # noqa: N802
        return True

    def __getattr__(self, name):
        raise OSError(-2147220991, "事件无法调用任何订户")


def test_window_that_lost_its_automation_still_creates_a_sender(monkeypatch):
    """微信从登录界面走进主界面时换窗口，旧控件读属性会抛 E_NOCONNECTION。

    这条异常不能让 UiaSender 建不起来：建不起来等于发送通道整条不可用。
    """
    sender = object.__new__(UiaSender)
    sender._window = None
    sender._hwnd = 0
    sender._ready = False

    def _find(_self):
        _self._window = _NamelessWindow()
        return True

    monkeypatch.setattr(UiaSender, "_find_window", _find)

    UiaSender._init(sender)

    assert sender._ready is True
    assert sender._hwnd == 0


def test_failure_while_locating_the_window_keeps_the_sender_constructible(monkeypatch):
    """定位过程中抛异常同样不能把发送器建不起来：发送时会惰性重找窗口。"""
    sender = object.__new__(UiaSender)
    sender._window = None
    sender._hwnd = 0
    sender._ready = False

    def _boom(_self):
        raise OSError(-2147220991, "事件无法调用任何订户")

    monkeypatch.setattr(UiaSender, "_find_window", _boom)

    UiaSender._init(sender)

    assert sender._ready is False
    assert sender._window is None
