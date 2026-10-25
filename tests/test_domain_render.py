"""领域消息渲染成 OneBot 事件时的字段对齐。

渲染是「一条消息」到「一个事件」的唯一出口：少一个字段客户端就丢了群名或 @
标记；群名带着微信追加的 "(5)" 人数后缀，发送时按名字搜索会话会搜不到。
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import bridge_core  # noqa: E402
from flandora.domain.message import ChatKind, ChatRef, Message, SenderRef, Text  # noqa: E402
from flandora.ob_protocol import render_message_event  # noqa: E402
from flandora.runtime import RUNTIME  # noqa: E402


class _StubStore:
    """只提供渲染会用到的读接口。"""

    my_wxid = "wxid_me"
    _decrypted: set[str] = set()

    def __init__(self, nickname: str):
        self._nickname = nickname

    def self_nickname(self) -> str:
        return self._nickname

    def display_names(self) -> dict[str, str]:
        return {}


GROUP_MSG = {
    "sessionId": "12345678@chatroom",
    "groupName": "测试群(5)",
    "sourceName": "测试群(5)",
    "sender": "wxid_zhangsan",
    "senderName": "张三",
    "sessionType": "group",
    "content": "在吗",
    "localId": 7,
    "timestamp": 1700000000,
}

PRIVATE_MSG = {
    "sessionId": "wxid_lisi",
    "sourceName": "李四",
    "sender": "wxid_lisi",
    "senderName": "李四",
    "sessionType": "private",
    "content": "收到",
    "localId": 8,
    "timestamp": 1700000001,
}


def _capture(monkeypatch) -> list[dict]:
    pushed: list[dict] = []
    monkeypatch.setattr(
        bridge_core, "push_event", lambda event: pushed.append(event) or len(pushed)
    )
    return pushed


def test_group_name_loses_the_member_count_suffix(isolated_ids, monkeypatch):
    pushed = _capture(monkeypatch)
    bridge_core.Flandora(sender=None).emit_message(dict(GROUP_MSG))

    event = pushed[0]
    assert event["group_name"] == "测试群"
    assert event["sender"]["nickname"] == "张三"
    assert event["message_type"] == "group"
    assert isinstance(event["group_id"], int)
    assert isinstance(event["user_id"], int)
    # 微信那边的原始标识一并给出去，客户端要按标识存东西时不必再查一次
    assert event["group_wxid"] == "12345678@chatroom"
    assert event["sender"]["wxid"] == "wxid_zhangsan"


def test_is_mentioned_comes_from_the_raw_text(isolated_ids, monkeypatch):
    RUNTIME.store = _StubStore("芙兰朵露")
    pushed = _capture(monkeypatch)
    bridge_core.Flandora(sender=None).emit_message({**GROUP_MSG, "content": "@芙兰朵露 在吗"})

    assert pushed[0]["is_mentioned"] is True


def test_private_event_has_no_group_fields(isolated_ids, monkeypatch):
    pushed = _capture(monkeypatch)
    bridge_core.Flandora(sender=None).emit_message(dict(PRIVATE_MSG))

    event = pushed[0]
    assert event["message_type"] == "private"
    assert "group_id" not in event
    assert "group_wxid" not in event
    assert "group_name" not in event
    assert "is_mentioned" not in event
    assert event["sender"]["nickname"] == "李四"
    assert event["sender"]["wxid"] == "wxid_lisi"


def test_sender_nickname_falls_back_to_the_integer_id(isolated_ids):
    """没有昵称时给整数 ID 的字符串形式，客户端总有一个能显示的名字。"""
    message = Message(
        chat=ChatRef(handle="wxid_lisi", name="李四", kind=ChatKind.PRIVATE),
        sender=SenderRef(handle="", nickname=""),
        segments=(Text("hi"),),
        message_id=1,
        timestamp=0,
    )

    event = render_message_event(message, "array")

    assert event["sender"]["nickname"] == str(event["user_id"])
    assert isinstance(event["self_id"], int)
    assert event["message"] == [{"type": "text", "data": {"text": "hi"}}]


def main() -> int:
    """脚本直跑：本文件用到 fixture，直接调用会 TypeError，委托给 pytest。"""
    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
