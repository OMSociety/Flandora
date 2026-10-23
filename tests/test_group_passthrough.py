"""验证：群消息全量上报，桥接不再替客户端决定"要不要回"。

背景：桥接曾自带 仅@回复/全部回复/批处理 三种模式，其中"仅@回复"会把没 @ 机器人的
群消息直接丢掉。回复策略属于客户端，桥接只负责如实上报，并附上 is_mentioned 标记。
这两个用例守住这条边界：没 @ 的消息必须进缓冲区（不被丢），@ 的判定必须准确。
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import bridge_core, config  # noqa: E402


GROUP_MSG = {
    "sessionId": "50071592949@chatroom",
    "sourceName": "AAA直播八卦二号间",
    "groupName": "AAA直播八卦二号间",
    "senderName": "张三",
    "content": "今天下午最后谁都没来我就直接爆炸",
    "sessionType": "group",
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


def test_group_message_without_mention_is_still_buffered():
    bridge = _make_bridge()
    try:
        bridge.add_to_buffer(dict(GROUP_MSG))
        with bridge.buffer_lock:
            keys = list(bridge.pending_buffers)
            assert keys == ["50071592949@chatroom_张三"], keys
            entry = bridge.pending_buffers[keys[0]]
            assert entry["messages"] == [GROUP_MSG["content"]], entry["messages"]
            assert entry["is_group"] is True
            assert entry["sender_in_group"] == "张三", entry["sender_in_group"]
    finally:
        _drain(bridge)


def test_is_mentioned_detects_only_real_mentions():
    bridge = _make_bridge()
    try:
        nick = config.BOT_NICKNAMES[0]
        assert bridge._is_mentioned({**GROUP_MSG, "content": f"@{nick} 在吗"}) is True
        assert bridge._is_mentioned({**GROUP_MSG, "content": f"＠{nick} 在吗"}) is True
        assert bridge._is_mentioned(dict(GROUP_MSG)) is False
    finally:
        _drain(bridge)
