"""端到端：收到的引用与 @ 在推出去的事件里已经是段，不再是扁平文本。"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import bridge_core  # noqa: E402

GROUP = "50071592949@chatroom"

GROUP_MSG = {
    "sessionId": GROUP,
    "sourceName": "AAA直播八卦二号间",
    "groupName": "AAA直播八卦二号间",
    "sender": "wxid_zhangsan",
    "senderName": "张三",
    "content": "",
    "sessionType": "group",
    "localId": 4244,
    "serverId": "SN-2",
    "quote": {"server_id": "SN-1", "sender": "wxid_lisi", "name": "李四"},
}

NAMES = {"wxid_lisi": "李四", "wxid_zhangsan": "张三"}


def _make_bridge():
    return bridge_core.Flandora(sender=lambda *a, **k: None)


def _push_once(monkeypatch, bridge, msg):
    captured = []
    monkeypatch.setattr(bridge_core, "push_event", lambda ev: captured.append(ev) or 1)
    monkeypatch.setattr(bridge, "resolve_names", lambda sid: dict(NAMES))
    bridge.emit_message(dict(msg))
    assert len(captured) == 1, captured
    return captured[0]


def test_quote_is_restored_as_reply_segment(isolated_ids, monkeypatch):
    bridge = _make_bridge()
    quoted_id = isolated_ids.message_ids().ensure(GROUP, 4200, server_id="SN-1")
    event = _push_once(monkeypatch, bridge, {**GROUP_MSG, "content": "同意"})
    assert event["message"][0] == {"type": "reply", "data": {"id": quoted_id}}
    assert event["message"][1]["data"]["text"] == "同意"
    # 自己这条的服务端号也记进了号表，后续别人引用它时能反查
    row = isolated_ids.message_ids().lookup(event["message_id"])
    assert row["server_id"] == "SN-2"
    assert row["local_id"] == 4244


def test_quote_without_known_target_keeps_zero_id(isolated_ids, monkeypatch):
    bridge = _make_bridge()
    event = _push_once(monkeypatch, bridge, {**GROUP_MSG, "content": "同意"})
    assert event["message"][0] == {"type": "reply", "data": {"id": 0}}


def test_mention_in_content_becomes_at_segment(isolated_ids, monkeypatch):
    bridge = _make_bridge()
    event = _push_once(
        monkeypatch, bridge, {**GROUP_MSG, "content": "@李四\u2005看一下这个", "quote": {}}
    )
    assert event["message"] == [
        {"type": "at", "data": {"qq": isolated_ids.contact_id("wxid_lisi")}},
        {"type": "text", "data": {"text": "看一下这个"}},
    ]


def test_plain_at_stays_text(isolated_ids, monkeypatch):
    bridge = _make_bridge()
    event = _push_once(
        monkeypatch, bridge, {**GROUP_MSG, "content": "@李四 看一下这个", "quote": {}}
    )
    assert [s["type"] for s in event["message"]] == ["text"]
    assert event["message"][0]["data"]["text"] == "@李四 看一下这个"


def test_private_quote_is_restored_too(isolated_ids, monkeypatch):
    bridge = _make_bridge()
    quoted_id = isolated_ids.message_ids().ensure("wxid_lisi", 4100, server_id="SN-9")
    event = _push_once(
        monkeypatch,
        bridge,
        {
            "sessionId": "wxid_lisi",
            "sourceName": "李四",
            "sender": "wxid_lisi",
            "senderName": "李四",
            "content": "收到",
            "sessionType": "private",
            "localId": 4101,
            "serverId": "SN-10",
            "quote": {"server_id": "SN-9", "sender": "wxid_zhangsan", "name": "我"},
        },
    )
    assert event["message"][0] == {"type": "reply", "data": {"id": quoted_id}}
