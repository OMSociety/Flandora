"""发送键：默认回车；微信把发送键改成 Ctrl+回车时，按结果试出来并记住。

真实微信里这条设置改过之后，单按回车只会插一个换行、消息留在输入框里。这里的
用例只验按键选择与重试判定，不启动微信。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flandora.uia_sender import VK_CONTROL, VK_RETURN, UiaSender  # noqa: E402


class FakeUser32:
    """只记录被置成按下的键态，不做真实调用。"""

    def __init__(self):
        self.states: list[bytes] = []
        # 用普通函数而不是绑定方法：生产代码会往它上面挂 argtypes
        self.SetKeyboardState = lambda state: self.states.append(bytes(state))


def make_sender() -> UiaSender:
    sender = object.__new__(UiaSender)
    sender._send_key = ""
    sender._hwnd = 1
    sender._user32 = FakeUser32()
    box = {"text": ""}
    sender._posted: list[tuple[int, tuple[int, ...]]] = []
    sender._typed: list[str] = []

    sender._input_text = lambda: box["text"]  # type: ignore[method-assign]
    sender._input_box = lambda: None  # type: ignore[method-assign]
    sender._user32_inst = lambda: sender._user32  # type: ignore[method-assign]
    sender._release_foreground = lambda: None  # type: ignore[method-assign]

    def post_key(vk: int, *mods: int) -> None:
        sender._posted.append((vk, mods))

    def clear_input() -> bool:
        box["text"] = ""
        return True

    def type_text(text: str) -> bool:
        sender._typed.append(text)
        box["text"] = text
        return True

    sender._post_key = post_key  # type: ignore[method-assign]
    sender._clear_input = clear_input  # type: ignore[method-assign]
    sender._type_text = type_text  # type: ignore[method-assign]
    return sender


def test_default_send_is_a_plain_enter():
    sender = make_sender()

    sender._press_send()

    assert sender._posted == [(VK_RETURN, ())]


def test_ctrl_enter_fakes_the_ctrl_state():
    sender = make_sender()
    sender._send_key = "ctrl_enter"

    sender._press_send()

    assert sender._posted == [(VK_RETURN, (VK_CONTROL,))], "发送时要带修饰键"
    states = sender._user32.states
    assert states[0][VK_CONTROL] == 0x80, "投递前先把 Ctrl 键态置为按下"
    assert states[-1][VK_CONTROL] == 0, "发完要把键态还原"


def test_retry_switches_to_ctrl_enter_and_remembers_it():
    sender = make_sender()
    sent: list[str] = []

    def confirm(contact: str, text: str, before: str) -> bool:
        return bool(sent)

    def post_send_key(kind: str) -> None:
        sent.append(kind)

    sender._confirm_sent = confirm  # type: ignore[method-assign]
    sender._post_send_key = post_send_key  # type: ignore[method-assign]

    assert sender._retry_send_as_ctrl_enter("文件传输助手", "你好", "") is True
    assert sent == ["ctrl_enter"], "重试要用 Ctrl+回车"
    assert sender._send_key == "ctrl_enter", "试成了就记住，后面不必再试"
    assert sender._typed == ["你好"], "回车留下的换行要先清掉再重新输入"


def test_retry_gives_up_when_ctrl_enter_does_not_send():
    sender = make_sender()
    sender._confirm_sent = lambda *a: False  # type: ignore[method-assign]

    assert sender._retry_send_as_ctrl_enter("文件传输助手", "你好", "") is False
    assert sender._send_key == "", "没试成就不能记成 Ctrl+回车"


def test_failure_hint_clears_leftover_text():
    sender = make_sender()
    box = {"text": "没发出去的内容"}
    sender._input_text = lambda: box["text"]  # type: ignore[method-assign]

    def clear_input() -> bool:
        box["text"] = ""
        return True

    sender._clear_input = clear_input  # type: ignore[method-assign]

    hint = sender._send_failure_hint()

    assert "没发出去" in hint, hint
    assert box["text"] == "", "残留内容必须清掉，否则下一条会把它一起发出去"
