"""端到端：收到的引用与 @ 在推出去的事件里已经是段，不是扁平文本。"""

import base64
import os
import sys
import time

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


def _tiny_png(dest):
    """写一张 1×1 的真 PNG：image 段要读得到文件才会渲染。"""
    dest.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
        )
    )
    return dest


# ===== 图片：能渲染就只发 image 段，不再叠一段「[图片]」文本 =====


def _push_image(monkeypatch, bridge, msg, *, emoji=False):
    """图片与表情包由工作线程处理，这里直接调那个入口，避免等线程。"""
    captured = []
    monkeypatch.setattr(bridge_core, "push_event", lambda ev: captured.append(ev) or 1)
    monkeypatch.setattr(bridge, "resolve_names", lambda sid: dict(NAMES))
    message = bridge._entry_for(dict(msg))
    if emoji:
        bridge.process_emoji_message(message)
    else:
        bridge.process_image_message(message)
    assert len(captured) == 1, captured
    return captured[0]


def test_image_with_a_file_is_one_segment(isolated_ids, monkeypatch, tmp_path):
    bridge = _make_bridge()
    path = _tiny_png(tmp_path / "1.png")
    event = _push_image(
        monkeypatch,
        bridge,
        {
            **GROUP_MSG,
            "content": f"[图片] {path}",
            "imagePath": str(path),
            "quote": {},
        },
    )
    assert [seg["type"] for seg in event["message"]] == ["image"], event["message"]
    # 小图走 base64 内联，大图才给 file://；这里只关心「只有一个 image 段」
    assert event["message"][0]["data"]["file"].startswith(("base64://", "file://"))
    # 普通图片不带表情包标记
    assert "sub_type" not in event["message"][0]["data"]


def test_image_without_a_file_keeps_the_placeholder(isolated_ids, monkeypatch):
    bridge = _make_bridge()
    event = _push_image(
        monkeypatch,
        bridge,
        {**GROUP_MSG, "content": '[图片] md5="0123456789abcdef"', "quote": {}},
    )
    assert event["message"] == [{"type": "text", "data": {"text": "[图片]"}}]


def test_emoji_with_a_file_is_one_segment(isolated_ids, monkeypatch, tmp_path):
    bridge = _make_bridge()
    path = _tiny_png(tmp_path / "emoji.gif")
    event = _push_image(
        monkeypatch,
        bridge,
        {
            **GROUP_MSG,
            "content": '[表情] md5="0123456789abcdef"',
            "imagePath": str(path),
            "quote": {},
        },
        emoji=True,
    )
    assert [seg["type"] for seg in event["message"]] == ["image"], event["message"]
    # 表情包按 go-cqhttp 口径标 sub_type=1，客户端据此与普通图片分开显示
    assert event["message"][0]["data"]["sub_type"] == 1


def test_emoji_without_a_file_keeps_the_placeholder(isolated_ids, monkeypatch):
    bridge = _make_bridge()
    event = _push_image(
        monkeypatch,
        bridge,
        {**GROUP_MSG, "content": '[表情] md5="0123456789abcdef"', "quote": {}},
        emoji=True,
    )
    assert event["message"] == [{"type": "text", "data": {"text": "[表情]"}}]


# ===== 被引的是自己发的：如实标记 reply_to_self，正文不改写；号复用发送时那个 =====

SELF_WXID = "wxid_me"
SELF_NICK = "芙兰朵露Flandre"


def _as_self(isolated_ids, bridge):
    """把当前登录身份设成测试账号，并让取昵称的入口直接给同一个名字。"""
    isolated_ids.SELF.set_wxid(SELF_WXID)
    isolated_ids.SELF.set_nickname(SELF_NICK)
    bridge.self_nickname = lambda: SELF_NICK
    return bridge


def test_group_quote_of_our_own_message_is_marked_not_rewritten(isolated_ids, monkeypatch):
    """群里 fromusr 是群标识，被引那位的名字在 displayname 里——按名字认出自己。

    认出之后只在事件上标 reply_to_self，不往消息段里塞 @：正文里没有的东西不能
    替客户端加上，唤醒与否留给客户端判断。
    """
    bridge = _as_self(isolated_ids, _make_bridge())
    event = _push_once(
        monkeypatch,
        bridge,
        {
            **GROUP_MSG,
            "content": "嗯",
            "quote": {"server_id": "SN-9", "sender": GROUP, "name": SELF_NICK},
        },
    )
    assert event["reply_to_self"] is True
    assert [seg["type"] for seg in event["message"]] == ["reply", "text"]
    assert event["message"][0]["data"]["id"] == 0
    # 没人 @ 机器人，is_mentioned 就还是假
    assert event["is_mentioned"] is False


def test_group_quote_of_someone_else_adds_no_at_segment(isolated_ids, monkeypatch):
    bridge = _as_self(isolated_ids, _make_bridge())
    event = _push_once(
        monkeypatch,
        bridge,
        {
            **GROUP_MSG,
            "content": "嗯",
            "quote": {"server_id": "SN-9", "sender": GROUP, "name": "张三"},
        },
    )
    assert event["reply_to_self"] is False
    assert [seg["type"] for seg in event["message"]] == ["reply", "text"]


def test_private_quote_of_our_own_message_is_marked_too(isolated_ids, monkeypatch):
    """私聊里 fromusr 就是发送者 wxid，直接比 wxid；同样只标记，不补段。"""
    bridge = _as_self(isolated_ids, _make_bridge())
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
            "quote": {"server_id": "SN-9", "sender": SELF_WXID, "name": "我"},
        },
    )
    assert event["reply_to_self"] is True
    assert [seg["type"] for seg in event["message"]] == ["reply", "text"]


def test_claim_reuses_the_id_handed_out_when_sending(isolated_ids, isolated_store, monkeypatch):
    """引用刚发出去的消息：补档要认领发送时那个号，不能另发一个。"""
    bridge = _as_self(isolated_ids, _make_bridge())
    sent_id = isolated_ids.allocate_message_id(GROUP)
    row = {**QUOTED_ROW, "sender": SELF_WXID, "create_time": time.time()}
    monkeypatch.setattr(bridge_core.RUNTIME, "store", _StubStore({"SN-0": row}))

    event = _push_once(
        monkeypatch,
        bridge,
        {
            **GROUP_MSG,
            "content": "同意",
            "quote": {"server_id": "SN-0", "sender": GROUP, "name": SELF_NICK},
        },
    )

    assert event["reply_to_self"] is True
    assert event["message"][0] == {"type": "reply", "data": {"id": sent_id}}, event["message"]
    assert isolated_ids.message_by_server("SN-0") == sent_id
    archived = bridge_core.ob_store.message_store().get(sent_id)
    assert archived is not None and archived["raw_message"] == "更早的一句"


def test_binding_a_sent_message_goes_through_the_module_helper(isolated_ids):
    """回读本地库时按发出时刻认领号：这条路径之前因参数签名不对整段失效。"""
    bridge = _make_bridge()
    sent_id = isolated_ids.allocate_message_id(GROUP)
    bridge._bind_outgoing_server(GROUP, "SN-11", time.time())
    assert isolated_ids.message_by_server("SN-11") == sent_id
