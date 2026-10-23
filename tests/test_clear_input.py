"""验证：输入框清不空时会退回 ValuePattern 直接置空，且失败原因能回传给 WebUI。

背景：真实发送时输入框里有过一段别的程序写进去的文本，退格键投递到窗口后删不掉，
发送被中止（保护是对的，但清空能力不够）。这里用假的控件与 user32 覆盖三条路径：
正常为空、退格无效但 ValuePattern 能置空、两条路都失效。
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import uia_sender  # noqa: E402
from flandora.uia_sender import UiaSender  # noqa: E402


class _ValuePattern:
    def __init__(self, state, fail=False):
        self._state = state
        self._fail = fail

    def SetValue(self, value):
        if self._fail:
            raise RuntimeError("控件拒绝置空")
        self._state["text"] = value


class _Box:
    def __init__(self, state, fail=False):
        self._state = state
        self._fail = fail

    def SetFocus(self):
        self._state["focused"] = True

    def GetValuePattern(self):
        return _ValuePattern(self._state, self._fail)


class _User32:
    """按键投递一律无效：模拟按键到不了输入控件。"""

    def PostMessageW(self, *args, **kwargs):
        return 1


def _make_sender(state, fail_setvalue=False):
    sender = UiaSender.__new__(UiaSender)
    sender._hwnd = 1
    sender._last_error = ""
    sender._input_box = lambda: _Box(state, fail_setvalue)
    sender._input_text = lambda: state["text"]
    sender._user32_inst = lambda: _User32()
    return sender


def test_empty_input_is_already_clear(monkeypatch):
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    state = {"text": "", "focused": False}
    assert _make_sender(state)._clear_input() is True


def test_valuepattern_fallback_clears_when_backspace_does_nothing(monkeypatch):
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    state = {"text": "xxxxxx", "focused": False}
    sender = _make_sender(state)
    assert sender._clear_input() is True
    assert state["text"] == ""
    assert state["focused"] is True, "清空前应先把焦点给输入控件"
    assert sender._last_error == ""


def test_failure_reports_reason(monkeypatch):
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    state = {"text": "xxxxxx", "focused": False}
    sender = _make_sender(state, fail_setvalue=True)
    assert sender._clear_input() is False
    assert state["text"] == "xxxxxx"
    assert "清不掉" in sender._last_error
