"""端到端：收到的引用与 @ 在推出去的事件里已经是段，不是扁平文本。"""

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


# ===== 被引的消息没推过：从本地库补一份存档 =====

QUOTED_ROW = {
    "local_id": 4200,
    "server_id": "SN-0",
    "kind": "text",
    "text": "更早的一句",
    "sender": "wxid_lisi",
    "create_time": 1700000000,
}


class _StubStore:
    """只提供回查所用到的那两个方法，代替真的本地库快照。"""

    def __init__(self, rows):
        self._rows = rows

    def message_by_server(self, username, server_id):
        assert username == GROUP
        return self._rows.get(server_id)

    def display_names(self):
        return dict(NAMES)


def _quote_unpushed(monkeypatch, bridge):
    monkeypatch.setattr(bridge_core.RUNTIME, "store", _StubStore({"SN-0": QUOTED_ROW}))
    return _push_once(
        monkeypatch,
        bridge,
        {**GROUP_MSG, "content": "同意", "quote": {"server_id": "SN-0", "name": "李四"}},
    )


def test_quote_of_an_unpushed_message_is_claimed(isolated_ids, isolated_store, monkeypatch):
    """引用一条 Flandora 没推过的旧消息：回查本地库补档，reply 段给出真号。"""
    bridge = _make_bridge()
    event = _quote_unpushed(monkeypatch, bridge)

    claimed_id = event["message"][0]["data"]["id"]
    assert claimed_id > 0, event["message"]
    archived = bridge_core.ob_store.message_store().get(claimed_id)
    assert archived is not None, "补档没写进存档，客户端 get_msg 仍取不到原文"
    assert archived["raw_message"] == "更早的一句"
    assert isolated_ids.message_by_server("SN-0") == claimed_id


def test_claimed_quote_reuses_the_same_id(isolated_ids, isolated_store, monkeypatch):
    """同一条被引消息被引用两次，两次给出同一个号：号是按 (会话, 行号) 幂等分配的。"""
    bridge = _make_bridge()
    first = _quote_unpushed(monkeypatch, bridge)["message"][0]["data"]["id"]
    second = _quote_unpushed(monkeypatch, bridge)["message"][0]["data"]["id"]
    assert first == second, (first, second)


def test_claimed_quote_keeps_the_existing_archive(isolated_ids, isolated_store, monkeypatch):
    """补档只填空缺：同一条消息只入档一次，重复补档时保留第一次写入的内容。"""
    bridge = _make_bridge()
    first = _quote_unpushed(monkeypatch, bridge)["message"][0]["data"]["id"]
    archive = bridge_core.ob_store.message_store()
    saved = archive.get(first)
    assert saved is not None

    real_save = archive.save_event
    calls = []
    monkeypatch.setattr(
        archive, "save_event", lambda event: calls.append(event) or real_save(event)
    )
    second = _quote_unpushed(monkeypatch, bridge)["message"][0]["data"]["id"]

    assert second == first
    assert calls == [], "存档里已有这条消息，补档不该再写一次"
    assert archive.get(first)["raw_message"] == saved["raw_message"]


def test_unclaimable_quote_still_keeps_zero(isolated_ids, isolated_store, monkeypatch):
    """本地库也查不到（消息已被清理）时仍是 0：段还在，只是取不到原文。"""
    bridge = _make_bridge()
    monkeypatch.setattr(bridge_core.RUNTIME, "store", _StubStore({}))
    event = _push_once(monkeypatch, bridge, {**GROUP_MSG, "content": "同意"})
    assert event["message"][0] == {"type": "reply", "data": {"id": 0}}
