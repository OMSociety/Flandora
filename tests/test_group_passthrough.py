"""验证：群消息全量上报，回复策略属于客户端。

桥接只负责如实上报并附上 is_mentioned 标记：没 @ 机器人的群消息照样原样推出去，
@ 的判定必须准确。下面几个用例守住这条边界。
"""

import logging
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import bridge_core, ob_ids  # noqa: E402
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
    """收件源 store 的替身：只提供昵称、登录 wxid 与名字表。"""

    def __init__(self, nickname, my_wxid="wxid_me", names=None):
        self._nickname = nickname
        self.my_wxid = my_wxid
        self._names = dict(names or {})

    def self_nickname(self):
        return self._nickname

    def display_names(self):
        return dict(self._names)


def test_group_mention_becomes_an_at_segment(isolated_ids, monkeypatch):
    """群里的真 @（昵称后带标记字符）要还原成 at 段，标记字符不进客户端文本。"""
    bridge = _make_bridge()
    pushed = _capture(monkeypatch)
    RUNTIME.store = _StubStore("芙兰朵露Flandre", names={"wxid_me": "芙兰朵露Flandre"})
    isolated_ids.SELF.set_wxid("wxid_me")

    bridge.emit_message({**GROUP_MSG, "content": "@芙兰朵露Flandre\u2005晚上好"})

    assert len(pushed) == 1, pushed
    event = pushed[0]
    assert [seg["type"] for seg in event["message"]] == ["at", "text"]
    # @ 的是桥接自己：at 的 qq 就是事件的 self_id
    assert event["message"][0]["data"]["qq"] == event["self_id"]
    assert event["message"][1]["data"]["text"] == "晚上好"
    assert "\u2005" not in event["raw_message"]
    assert event["is_mentioned"] is True


def test_hand_typed_at_stays_text(isolated_ids, monkeypatch):
    """手打的 @ 没有标记字符，不能被当成提及拆成 at 段。"""
    bridge = _make_bridge()
    pushed = _capture(monkeypatch)
    RUNTIME.store = _StubStore("芙兰朵露Flandre", names={"wxid_me": "芙兰朵露Flandre"})

    bridge.emit_message({**GROUP_MSG, "content": "@芙兰朵露Flandre 晚上好"})

    assert [seg["type"] for seg in pushed[0]["message"]] == ["text"]
    assert _text_of(pushed[0]) == "@芙兰朵露Flandre 晚上好"


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


def test_group_name_learning_logs_only_on_first_sight_and_rename(isolated_ids, monkeypatch, caplog):
    """同一个群继续来消息不重复记名字：只在首次见到或群改名时留下 [群名学习]。"""
    bridge = _make_bridge()
    _capture(monkeypatch)
    group_id = ob_ids.contact_id(GROUP_MSG["sessionId"])

    with caplog.at_level(logging.INFO, logger="flandora"):
        bridge.emit_message(dict(GROUP_MSG))
        bridge.emit_message(dict(GROUP_MSG))
        assert caplog.text.count("[群名学习]") == 1, caplog.text

        caplog.clear()
        bridge.emit_message({**GROUP_MSG, "groupName": "改了名的群"})

    assert caplog.text.count("[群名学习]") == 1, caplog.text
    assert ob_ids.lookup_name(group_id) == "改了名的群"


QUOTE_OF_ME = {
    "server_id": "883290421",
    "sender": "wxid_me",
    "name": "芙兰朵露Flandre",
}


def test_quote_of_the_bot_itself_is_marked_not_prefixed(isolated_ids, monkeypatch):
    """有人引用机器人刚说过的话：事件标 reply_to_self，消息段不替客户端补 @。"""
    bridge = _make_bridge()
    pushed = _capture(monkeypatch)
    RUNTIME.store = _StubStore("芙兰朵露Flandre", names={"wxid_me": "芙兰朵露Flandre"})
    isolated_ids.SELF.set_wxid("wxid_me")

    bridge.emit_message({**GROUP_MSG, "content": "这话我同意", "quote": dict(QUOTE_OF_ME)})

    event = pushed[0]
    assert event["reply_to_self"] is True
    assert [seg["type"] for seg in event["message"]] == ["reply", "text"]
    # 被引的是自己发的消息，没进过事件存档，只能给 0
    assert event["message"][0]["data"]["id"] == 0
    assert _text_of(event) == "这话我同意"


def test_quote_of_someone_else_carries_no_at_self(isolated_ids, monkeypatch):
    """引用别人说的话不补 at：那不是在叫机器人。"""
    bridge = _make_bridge()
    pushed = _capture(monkeypatch)
    isolated_ids.SELF.set_wxid("wxid_me")

    bridge.emit_message(
        {**GROUP_MSG, "content": "同意", "quote": {**QUOTE_OF_ME, "sender": "wxid_lisi"}}
    )

    assert [seg["type"] for seg in pushed[0]["message"]] == ["reply", "text"]


def test_quote_of_the_bot_with_a_real_mention_adds_only_one_at(isolated_ids, monkeypatch):
    """正文里已经 @ 了机器人：不再补第二个 at。"""
    bridge = _make_bridge()
    pushed = _capture(monkeypatch)
    RUNTIME.store = _StubStore("芙兰朵露Flandre", names={"wxid_me": "芙兰朵露Flandre"})
    isolated_ids.SELF.set_wxid("wxid_me")

    bridge.emit_message(
        {**GROUP_MSG, "content": "@芙兰朵露Flandre\u2005同意", "quote": dict(QUOTE_OF_ME)}
    )

    assert [seg["type"] for seg in pushed[0]["message"]] == ["reply", "at", "text"]


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


# ------------------------------------------------------------- 消息没送出去要记账


def _dropped() -> int:
    return bridge_core.events.EVENTS.counters_copy()["dropped"]


def test_a_push_nobody_received_is_counted_as_dropped(isolated_ids, monkeypatch):
    """没有客户端在线时这条消息就没了，计数器必须记下来。

    仪表盘上的「丢弃」要真的记上：_push_entry 算出的 sent 没人收下时，
    这一条必须进 dropped，否则出了问题只能翻日志猜。
    """
    bridge = _make_bridge()
    monkeypatch.setattr(bridge_core, "push_event", lambda ev: 0)
    before = _dropped()
    bridge.emit_message(dict(GROUP_MSG))
    assert _dropped() == before + 1


def test_a_failed_push_is_counted_as_dropped(isolated_ids, monkeypatch):
    """推送抛异常同样等于这条消息没了：不能只是打条日志、计数器不动。"""

    def _boom(event):
        raise RuntimeError("通道断了")

    bridge = _make_bridge()
    monkeypatch.setattr(bridge_core, "push_event", _boom)
    before = _dropped()
    bridge.emit_message(dict(GROUP_MSG))
    assert _dropped() == before + 1
