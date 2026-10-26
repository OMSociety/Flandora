"""发送方式：默认回车；回车发不出去就改点「发送」按钮，并记住结论。

真实微信里把发送键换成 Ctrl+回车之后，单按回车只会插一个换行、消息留在输入框里；
点输入框右下角的「发送」按钮与这条设置无关。配置里的 send_key 可以直接指定方式，
auto 才走「试一次并记住」。这里的用例只验分发与重试判定，不启动微信。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flandora import config  # noqa: E402
from flandora.uia_sender import VK_RETURN, UiaSender, _with_cancel_note  # noqa: E402


def test_cancel_note_follows_sentence_punctuation():
    """原因自己带句号时不能再接逗号，否则日志出现「。，」。"""
    assert _with_cancel_note("程序点不到。") == "程序点不到。本次发送已取消"
    assert _with_cancel_note("没选中") == "没选中，本次发送已取消"
    assert _with_cancel_note("") == "本次发送已取消"


def make_sender() -> UiaSender:
    sender = object.__new__(UiaSender)
    sender._send_key = ""
    sender._hwnd = 1
    box = {"text": ""}
    sender._posted: list[tuple[int, tuple[int, ...]]] = []
    sender._clicked = 0
    sender._typed: list[str] = []

    sender._input_text = lambda: box["text"]  # type: ignore[method-assign]
    sender._input_box = lambda: None  # type: ignore[method-assign]
    sender._release_foreground = lambda: None  # type: ignore[method-assign]

    def post_key(vk: int, *mods: int) -> None:
        sender._posted.append((vk, mods))

    def click_button() -> bool:
        sender._clicked += 1
        return True

    def clear_input() -> bool:
        box["text"] = ""
        return True

    def type_text(text: str) -> bool:
        sender._typed.append(text)
        box["text"] = text
        return True

    sender._post_key = post_key  # type: ignore[method-assign]
    sender._click_send_button = click_button  # type: ignore[method-assign]
    sender._clear_input = clear_input  # type: ignore[method-assign]
    sender._type_text = type_text  # type: ignore[method-assign]
    return sender


@pytest.fixture
def send_mode():
    """把配置里的 send_key 钉成指定值，用例结束恢复（删掉覆盖项即回默认）。"""

    def apply(mode: str) -> None:
        config.SEND_KEY = mode

    yield apply
    del config.SEND_KEY


def test_default_send_is_a_plain_enter():
    sender = make_sender()

    sender._press_send()

    assert sender._posted == [(VK_RETURN, ())]
    assert sender._clicked == 0


def test_learned_button_mode_clicks_instead_of_pressing_enter():
    sender = make_sender()
    sender._send_key = "button"

    sender._press_send()

    assert sender._clicked == 1, "记住是按钮之后就直接点按钮"
    assert sender._posted == []


def test_config_can_pin_the_send_mode(send_mode):
    send_mode("button")
    sender = make_sender()
    sender._press_send()
    assert sender._clicked == 1

    pinned = make_sender()
    send_mode("enter")
    pinned._send_key = "button"
    pinned._press_send()
    assert pinned._posted == [(VK_RETURN, ())], "配置指定回车时不用记住的按钮"
    assert pinned._clicked == 0


def test_unknown_config_value_falls_back_to_auto(send_mode):
    send_mode("whatever")
    sender = make_sender()

    assert sender._configured_send_mode() == "auto"


def test_auto_may_retry_but_pinned_does_not(send_mode):
    sender = make_sender()

    send_mode("auto")
    assert sender._may_retry_by_button() is True
    sender._send_key = "button"
    assert sender._may_retry_by_button() is False, "已经试成过就不必再试"

    pinned = make_sender()
    send_mode("enter")
    assert pinned._may_retry_by_button() is False, "配置固定了方式就不去猜"


def test_retry_clicks_the_button_and_remembers_it():
    sender = make_sender()
    sender._confirm_sent = lambda contact, text, before: sender._clicked > 0  # type: ignore[method-assign]

    assert sender._retry_by_send_button("文件传输助手", "你好", "") is True
    assert sender._clicked == 1, "重试走的是点按钮"
    assert sender._send_key == "button", "试成了就记住，后面不必再试"
    assert sender._typed == ["你好"], "回车留下的换行要先清掉再重新输入"


def test_retry_gives_up_when_the_button_does_not_send():
    sender = make_sender()
    sender._confirm_sent = lambda *a: False  # type: ignore[method-assign]

    assert sender._retry_by_send_button("文件传输助手", "你好", "") is False
    assert sender._send_key == "", "没试成就不能记成按钮"


def test_retry_stops_when_the_button_is_missing():
    sender = make_sender()
    sender._click_send_button = lambda: False  # type: ignore[method-assign]

    assert sender._retry_by_send_button("文件传输助手", "你好", "") is False


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
