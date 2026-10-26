"""验证：语音按占位文本上报，桥接不转写、不自行消化。

本机微信 4.x 不把语音落成可读文件（msg/attach 下只有 Img），音频本体拿不到。所以语音只作为
"[语音]" 占位文本走普通上报链路，客户端据此知道有人发过语音，转写策略由客户端定。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import bridge_core  # noqa: E402

VOICE_MSG = {
    "sessionId": "wxid_peer_001",
    "sourceName": "示例成员A",
    "senderName": "示例成员A",
    "content": "[语音]",
    "sessionType": "single",
}


def _make_bridge():
    return bridge_core.Flandora(sender=lambda *a, **k: None)


def test_voice_goes_through_plain_report(isolated_ids, monkeypatch):
    bridge = _make_bridge()
    pushed = []
    monkeypatch.setattr(bridge_core, "push_event", lambda ev: pushed.append(ev) or 1)

    bridge.emit_message(dict(VOICE_MSG))

    assert len(pushed) == 1, pushed
    event = pushed[0]
    assert event["message_type"] == "private"
    assert [seg["type"] for seg in event["message"]] == ["text"]
    assert event["message"][0]["data"]["text"] == "[语音]"


def test_voice_is_not_treated_as_ignored():
    bridge = _make_bridge()
    assert bridge.should_ignore(dict(VOICE_MSG)) is False
