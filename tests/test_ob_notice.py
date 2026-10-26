"""撤回与拍一拍：解析微信系统消息并转成 notice 事件。

依据是真实库里的样本——撤回是 `sysmsg type="revokemsg"`，拍一拍是
`local_type` 低 49 高 62 的 XML 里带 `<patinfo>`。桥接认结构不认文案，
微信改了说法也不影响解析。
"""

import pytest

from flandora import bridge_core, ob_protocol, wx_store

GROUP = "58482702152@chatroom"

REVOKE_BODY = b"""<sysmsg type="revokemsg"><revokemsg><session>58482702152@chatroom</session>
<msgid>1234567890</msgid><replacemsg><![CDATA["\xe8\xb1\xb9\xe8\xb1\xb9\xe5\x8c\x85" \xe6\x92\xa4\xe5\x9b\x9e\xe4\xba\x86\xe4\xb8\x80\xe6\x9d\xa1\xe6\xb6\x88\xe6\x81\xaf]]></replacemsg>
<revoketime>0</revoketime></revokemsg></sysmsg>"""

POKE_BODY = """<sysmsg type="pat">
<title>豹豹包 拍了拍 芙兰朵露</title>
<patinfo><fromusername>wxid_baobao</fromusername>
<chatusername>58482702152@chatroom</chatusername>
<pattedusername>wxid_flandore</pattedusername>
<suffix>的流萤说真好看</suffix></patinfo></sysmsg>"""


def test_sysmsg_type_reads_attribute():
    assert wx_store.sysmsg_type(REVOKE_BODY) == "revokemsg"
    assert wx_store.sysmsg_type(POKE_BODY) == "pat"
    assert wx_store.sysmsg_type(b"plain text") == ""


def test_revoke_info_extracts_nickname():
    info = wx_store.revoke_info(REVOKE_BODY)

    assert info is not None
    assert info["nickname"] == "豹豹包"


def test_revoke_info_ignores_other_sysmsg():
    assert wx_store.revoke_info(POKE_BODY) is None
    assert wx_store.revoke_info(b"plain text") is None


def test_pat_info_extracts_participants():
    info = wx_store.pat_info(POKE_BODY)

    assert info is not None
    assert info["from"] == "wxid_baobao"
    assert info["group"] == GROUP
    assert info["to"] == "wxid_flandore"
    assert info["suffix"] == "的流萤说真好看"


def test_pat_info_ignores_other_sysmsg():
    assert wx_store.pat_info(REVOKE_BODY) is None


def test_recall_event_shape():
    group = ob_protocol.make_recall_event(
        group_id=123, user_id=456, operator_id=456, message_id=0, nickname="豹豹包"
    )
    private = ob_protocol.make_recall_event(user_id=456, message_id=789, nickname="豹豹包")

    assert group["post_type"] == "notice"
    assert group["notice_type"] == "group_recall"
    assert group["group_id"] == 123
    assert group["user_id"] == 456
    assert group["operator_id"] == 456
    assert group["message_id"] == 0
    # 微信 PC 端的撤回通知不带被撤消息的 msgid，这里必须如实标出来
    assert group["message_id_known"] is False
    assert group["nickname"] == "豹豹包"
    assert private["notice_type"] == "friend_recall"
    assert private["message_id_known"] is True
    assert "group_id" not in private


def test_poke_event_shape():
    event = ob_protocol.make_poke_event(
        group_id=123, user_id=456, target_id=789, suffix="的流萤说真好看"
    )

    assert event["post_type"] == "notice"
    assert event["notice_type"] == "notify"
    assert event["sub_type"] == "poke"
    assert event["group_id"] == 123
    assert event["user_id"] == 456
    assert event["target_id"] == 789
    assert event["suffix"] == "的流萤说真好看"


@pytest.fixture()
def bridge(monkeypatch):
    """真实例 + 假名册：只需要认得这两个 wxid。"""
    instance = bridge_core.Flandora(sender=lambda *a, **k: None)
    monkeypatch.setattr(
        instance,
        "resolve_names",
        lambda handle: {"wxid_baobao": "豹豹包", "wxid_flandore": "芙兰朵露"},
    )
    return instance


def _group_msg(**extra):
    data = {
        "sessionId": GROUP,
        "talkerId": GROUP,
        "sourceName": "测试群",
        "groupName": "测试群",
        "sender": "wxid_baobao",
        "senderName": "豹豹包",
        "sessionType": "group",
    }
    data.update(extra)
    return data


def test_revoke_is_pushed_as_notice_not_message(isolated_ids, bridge, monkeypatch):
    pushed = []
    monkeypatch.setattr(bridge_core, "push_event", pushed.append)

    bridge.emit_message(
        _group_msg(content='"豹豹包" 撤回了一条消息', revoke={"nickname": "豹豹包"})
    )

    assert len(pushed) == 1, pushed
    assert pushed[0]["notice_type"] == "group_recall"
    assert pushed[0]["user_id"] > 0
    # 撤回不进消息流：它不是一条要转发的消息
    assert [ev for ev in pushed if ev.get("post_type") == "message"] == []


def test_poke_is_pushed_as_notice(isolated_ids, bridge, monkeypatch):
    pushed = []
    monkeypatch.setattr(bridge_core, "push_event", pushed.append)

    bridge.emit_message(
        _group_msg(
            content="豹豹包 拍了拍 芙兰朵露",
            pat={"from": "wxid_baobao", "to": "wxid_flandore", "group": GROUP, "suffix": ""},
        )
    )

    assert len(pushed) == 1, pushed
    assert pushed[0]["sub_type"] == "poke"
    assert pushed[0]["user_id"] > 0
    assert pushed[0]["target_id"] > 0
    assert pushed[0]["user_id"] != pushed[0]["target_id"]


def test_unknown_system_message_is_still_reported(isolated_ids, bridge, monkeypatch):
    pushed = []
    monkeypatch.setattr(bridge_core, "push_event", pushed.append)

    bridge.emit_message(_group_msg(content="有人加入了群聊"))

    assert len(pushed) == 1, pushed
    assert pushed[0]["post_type"] == "message"
    assert pushed[0]["raw_message"] == "有人加入了群聊"


def test_revoke_row_is_reported_as_a_recall():
    """local_type=10002 那一行必须带 revoke 字段，否则会被当正文推成消息事件。"""
    row = wx_store.payload_of(
        local_id=7,
        server_id=0,
        local_type=wx_store.BASE_REVOKE,
        sender="wxid_demo",
        create_time=1700000000,
        body="[撤回了一条消息]",
    )

    assert row["kind"] == "revoke"
    assert row["revoke"], "撤回行没有 revoke 字段，桥接就摘不成 notice"
    assert row["text"] == "[撤回了一条消息]"


def test_sysmsg_revoke_row_keeps_the_nickname():
    row = wx_store.payload_of(
        local_id=8,
        server_id=0,
        local_type=wx_store.BASE_SYSTEM,
        sender="wxid_demo",
        create_time=1700000001,
        body=REVOKE_BODY.decode("utf-8"),
    )

    assert row["kind"] == "system"
    assert row["revoke"]["nickname"] == "豹豹包"
    assert row["pat"] == {}
