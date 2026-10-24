"""提及验收判定的单元测试：连接对象、U+2005 退化形态与完全没有提及。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flandora.uia_sender import UiaSender  # noqa: E402


def make_sender(text: str) -> UiaSender:
    sender = object.__new__(UiaSender)
    sender._input_text = lambda: text  # type: ignore[method-assign]
    return sender


def test_mention_object_is_recognised_by_object_char():
    assert make_sender("@\ufffc 提及自检")._mention_selected("斯兰德Slandre")


def test_mention_token_with_u2005_is_recognised():
    assert make_sender("@斯兰德Slandre\u2005 提及自检")._mention_selected("斯兰德Slandre")


def test_plain_text_at_name_is_not_a_mention():
    assert not make_sender("@斯兰德Slandre 提及自检")._mention_selected("斯兰德Slandre")


def test_other_members_mention_does_not_count():
    assert not make_sender("@小苏打\u2005 你好")._mention_selected("斯兰德Slandre")
