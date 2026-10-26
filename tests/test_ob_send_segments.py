"""发送链路的段形态：语音、视频、@ 提及、未支持段与只发引用段。

重点不是「能发出去」，而是**发不出去时必须报错**：未接线的段类型静默跳过会让
客户端把内容记进上下文；@ 必须走真实名册与提及对象，不能退化成「@昵称」正文。
"""

import asyncio
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import action_catalog, ob_ids, ob_protocol, ob_store  # noqa: E402
from flandora.runtime import RUNTIME


class FakeSender:
    """记录调用结果的假发送器；send_image/其他一律当失败。"""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.texts: list[tuple[str, str]] = []
        self.files: list[tuple[str, str, bytes]] = []
        self.mentions: list[tuple[str, list[str], str]] = []
        self._last_error = ""

    def send_text(self, contact: str, text: str) -> bool:
        if self.ok:
            self.texts.append((contact, text))
        return self.ok

    def send_file(self, contact: str, path: str) -> bool:
        with open(path, "rb") as fh:
            payload = fh.read()
        if self.ok:
            self.files.append((contact, path, payload))
        return self.ok

    def send_mention_text(self, contact: str, names: list[str], text: str) -> bool:
        if self.ok:
            self.mentions.append((contact, names, text))
        return self.ok

    def last_error(self) -> str:
        """与真实发送器同一接口：派发层会把这条原因写进失败响应。"""
        return self._last_error


def _call(payload: dict) -> dict:
    return asyncio.run(ob_protocol._handle_ob_api(payload))


def _prepare(monkeypatch, sender) -> int:
    RUNTIME.sender = sender
    uid = ob_ids.contact_id("wxid_zhangsan")
    ob_ids.remember_name(uid, "张三")
    return uid


def _prepare_group(monkeypatch, sender, members: list[dict]) -> tuple[int, int]:
    RUNTIME.sender = sender
    monkeypatch.setattr(ob_protocol, "_members_roster", lambda handle: members)
    gid = ob_ids.contact_id("50071592949@chatroom")
    ob_ids.remember_name(gid, "测试群")
    uid = ob_ids.contact_id("wxid_lisi")
    return gid, uid


def test_cq_string_is_parsed_into_segments(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": "看这个[CQ:face,id=1]"},
        }
    )
    # face 段没有 file，走 QQ 表情映射；至少文本段必须真的发出去
    assert resp["status"] == "ok"
    assert resp["data"]["message_id"] > 0
    assert sender.texts and sender.texts[0][0] == "张三"


def test_auto_escape_sends_cq_string_verbatim(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    raw = "[CQ:face,id=1]不是表情"
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": raw, "auto_escape": True},
        }
    )
    assert resp["status"] == "ok"
    assert sender.texts == [("张三", raw)]


class _NameStore:
    """只提供 ``display_names()`` 的最小本地库视图。"""

    def __init__(self, names: dict[str, str]):
        self._names = names

    def display_names(self) -> dict[str, str]:
        return dict(self._names)


def test_private_send_asks_the_local_db_for_the_display_name(monkeypatch, isolated_ids):
    """没学过的私聊标识（filehelper 这类系统会话）要先换成显示名再发。"""
    sender = FakeSender()
    RUNTIME.sender = sender
    monkeypatch.setattr(ob_protocol, "_store", lambda: _NameStore({"filehelper": "文件传输助手"}))
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": "filehelper", "message": "在吗"},
        }
    )
    assert resp["status"] == "ok"
    assert sender.texts == [("文件传输助手", "在吗")]


def test_private_send_falls_back_to_the_handle(monkeypatch, isolated_ids):
    """本地库也给不出名字时，仍然按原来的方式拿标识去搜索。"""
    sender = FakeSender()
    RUNTIME.sender = sender
    monkeypatch.setattr(ob_protocol, "_store", lambda: _NameStore({"filehelper": "filehelper"}))
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": "wxid_never_seen", "message": "在吗"},
        }
    )
    assert resp["status"] == "ok"
    assert sender.texts == [("wxid_never_seen", "在吗")]


def test_unsupported_segment_reports_failure(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "forward", "data": {"id": "1"}}]},
        }
    )
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_INTERNAL
    assert "forward" in resp["message"]
    assert sender.texts == []


def test_video_segment_is_sent_as_file(monkeypatch, isolated_ids, tmp_path):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    path = tmp_path / "clip.mp4"
    payload = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
    path.write_bytes(payload)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "video", "data": {"file": str(path)}}]},
        }
    )
    assert resp["status"] == "ok"
    assert resp["data"]["message_id"] > 0
    assert sender.files and sender.files[0][0] == "张三"
    assert sender.files[0][2] == payload


def test_video_base64_is_written_and_removed(monkeypatch, isolated_ids):
    import base64

    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    raw = b"\x00\x00\x00\x18ftypmp42" + b"x" * 16
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [
                    {
                        "type": "video",
                        "data": {"file": "base64://" + base64.b64encode(raw).decode()},
                    }
                ],
            },
        }
    )
    assert resp["status"] == "ok"
    assert sender.files and sender.files[0][2] == raw
    assert sender.files[0][1].endswith(".mp4")
    # 临时文件在发送结束后删掉，不留在 data 目录里
    assert not os.path.exists(sender.files[0][1])


def test_video_without_file_reports_failure(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "video", "data": {}}]},
        }
    )
    assert resp["status"] == "failed"
    assert "视频缺少 file" in resp["message"]


def test_send_by_handle_instead_of_id(monkeypatch, isolated_ids):
    """user_id 直接填 wxid 也能发，客户端不必自己先去换 ID。"""
    sender = FakeSender()
    _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": "wxid_zhangsan", "message": "在吗"},
        }
    )
    assert resp["status"] == "ok"
    assert sender.texts == [("张三", "在吗")]


def test_send_to_unknown_id_reports_failure(monkeypatch, isolated_ids):
    """没分配过的整数 ID 必须报错，不能拿这个数字去微信里搜。"""
    sender = FakeSender()
    _prepare(monkeypatch, sender)
    free = next(n for n in range(1, 5000) if ob_ids.handle_of(n) is None)
    resp = _call({"action": "send_private_msg", "params": {"user_id": free, "message": "在吗"}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_NOT_FOUND
    assert "未知的 user_id" in resp["message"]
    assert sender.texts == []


def test_record_segment_is_sent_as_file(monkeypatch, isolated_ids, tmp_path):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    path = tmp_path / "hello.mp3"
    payload = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 32
    path.write_bytes(payload)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [{"type": "record", "data": {"file": str(path)}}],
            },
        }
    )
    assert resp["status"] == "ok"
    assert resp["data"]["message_id"] > 0
    assert sender.files and sender.files[0][0] == "张三"
    assert sender.files[0][2] == payload


def test_mention_is_sent_with_member_name(monkeypatch, isolated_ids):
    sender = FakeSender()
    members = [
        {
            "username": "wxid_lisi",
            "name": "李四",
            "remark": "",
            "nick_name": "李四",
            "role": "member",
        }
    ]
    gid, uid = _prepare_group(monkeypatch, sender, members)
    resp = _call(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": gid,
                "message": [
                    {"type": "at", "data": {"qq": uid}},
                    {"type": "text", "data": {"text": " 看一下"}},
                ],
            },
        }
    )
    assert resp["status"] == "ok"
    assert sender.mentions == [("测试群", ["李四"], " 看一下")]
    assert sender.texts == []


def test_mention_unknown_member_is_reported(monkeypatch, isolated_ids):
    sender = FakeSender()
    members = [
        {
            "username": "wxid_lisi",
            "name": "李四",
            "remark": "",
            "nick_name": "李四",
            "role": "member",
        }
    ]
    gid, _uid = _prepare_group(monkeypatch, sender, members)
    stranger = ob_ids.contact_id("wxid_wangwu")
    resp = _call(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": gid,
                "message": [
                    {"type": "at", "data": {"qq": stranger}},
                    {"type": "text", "data": {"text": "hi"}},
                ],
            },
        }
    )
    assert resp["status"] == "failed"
    assert "不是这个群的成员" in resp["message"]
    assert sender.mentions == []


def test_mention_at_all_is_refused(monkeypatch, isolated_ids):
    sender = FakeSender()
    members = [
        {
            "username": "wxid_lisi",
            "name": "李四",
            "remark": "",
            "nick_name": "李四",
            "role": "member",
        }
    ]
    gid, _uid = _prepare_group(monkeypatch, sender, members)
    resp = _call(
        {
            "action": "send_group_msg",
            "params": {
                "group_id": gid,
                "message": [
                    {"type": "at", "data": {"qq": "all"}},
                    {"type": "text", "data": {"text": "开会"}},
                ],
            },
        }
    )
    assert resp["status"] == "failed"
    assert "全体成员" in resp["message"]


def test_mention_in_private_chat_is_refused(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [
                    {"type": "at", "data": {"qq": uid}},
                    {"type": "text", "data": {"text": "hi"}},
                ],
            },
        }
    )
    assert resp["status"] == "failed"
    assert "@ 只能用在群消息里" in resp["message"]


def test_reply_only_message_reports_not_implemented(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "reply", "data": {"id": "1"}}]},
        }
    )
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_NOT_IMPLEMENTED
    assert sender.texts == []


def test_sender_failure_is_reported(monkeypatch, isolated_ids):
    sender = FakeSender(ok=False)
    uid = _prepare(monkeypatch, sender)
    resp = _call({"action": "send_private_msg", "params": {"user_id": uid, "message": "hi"}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_INTERNAL
    assert "发送失败" in resp["message"]
    assert "message_id" not in resp["data"]


def test_invalid_message_shape_is_bad_param(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {"action": "send_private_msg", "params": {"user_id": uid, "message": [{"data": {}}]}}
    )
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM


def test_group_name_falls_back_to_roster(monkeypatch, isolated_ids):
    """没收到过消息、也没配映射时，群名从本地库名册取。"""
    sender = FakeSender()
    RUNTIME.sender = sender
    monkeypatch.setattr(ob_protocol, "_group_name", lambda handle: "名册里的群")
    gid = ob_ids.contact_id("50071592949@chatroom")
    resp = _call(
        {
            "action": "send_group_msg",
            "params": {"group_id": gid, "message": [{"type": "text", "data": {"text": "hi"}}]},
        }
    )
    assert resp["status"] == "ok"
    assert sender.texts == [("名册里的群", "hi")]


def test_group_without_any_name_is_reported(monkeypatch, isolated_ids):
    sender = FakeSender()
    RUNTIME.sender = sender
    monkeypatch.setattr(ob_protocol, "_group_name", lambda handle: "")
    gid = ob_ids.contact_id("60000000000@chatroom")
    resp = _call(
        {
            "action": "send_group_msg",
            "params": {"group_id": gid, "message": [{"type": "text", "data": {"text": "hi"}}]},
        }
    )
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
    assert "本地库里没有这个群" in resp["message"]
    assert sender.texts == []


# ---------------------------------------------- message_type 可以省略（按 *_id 推断）


def test_send_msg_infers_the_group_from_group_id(monkeypatch, isolated_ids):
    """不给 message_type 时按 *_id 推断：给了 group_id 就按群聊处理。

    推断错了会发到错误的地方，所以这里必须走群聊那条路（缺什么就报什么），
    而不是把 group_id 放着不用、回头说「缺少 user_id」。
    """
    sender = FakeSender()
    RUNTIME.sender = sender
    monkeypatch.setattr(ob_protocol, "_group_name", lambda handle: "")
    gid = ob_ids.contact_id("70000000000@chatroom")
    resp = _call(
        {
            "action": "send_msg",
            "params": {"group_id": gid, "message": [{"type": "text", "data": {"text": "hi"}}]},
        }
    )
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
    assert "无法定位群" in resp["message"]
    assert "缺少" not in resp["message"]


def test_send_msg_infers_private_from_user_id(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_msg",
            "params": {"user_id": uid, "message": [{"type": "text", "data": {"text": "hi"}}]},
        }
    )
    assert resp["status"] == "ok"
    assert sender.texts == [("张三", "hi")]


def test_unknown_message_type_is_reported(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_msg",
            "params": {
                "user_id": uid,
                "message": [{"type": "text", "data": {"text": "hi"}}],
                "message_type": "guild",
            },
        }
    )
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
    assert "message_type" in resp["message"]
    assert sender.texts == []


def test_contradictory_message_type_is_reported(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [{"type": "text", "data": {"text": "hi"}}],
                "message_type": "group",
            },
        }
    )
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
    assert "矛盾" in resp["message"]
    assert sender.texts == []


# ------------------------------------------------------- 失败原因与部分失败清单


def test_failed_send_carries_the_sender_reason(monkeypatch, isolated_ids):
    """发送失败要带上发送器记下的原因：只回「发送失败」看不出卡在哪一步。"""
    sender = FakeSender(ok=False)
    uid = _prepare(monkeypatch, sender)
    sender._last_error = "输入框有清不掉的残留"
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "text", "data": {"text": "hi"}}]},
        }
    )
    assert resp["status"] == "failed"
    assert "输入框有清不掉的残留" in resp["message"]


def test_partial_send_lists_what_did_not_go_out(monkeypatch, isolated_ids):
    """一段发出去、一段没发出去时，除计数外还要给出原因清单。"""
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [
                    {"type": "text", "data": {"text": "正文"}},
                    {"type": "music", "data": {"id": "1"}},
                ],
            },
        }
    )
    assert resp["status"] == "ok"
    assert sender.texts == [("张三", "正文")]
    assert resp["data"]["failed_segments"] == 1
    assert "不支持的消息段 music" in resp["data"]["failed_details"][0]


# ----------------------------------------------------- 图片解不开时必须说明原因


def test_image_only_message_reports_the_image_reason(monkeypatch, isolated_ids):
    """只发图片、图片又解不开时，报的必须是图片的原因。

    只说「message 里没有可发送的内容」时，客户端没法排查到底哪一段没发出去。
    """
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [{"type": "image", "data": {"file": "base64://abc"}}],
            },
        }
    )
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_INTERNAL
    assert "base64 数据不合法" in resp["message"]
    assert sender.files == []


def test_missing_client_attachment_is_reported(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [{"type": "image", "data": {"file": "查无此图.png"}}],
            },
        }
    )
    assert resp["status"] == "failed"
    assert "文件未找到" in resp["message"]
    assert "查无此图.png" in resp["message"]


def test_unknown_face_id_is_reported(monkeypatch, isolated_ids):
    """表情 id 不在映射表里也要记账。

    只写 debug 日志时，客户端收到的是「没有可发送的内容」，看不出是表情没发出去。
    """
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [{"type": "face", "data": {"id": "999999"}}],
            },
        }
    )
    assert resp["status"] == "failed"
    assert "不在支持列表里" in resp["message"]
    assert sender.texts == []


def test_known_face_id_still_goes_out(monkeypatch, isolated_ids):
    """映射表里有的表情照常发送，不受上面的记账影响。"""
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    known = next(iter(ob_protocol.QQ_FACE_TO_WECHAT))
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [{"type": "face", "data": {"id": str(known)}}],
            },
        }
    )
    assert resp["status"] == "ok"
    assert sender.texts


# ------------------------------------------------- 发出去的消息也要进事件存档


def test_sent_message_is_archived_for_get_msg(monkeypatch, isolated_ids):
    """自己发出的消息进存档：客户端引用机器人说过的话时，靠这个号取回原文。"""
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "text", "data": {"text": "在的"}}]},
        }
    )
    assert resp["status"] == "ok"
    mid = resp["data"]["message_id"]

    got = _call({"action": "get_msg", "params": {"message_id": mid}})
    assert got["status"] == "ok"
    event = got["data"]
    assert event["post_type"] == "message_sent"
    assert event["message_id"] == mid
    assert event["message_type"] == "private"
    # 私聊事件的 user_id 是对方（与收到私聊消息同一口径），机器人自己才在 self_id
    assert event["user_id"] == uid
    assert event["self_id"] == ob_ids.SELF.user_id()
    assert event["raw_message"] == "在的"
    assert event["sender"]["wxid"] == ob_ids.SELF.wxid()


def test_group_sent_message_carries_group_fields(monkeypatch, isolated_ids):
    """群里的出站存档带上群号与群名，客户端按群过滤时用得上。"""
    sender = FakeSender()
    gid, _uid = _prepare_group(monkeypatch, sender, [])
    monkeypatch.setattr(
        ob_protocol,
        "_groups_roster",
        lambda: [{"username": "50071592949@chatroom", "name": "测试群"}],
    )
    resp = _call(
        {
            "action": "send_group_msg",
            "params": {"group_id": gid, "message": [{"type": "text", "data": {"text": "到了"}}]},
        }
    )
    assert resp["status"] == "ok"
    got = _call({"action": "get_msg", "params": {"message_id": resp["data"]["message_id"]}})
    event = got["data"]
    assert event["message_type"] == "group"
    assert event["group_id"] == gid
    assert event["user_id"] == ob_ids.SELF.user_id()
    assert event["group_name"] == "测试群"


def test_failed_send_is_not_archived(monkeypatch, isolated_ids):
    """一段都没发出去时不留存档：存档里只应有真正送达的消息。"""
    sender = FakeSender(ok=False)
    uid = _prepare(monkeypatch, sender)
    store = ob_store.message_store()
    before = store.count()
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "text", "data": {"text": "hi"}}]},
        }
    )
    assert resp["status"] == "failed"
    # 存档为整场会话共用，这里看增量：一发都没出去就不该多出任何行
    assert store.count() == before
