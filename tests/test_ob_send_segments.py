"""发送链路的消息形态：CQ 码字符串、auto_escape、未支持段与只发引用段。

重点不是「能发出去」，而是**发不出去时必须报错**。此前未接线的段类型会被静默
跳过，客户端收到 ok 就以为发出去了，把内容记进上下文；这里逐条钉住失败语义。
"""

import asyncio
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import action_catalog, ob_ids, ob_protocol, state  # noqa: E402


class FakeSender:
    """只认 send_text 的发送器；send_image/其他一律当失败。"""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.texts: list[tuple[str, str]] = []
        self._last_error = ""

    def send_text(self, contact: str, text: str) -> bool:
        if self.ok:
            self.texts.append((contact, text))
        return self.ok


def _call(payload: dict) -> dict:
    return asyncio.run(ob_protocol._handle_ob_api(payload))


def _prepare(monkeypatch, sender) -> int:
    monkeypatch.setattr(state, "sender_instance", sender, raising=False)
    uid = ob_ids.contact_id("wxid_zhangsan")
    state._ob_id_to_contact[uid] = "张三"
    return uid


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


def test_unsupported_segment_reports_failure(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "video", "data": {"file": "x.mp4"}}]},
        }
    )
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_INTERNAL
    assert "video" in resp["message"]
    assert sender.texts == []


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


def test_partial_failure_keeps_message_id_and_counts_segments(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": uid,
                "message": [{"type": "text", "data": {"text": "hi"}}]
                + [{"type": "video", "data": {"file": "x.mp4"}}],
            },
        }
    )
    assert resp["status"] == "ok"
    assert resp["data"]["message_id"] > 0
    assert resp["data"]["failed_segments"] == 1


def test_invalid_message_shape_is_bad_param(monkeypatch, isolated_ids):
    sender = FakeSender()
    uid = _prepare(monkeypatch, sender)
    resp = _call(
        {"action": "send_private_msg", "params": {"user_id": uid, "message": [{"data": {}}]}}
    )
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
