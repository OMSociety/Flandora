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
    "sender": "wxid_zhangsan",
    "senderName": "张三",
    "content": "今天下午最后谁都没来我就直接爆炸",
    "sessionType": "group",
    "localId": 4242,
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
            # 缓冲键用发言人 wxid：昵称改了不会把同一个人拆成两条事件
            assert keys == ["50071592949@chatroom_wxid_zhangsan"], keys
            entry = bridge.pending_buffers[keys[0]]
            assert entry["messages"] == [GROUP_MSG["content"]], entry["messages"]
            assert entry["is_group"] is True
            assert entry["sender_in_group"] == "张三", entry["sender_in_group"]
            assert entry["sender_wxid"] == "wxid_zhangsan", entry["sender_wxid"]
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


def test_pushed_event_carries_integer_ids(isolated_ids, monkeypatch):
    """事件里的 user_id / group_id / message_id 都是整数，且由 ID 层给号。"""
    bridge = _make_bridge()
    captured = []
    monkeypatch.setattr(bridge_core, "push_event", lambda ev: captured.append(ev) or 1)
    try:
        bridge.add_to_buffer(dict(GROUP_MSG))
        key = next(iter(bridge.pending_buffers))
        bridge.process_sender(key)

        assert len(captured) == 1, captured
        event = captured[0]
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
    finally:
        _drain(bridge)
