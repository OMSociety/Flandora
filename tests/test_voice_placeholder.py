"""验证：语音按占位文本上报，桥接不转写、不自行消化。

背景：桥接曾调阿里云百炼 Fun-ASR 把语音转成文字再上报，属于替客户端做决定；而且本机微信
4.x 并不把语音落成可读文件（msg/attach 下只有 Img），音频本体根本拿不到。现在语音只作为
"[语音]" 占位文本进入普通缓冲，客户端据此知道有人发过语音，转写策略由客户端定。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import bridge_core  # noqa: E402

VOICE_MSG = {
    "sessionId": "wxid_peer_001",
    "sourceName": "斯兰德Slandre",
    "senderName": "斯兰德Slandre",
    "content": "[语音]",
    "sessionType": "single",
}


def _make_bridge():
    return bridge_core.Flandora(sender=lambda *a, **k: None)


def _drain(bridge):
    with bridge.buffer_lock:
        entries = list(bridge.pending_buffers.values())
        bridge.pending_buffers.clear()
    for entry in entries:
        timer = entry.get("timer")
        if timer is not None:
            timer.cancel()


def test_voice_goes_through_plain_text_buffer():
    bridge = _make_bridge()
    try:
        bridge.add_to_buffer(dict(VOICE_MSG))
        with bridge.buffer_lock:
            keys = list(bridge.pending_buffers)
            assert keys == ["wxid_peer_001"], keys
            entry = bridge.pending_buffers[keys[0]]
            assert entry["messages"] == ["[语音]"], entry["messages"]
            assert entry.get("image_segments") is None, entry.get("image_segments")
    finally:
        _drain(bridge)


def test_voice_is_not_treated_as_ignored():
    bridge = _make_bridge()
    try:
        assert bridge.should_ignore(dict(VOICE_MSG)) is False
    finally:
        _drain(bridge)


def test_asr_entrypoints_are_gone():
    for name in (
        "process_audio_message",
        "transcribe_audio_via_fun_asr",
        "_dispatch_message_to_buffer",
    ):
        assert not hasattr(bridge_core.Flandora, name), f"{name} 应当已删除"
