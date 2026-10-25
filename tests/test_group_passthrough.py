"""验证：群消息全量上报，回复策略属于客户端。

桥接只负责如实上报并附上 is_mentioned 标记：没 @ 机器人的群消息照样原样推出去，
@ 的判定必须准确。下面几个用例守住这条边界。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import bridge_core  # noqa: E402
from flandora.runtime import RUNTIME  # noqa: E402

GROUP_MSG = {
    "sessionId": "50071592949@chatroom",
    "sourceName": "AAA直播八卦二号间",
    "groupName": "AAA直播八卦二号间",
    "sender": "wxid_zhangsan",
    "senderName": "张三",
    "content": "今天下午最后谁都没来我就直接爆炸",
    "sessionType": "group",
    "localId": 4242,
}


def _make_bridge():
    return bridge_core.Flandora(sender=lambda *a, **k: None)


def _capture(monkeypatch):
    """把推出去的事件收进列表，替代真实 WebSocket 发送。"""
    pushed = []
    monkeypatch.setattr(bridge_core, "push_event", lambda ev: pushed.append(ev) or 1)
    return pushed


def _text_of(event):
    return "".join(seg["data"]["text"] for seg in event["message"] if seg["type"] == "text")


class _StubStore:
    """收件源 store 的替身：只提供昵称与登录 wxid。"""

    def __init__(self, nickname, my_wxid="wxid_me"):
        self._nickname = nickname
        self.my_wxid = my_wxid

    def self_nickname(self):
        return self._nickname


def test_group_message_without_mention_is_reported(isolated_ids, monkeypatch):
    """没 @ 机器人的群消息也照发：要不要回由客户端判断。"""
    bridge = _make_bridge()
    pushed = _capture(monkeypatch)

    bridge.emit_message(dict(GROUP_MSG))

    assert len(pushed) == 1, pushed
    event = pushed[0]
    assert event["message_type"] == "group"
    assert event["sender"]["nickname"] == "张三"
    assert _text_of(event) == GROUP_MSG["content"]
    assert event["is_mentioned"] is False


def test_is_mentioned_uses_self_nickname_from_local_db():
    """@ 识别的昵称取自本地通讯录里自己那行，不来自配置。"""
    bridge = _make_bridge()
    RUNTIME.store = _StubStore("芙兰朵露")
    assert bridge._is_mentioned({**GROUP_MSG, "content": "@芙兰朵露 在吗"}) is True
    assert bridge._is_mentioned({**GROUP_MSG, "content": "＠芙兰朵露 在吗"}) is True
    assert bridge._is_mentioned(dict(GROUP_MSG)) is False


def test_is_mentioned_is_false_when_nickname_unavailable():
    """读不到自己昵称时不做误判，也不能崩。"""
    bridge = _make_bridge()
    assert bridge._is_mentioned({**GROUP_MSG, "content": "@芙兰朵露 在吗"}) is False
    RUNTIME.store = _StubStore("")
    assert bridge._is_mentioned({**GROUP_MSG, "content": "@芙兰朵露 在吗"}) is False


def test_should_ignore_drops_messages_from_the_bot_itself():
    """防自回环：按登录 wxid 与自己的昵称两路挡住自己发的消息。"""
    bridge = _make_bridge()
    RUNTIME.store = _StubStore("芙兰朵露", my_wxid="wxid_me")
    assert bridge.should_ignore({"talkerId": "wxid_me", "content": "自己发的"}) is True
    assert bridge.should_ignore({"sourceName": "芙兰朵露", "content": "和自己聊天"}) is True
    assert (
        bridge.should_ignore({"talkerId": "wxid_other", "sourceName": "张三", "content": "在吗"})
        is False
    )
    # 空正文（语音占位符之外的空白）依旧丢掉
    assert (
        bridge.should_ignore({"talkerId": "wxid_other", "sourceName": "张三", "content": "   "})
        is True
    )


def test_pushed_event_carries_integer_ids(isolated_ids, monkeypatch):
    """事件里的 user_id / group_id / message_id 都是整数，且由 ID 层给号。"""
    bridge = _make_bridge()
    pushed = _capture(monkeypatch)

    bridge.emit_message(dict(GROUP_MSG))

    assert len(pushed) == 1, pushed
    event = pushed[0]
    assert event["message_type"] == "group"
    assert isinstance(event["user_id"], int) and event["user_id"] > 0
    assert isinstance(event["group_id"], int) and event["group_id"] > 0
    assert isinstance(event["message_id"], int) and event["message_id"] > 0
    # 昵称只作为展示字段，不进 ID
    assert event["sender"]["nickname"] == "张三"
    assert event["sender"]["user_id"] == event["user_id"]

    # ID 层能反查回微信原文
    assert isolated_ids.handle_of(event["user_id"]) == "wxid_zhangsan"
    assert isolated_ids.handle_of(event["group_id"]) == "50071592949@chatroom"

    # 同一条本地消息取两次得到同一个 message_id
    row = isolated_ids.message_ids().lookup(event["message_id"])
    assert row["talker"] == "50071592949@chatroom"
    assert row["local_id"] == 4242
    assert bridge._message_id_for("50071592949@chatroom", 4242) == event["message_id"]
