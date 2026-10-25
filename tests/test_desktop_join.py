"""执行线程归位到微信桌面：隔离桌面模式下修饰键才传得过去。

背景：``AttachThreadInput`` 要求两个线程在同一张桌面。隔离桌面模式下发送线程在默认
桌面、微信在 FlandoraSandbox 上，不归位就附着失败，Ctrl 被当成普通字符打进输入框
（粘贴变成字面的 v，实测）。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from flandora.uia_sender import UiaSender
from flandora.win32 import windows


class _FakeUser32:
    """只记调用，不碰真桌面。"""

    def __init__(self, *, open_result: int = 1234, set_result: int = 1) -> None:
        self.open_result = open_result
        self.set_result = set_result
        self.opened: list[tuple] = []
        self.moved: list[int] = []
        self.closed: list[int] = []

    def OpenDesktopW(self, name, flags, inherit, access):
        self.opened.append((name, flags, inherit, access))
        return self.open_result

    def SetThreadDesktop(self, hdesk):
        self.moved.append(hdesk)
        return self.set_result

    def CloseDesktop(self, hdesk):
        self.closed.append(hdesk)


def _patch_user32(monkeypatch, fake: _FakeUser32) -> None:
    monkeypatch.setattr(windows.ctypes, "windll", SimpleNamespace(user32=_Dll(fake)))


class _Func:
    """像 ctypes 的函数对象：被测代码会往上设 restype。"""

    def __init__(self, func):
        self._func = func
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self._func(*args)


class _Dll:
    """把有状态的对象包成「像 ctypes 的 DLL」。"""

    def __init__(self, target):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_funcs", {})

    def __getattr__(self, name):
        funcs = object.__getattribute__(self, "_funcs")
        if name not in funcs:
            attr = getattr(object.__getattribute__(self, "_target"), name)
            if not callable(attr):
                return attr
            funcs[name] = _Func(attr)
        return funcs[name]


def _sender(desktop: str = "") -> UiaSender:
    sender = UiaSender.__new__(UiaSender)
    sender.isolated_desktop = desktop
    sender._desktop_state = threading.local()
    return sender


# ---------------------------------------------------------------- win32 帮手


def test_blank_name_is_a_noop(monkeypatch):
    """没开隔离桌面时不碰任何 Win32 调用。"""
    fake = _FakeUser32()
    _patch_user32(monkeypatch, fake)
    assert windows.join_thread_to_desktop("") is True
    assert fake.opened == []


def test_join_opens_moves_and_closes(monkeypatch):
    fake = _FakeUser32()
    _patch_user32(monkeypatch, fake)
    assert windows.join_thread_to_desktop("FlandoraSandbox") is True
    assert fake.opened == [("FlandoraSandbox", 0, False, windows.DESKTOP_ALL_ACCESS)]
    assert fake.moved == [1234]
    assert fake.closed == [1234]  # 句柄要还回去，别泄漏


def test_unopenable_desktop_reports_failure(monkeypatch):
    fake = _FakeUser32(open_result=0)
    _patch_user32(monkeypatch, fake)
    assert windows.join_thread_to_desktop("FlandoraSandbox") is False
    assert fake.moved == []


def test_failed_switch_still_closes_the_handle(monkeypatch):
    fake = _FakeUser32(set_result=0)
    _patch_user32(monkeypatch, fake)
    assert windows.join_thread_to_desktop("FlandoraSandbox") is False
    assert fake.closed == [1234]


# ---------------------------------------------------------------- 发送器侧


def test_sender_without_isolated_desktop_never_joins(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(windows, "join_thread_to_desktop", lambda name: calls.append(name) or True)
    _sender("")._join_wechat_desktop()
    assert calls == []


def test_sender_joins_once_per_thread(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(windows, "join_thread_to_desktop", lambda name: calls.append(name) or True)
    sender = _sender("FlandoraSandbox")
    sender._join_wechat_desktop()
    sender._join_wechat_desktop()
    assert calls == ["FlandoraSandbox"]


def test_sender_joins_again_on_another_thread(monkeypatch):
    """发送可能落在别的线程上（线程池），每个线程都得自己归位。"""
    calls: list[str] = []
    monkeypatch.setattr(windows, "join_thread_to_desktop", lambda name: calls.append(name) or True)
    sender = _sender("FlandoraSandbox")
    sender._join_wechat_desktop()
    worker = threading.Thread(target=sender._join_wechat_desktop)
    worker.start()
    worker.join()
    assert calls == ["FlandoraSandbox", "FlandoraSandbox"]


def test_failed_join_is_logged_and_not_remembered(monkeypatch, caplog):
    monkeypatch.setattr(windows, "join_thread_to_desktop", lambda name: False)
    sender = _sender("FlandoraSandbox")
    with caplog.at_level("WARNING", logger="flandora"):
        sender._join_wechat_desktop()
    assert "没能归位到隔离桌面" in caplog.text
    assert getattr(sender._desktop_state, "name", "") == ""
