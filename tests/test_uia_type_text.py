"""验证：正文按内容挑投递通路——BMP 走 WM_CHAR，emoji 走剪贴板粘贴。

背景：WM_CHAR 的 wParam 只装得下一个 16 位码元，emoji 这类 U+FFFF 以上的字符
投过去会被微信输入框丢掉（实测连代理项拆开投递也一样），真机表现是「文字发送
失败」。这里用假 user32 与假剪贴板覆盖拆分规则、通路选择、粘贴回读与句柄释放。
"""

import ctypes
import os
import sys
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import uia_sender  # noqa: E402
from flandora.uia_sender import (  # noqa: E402
    WM_CHAR,
    UiaSender,
    has_non_bmp,
    needs_paste,
    utf16_units,
)

CF_UNICODETEXT = 13


class _Func:
    """像 ctypes 的函数对象：调用方可以往上设 restype / argtypes。"""

    def __init__(self, func):
        self._func = func
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self._func(*args)


class _Dll:
    """把有状态的对象包成「像 ctypes 的 DLL」，供被测代码设函数签名。"""

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


class _FakeKernel32:
    def __init__(self):
        self.blocks = {}
        self.freed = []
        self._next = 0

    def GlobalAlloc(self, flags, size):
        self._next += 1
        self.blocks[self._next] = ctypes.create_string_buffer(size)
        return self._next

    def GlobalLock(self, handle):
        return ctypes.addressof(self.blocks[handle])

    def GlobalUnlock(self, handle):
        return 1

    def GlobalFree(self, handle):
        self.freed.append(handle)
        return 0


class _FakeClipboardUser32:
    def __init__(self, open_ok=True, accept=True):
        self.data = {}
        self.open_ok = open_ok
        self.accept = accept
        self.closed = 0

    def OpenClipboard(self, hwnd):
        return 1 if self.open_ok else 0

    def EmptyClipboard(self):
        self.data.clear()
        return 1

    def SetClipboardData(self, fmt, handle):
        if not self.accept:
            return 0
        self.data[fmt] = handle
        return handle

    def CloseClipboard(self):
        self.closed += 1
        return 1


def _sender(user32=None):
    sender = UiaSender.__new__(UiaSender)
    sender._hwnd = 7
    sender._last_error = ""
    sender._fg_before = 0
    sender._attached = False
    target = user32 if user32 is not None else _User32()
    sender._user32_inst = lambda: _Dll(target)
    return sender


def _install_clipboard(monkeypatch, user32, kernel32):
    monkeypatch.setattr(
        uia_sender.ctypes,
        "windll",
        SimpleNamespace(user32=_Dll(user32), kernel32=_Dll(kernel32)),
    )


def _clipboard_text(user32, kernel32):
    return (
        kernel32.blocks[user32.data[CF_UNICODETEXT]].raw.split(b"\x00\x00")[0].decode("utf-16-le")
    )


# ---------------------------------------------------------------- 拆分规则


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


def test_has_non_bmp_flags_only_astral_chars():
    assert has_non_bmp("晚安🥺") is True
    assert has_non_bmp("晚安，好梦～") is False
    assert has_non_bmp("") is False


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


# ---------------------------------------------------------------- 通路选择


def test_type_text_handles_bmp_text(monkeypatch):
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    state = {"text": ""}
    sender = _sender(_TypingUser32(state))
    sender._input_text = lambda: state["text"]
    sender._clear_input = lambda: state.update(text="") or True
    assert sender._type_text("这么晚还不睡，是要偷偷修仙嘛") is True
    assert state["text"] == "这么晚还不睡，是要偷偷修仙嘛"


def test_enter_text_types_when_everything_is_bmp(monkeypatch):
    typed = []
    sender = _sender()
    sender._type_text = lambda text: typed.append(text) or True
    sender._paste_text = lambda *a, **k: pytest_fail("BMP 文本不该走粘贴")
    assert sender._enter_text("晚安🥺"[:2]) is True  # 「晚安」两个 BMP 字符
    assert typed == ["晚安"]


def test_enter_text_pastes_when_the_text_has_an_emoji():
    pasted = []
    sender = _sender()
    sender._type_text = lambda text: pytest_fail("含 emoji 的正文不该逐字符投递")
    sender._paste_text = lambda text, *a, **k: pasted.append(text) or True
    assert sender._enter_text("晚安🥺") is True
    assert pasted == ["晚安🥺"]


def test_needs_paste_covers_emoji_and_newlines():
    assert needs_paste("普通一行") is False
    assert needs_paste("带 emoji 🥺") is True
    assert needs_paste("第一行\n第二行") is True


def test_enter_text_pastes_when_the_text_has_a_newline():
    """换行 WM_CHAR 送不进去（实测整行被吞），必须走粘贴。"""
    pasted = []
    sender = _sender()
    sender._type_text = lambda text: pytest_fail("含换行的正文不该逐字符投递")
    sender._paste_text = lambda text, *a, **k: pasted.append(text) or True
    assert sender._enter_text("第一行\n第二行") is True
    assert pasted == ["第一行\n第二行"]


def test_enter_text_clears_the_input_when_pasting_fails():
    cleared = []
    sender = _sender()
    sender._paste_text = lambda *a, **k: False
    sender._clear_input = lambda: cleared.append(True) or True
    assert sender._enter_text("晚安🥺") is False
    assert cleared == [True]


def pytest_fail(message):
    raise AssertionError(message)


def test_mismatch_window_points_at_the_first_difference():
    info = uia_sender.mismatch_window("0123456789", "01234X6789")
    assert "第 5 个字起不同" in info
    assert "'0123456789'" in info and "'01234X6789'" in info
    assert "长度 10/10" in info


def test_mismatch_window_handles_a_missing_tail():
    info = uia_sender.mismatch_window("abcdef\n", "abcdef")
    assert "第 6 个字起不同" in info
    assert "长度 7/6" in info


# ---------------------------------------------------------------- 粘贴


def test_paste_text_copies_and_verifies():
    copied = []
    sender = _sender()
    sender._copy_text_to_clipboard = lambda text: copied.append(text) or True
    sender._paste_clipboard = lambda expect_image: True
    sender._input_text = lambda: "晚安🥺"
    assert sender._paste_text("晚安🥺") is True
    assert copied == ["晚安🥺"]


def test_paste_text_rejects_a_mismatch():
    sender = _sender()
    sender._copy_text_to_clipboard = lambda text: True
    sender._paste_clipboard = lambda expect_image: True
    sender._input_text = lambda: "晚安"
    assert sender._paste_text("晚安🥺") is False


def test_paste_text_gives_up_when_the_clipboard_copy_fails():
    sender = _sender()
    sender._copy_text_to_clipboard = lambda text: False
    sender._paste_clipboard = lambda expect_image: pytest_fail("没写进剪贴板就不该粘贴")
    assert sender._paste_text("晚安🥺") is False


# ---------------------------------------------------------------- 剪贴板


class _PasteUser32(_User32):
    """记录粘贴动作的先后顺序：先附着输入队列，再置键态，最后投 Ctrl+V。"""

    def __init__(self):
        super().__init__()
        self.calls = []

    def GetWindowThreadProcessId(self, hwnd, pid):
        return 4242

    def AttachThreadInput(self, tid_me, tid_wx, attach):
        self.calls.append("attach" if attach else "detach")
        return 1

    def SetActiveWindow(self, hwnd):
        self.calls.append("active")
        return hwnd

    def SetKeyboardState(self, state):
        self.calls.append("keys")

    def PostMessageW(self, hwnd, msg, wparam, lparam):
        self.calls.append(("post", msg, wparam))
        return 1


def test_paste_clipboard_reattaches_attach_before_posting_ctrl_v(monkeypatch):
    user32 = _PasteUser32()
    sender = _sender(user32)
    sender._input_text = lambda: uia_sender.UiaSender.IMAGE_MARK
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    assert sender._paste_clipboard(expect_image=True) is True
    assert user32.calls.index("attach") < user32.calls.index("keys")
    posts = [c for c in user32.calls if isinstance(c, tuple)]
    assert posts[0] == ("post", uia_sender.WM_KEYDOWN, uia_sender.VK_CONTROL)
    assert posts[1] == ("post", uia_sender.WM_KEYDOWN, uia_sender.VK_V)


def test_copy_text_to_clipboard_writes_utf16(monkeypatch):
    user32 = _FakeClipboardUser32()
    kernel32 = _FakeKernel32()
    _install_clipboard(monkeypatch, user32, kernel32)
    assert _sender(user32)._copy_text_to_clipboard("晚安🥺") is True
    assert _clipboard_text(user32, kernel32) == "晚安🥺"
    assert kernel32.freed == [], "交给系统的句柄不能再释放"
    assert user32.closed == 1


def test_copy_text_to_clipboard_frees_memory_when_rejected(monkeypatch):
    user32 = _FakeClipboardUser32(accept=False)
    kernel32 = _FakeKernel32()
    _install_clipboard(monkeypatch, user32, kernel32)
    assert _sender(user32)._copy_text_to_clipboard("晚安🥺") is False
    assert kernel32.freed == [1]


def test_copy_text_to_clipboard_reports_a_busy_clipboard(monkeypatch):
    user32 = _FakeClipboardUser32(open_ok=False)
    kernel32 = _FakeKernel32()
    _install_clipboard(monkeypatch, user32, kernel32)
    assert _sender(user32)._copy_text_to_clipboard("晚安🥺") is False
    assert kernel32.freed == [1]


# ---------------------------------------------------------------- 搜索框


def test_search_typing_reuses_the_same_posting(monkeypatch):
    """搜索框输联系人名走同一个投递函数（非 BMP 字符微信收不下，未走粘贴）。"""
    monkeypatch.setattr(uia_sender.time, "sleep", lambda *_: None)
    state = {"text": ""}
    sender = _sender(_TypingUser32(state))
    sender._search_input = lambda: object()
    sender._post_click = lambda *a, **k: True
    sender._open_first_search_hit = lambda contact: False
    sender._leave_search = lambda: None
    assert sender._search_and_select("群聊测试") is False  # 没打开成功，但字符已投递
    assert state["text"] == "群聊测试"
