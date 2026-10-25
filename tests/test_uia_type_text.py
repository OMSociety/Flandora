"""验证：字符投递会把 U+FFFF 以上的字符按 UTF-16 代理项拆开。

背景：WM_CHAR 的 wParam 只装得下一个 16 位码元，emoji 这类字符整码点投过去会被
窗口截断丢掉，输入框回读永远不符，最终整条消息判失败（真机表现为「文字发送失败」）。
这里用假 user32 覆盖拆分规则、两条投递路径，以及回读校验对 emoji 仍然成立。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import uia_sender  # noqa: E402
from flandora.uia_sender import WM_CHAR, UiaSender, utf16_units  # noqa: E402


class _User32:
    def __init__(self):
        self.posted = []

    def PostMessageW(self, hwnd, msg, wparam, lparam):
        self.posted.append((hwnd, msg, wparam, lparam))
        return 1


class _TypingUser32(_User32):
    """把投递的码元拼成输入框内容，模拟真实窗口只认 16 位码元的行为。"""

    def __init__(self, state):
        super().__init__()
        self.state = state

    def PostMessageW(self, hwnd, msg, wparam, lparam):
        super().PostMessageW(hwnd, msg, wparam, lparam)
        if msg != WM_CHAR:
            return 1
        # 真实窗口从 wParam 里只取低 16 位，超出部分被丢掉
        units = self.state["text"] + chr(wparam & 0xFFFF)
        try:
            self.state["text"] = units.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeDecodeError:
            self.state["text"] = units
        return 1


def _sender(user32=None):
    sender = UiaSender.__new__(UiaSender)
    sender._hwnd = 7
    sender._last_error = ""
    sender._user32_inst = lambda: user32 if user32 is not None else _User32()
    return sender


def test_bmp_chars_are_single_units():
    assert utf16_units("a") == (ord("a"),)
    assert utf16_units("中") == (0x4E2D,)
    assert utf16_units("\u2005") == (0x2005,)


def test_astral_char_splits_into_a_surrogate_pair():
    assert utf16_units("🥺") == (0xD83E, 0xDD7A)
    assert utf16_units("😀") == (0xD83D, 0xDE00)


def test_split_units_rebuild_the_character():
    units = utf16_units("🥺")
    raw = b"".join(unit.to_bytes(2, "little") for unit in units)
    assert raw.decode("utf-16-le") == "🥺"


def test_post_char_posts_one_message_per_unit():
    user32 = _User32()
    _sender(user32)._post_char("🥺")
    assert [p[2] for p in user32.posted] == [0xD83E, 0xDD7A]
    assert {p[1] for p in user32.posted} == {WM_CHAR}


def test_post_char_keeps_bmp_char_in_one_message():
    user32 = _User32()
    _sender(user32)._post_char("中")
    assert [p[2] for p in user32.posted] == [0x4E2D]


def test_post_char_without_a_window_posts_nothing():
    user32 = _User32()
    sender = _sender(user32)
    sender._hwnd = 0
    sender._post_char("🥺")
    assert user32.posted == []


def test_type_text_succeeds_for_a_message_with_an_emoji(monkeypatch):
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    state = {"text": ""}
    sender = _sender(_TypingUser32(state))
    sender._input_text = lambda: state["text"]
    sender._clear_input = lambda: state.update(text="") or True
    assert sender._type_text("这么晚还不睡，是要偷偷修仙嘛🥺") is True
    assert state["text"] == "这么晚还不睡，是要偷偷修仙嘛🥺"


def test_whole_codepoint_never_reaches_the_input_box(monkeypatch):
    """回归护栏：整码点投递时回读必然不符，三次重打后判失败。"""
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    monkeypatch.setattr(uia_sender, "utf16_units", lambda ch: (ord(ch),))
    state = {"text": ""}
    sender = _sender(_TypingUser32(state))
    sender._input_text = lambda: state["text"]
    sender._clear_input = lambda: state.update(text="") or True
    assert sender._type_text("🥺") is False


def test_search_typing_uses_the_same_split(monkeypatch):
    """搜索框输联系人名走同一个投递函数，emoji 群名同样能打进去。"""
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    state = {"text": ""}
    sender = _sender(_TypingUser32(state))
    sender._search_input = lambda: object()
    sender._post_click = lambda *a, **k: True
    sender._open_first_search_hit = lambda contact: False
    sender._leave_search = lambda: None
    assert sender._search_and_select("群🥺") is False  # 没打开成功，但字符已按码元投递
    assert state["text"] == "群🥺"
