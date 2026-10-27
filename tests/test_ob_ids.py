"""OneBot ID 层：稳定映射、幂等发号、坏文件自愈。"""

import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402

from flandora.ob_ids import (  # noqa: E402
    ID_MAX,
    ID_MIN,
    ContactIds,
    MessageIds,
)


def test_contact_id_is_stable_across_reload(tmp_path):
    path = str(tmp_path / "id_map.json")
    first = ContactIds(path)
    oid = first.get("wxid_alice")
    assert ID_MIN <= oid <= ID_MAX

    reloaded = ContactIds(path)
    assert reloaded.get("wxid_alice") == oid
    assert reloaded.handle(oid) == "wxid_alice"


def test_different_handles_get_different_ids(tmp_path):
    ids = ContactIds(str(tmp_path / "id_map.json"))
    alice = ids.get("wxid_alice")
    bob = ids.get("wxid_bob")
    assert alice != bob
    assert len(ids) == 2


def test_duplicate_ids_in_file_are_repaired(tmp_path, caplog):
    """手改出重复 ID 时，两个人不能共用一个身份。"""
    path = tmp_path / "id_map.json"
    path.write_text(
        json.dumps({"version": 1, "ids": {"wxid_alice": 42, "wxid_bob": 42}}), encoding="utf-8"
    )
    ids = ContactIds(str(path))
    alice = ids.get("wxid_alice")
    bob = ids.get("wxid_bob")
    assert alice == 42
    assert bob != 42
    # 修复结果已落盘，重新打开不会再看到撞号
    again = ContactIds(str(path))
    assert again.get("wxid_alice") == 42
    assert again.get("wxid_bob") == bob


def test_invalid_entries_are_dropped(tmp_path):
    path = tmp_path / "id_map.json"
    path.write_text(
        json.dumps({"version": 1, "ids": {"wxid_ok": 7, "wxid_bad": "not-an-int"}}),
        encoding="utf-8",
    )
    ids = ContactIds(str(path))
    assert ids.get("wxid_ok") == 7
    assert ids.handle(7) == "wxid_ok"
    assert ids.get("wxid_bad") != "not-an-int"


def test_corrupt_file_does_not_crash_startup(tmp_path):
    path = tmp_path / "id_map.json"
    path.write_text("{not json", encoding="utf-8")
    ids = ContactIds(str(path))
    assert ID_MIN <= ids.get("wxid_alice") <= ID_MAX


def test_empty_handle_is_rejected(tmp_path):
    ids = ContactIds(str(tmp_path / "id_map.json"))
    with pytest.raises(ValueError):
        ids.get("")


def test_resolve_handle_accepts_both_shapes(isolated_ids):
    """客户端可以给整数 ID，也可以直接给微信标识。"""
    oid = isolated_ids.contact_id("wxid_zhangsan")
    assert isolated_ids.resolve_handle(oid) == "wxid_zhangsan"
    assert isolated_ids.resolve_handle(str(oid)) == "wxid_zhangsan"
    assert isolated_ids.resolve_handle("wxid_zhangsan") == "wxid_zhangsan"
    # 没见过的标识原样返回：还没打过照面的好友也能按 wxid 发消息
    assert isolated_ids.resolve_handle("wxid_never_seen") == "wxid_never_seen"


def test_resolve_handle_rejects_unknown_id(isolated_ids):
    """没分配过的整数 ID 解析不出来：调用方要如实报错，不能拿这个数字去微信里搜。"""
    free = next(n for n in range(1, 5000) if isolated_ids.handle_of(n) is None)
    assert isolated_ids.resolve_handle(free) is None
    assert isolated_ids.resolve_handle(str(free)) is None


def test_resolve_handle_prefers_known_numeric_handle(isolated_ids):
    """微信号可以全是数字：表里已有的标识优先于同名的整数 ID。"""
    isolated_ids.contact_id("12345")
    assert isolated_ids.resolve_handle("12345") == "12345"


def test_resolve_handle_rejects_bad_shapes(isolated_ids):
    for value in (None, True, "", "   ", [], 0):
        assert isolated_ids.resolve_handle(value) is None


def test_find_contact_id_does_not_allocate(isolated_ids):
    assert isolated_ids.find_contact_id("wxid_nobody") is None
    assert isolated_ids.contact_ids().items() == {}


def test_message_id_ensure_is_idempotent(tmp_path):
    ids = MessageIds(str(tmp_path / "msg.db"))
    try:
        first = ids.ensure("123@chatroom", 1001)
        second = ids.ensure("123@chatroom", 1001)
        assert first == second
        assert 0 < first < 2**31

        other = ids.ensure("123@chatroom", 1002)
        assert other != first

        # 不同会话可以用同一个本地行号，得到不同 message_id
        other_talker = ids.ensure("wxid_bob", 1001)
        assert other_talker not in (first, other)
    finally:
        ids.close()


def test_message_id_lookup_round_trip(tmp_path):
    ids = MessageIds(str(tmp_path / "msg.db"))
    try:
        mid = ids.ensure("123@chatroom", 55, server_id="srv-1", outgoing=False)
        row = ids.lookup(mid)
        assert row is not None
        assert row["talker"] == "123@chatroom"
        assert row["local_id"] == 55
        assert row["server_id"] == "srv-1"
        assert row["outgoing"] is False
        assert ids.lookup(999999) is None
    finally:
        ids.close()


def test_message_id_allocate_for_outgoing(tmp_path):
    ids = MessageIds(str(tmp_path / "msg.db"))
    try:
        mid = ids.allocate("wxid_bob")
        row = ids.lookup(mid)
        assert row is not None
        assert row["local_id"] is None
        assert row["outgoing"] is True
        assert len(ids) == 1
    finally:
        ids.close()


def test_message_ids_survive_reopen(tmp_path):
    path = str(tmp_path / "msg.db")
    ids = MessageIds(path)
    mid = ids.ensure("wxid_bob", 7)
    ids.close()

    again = MessageIds(path)
    try:
        assert again.ensure("wxid_bob", 7) == mid
    finally:
        again.close()


def test_message_ids_reject_bad_input(tmp_path):
    ids = MessageIds(str(tmp_path / "msg.db"))
    try:
        with pytest.raises(ValueError):
            ids.ensure("", 1)
        with pytest.raises(ValueError):
            ids.ensure("wxid_bob", "1")
        with pytest.raises(ValueError):
            ids.allocate("")
    finally:
        ids.close()


def test_outgoing_ids_bind_to_server_ids_in_order(tmp_path):
    """自己发出的号按发出顺序绑到服务端号：客户端引用机器人消息时靠它回查。"""
    ids = MessageIds(str(tmp_path / "msg.db"))
    try:
        now = time.time()
        first = ids.allocate("wxid_bob")
        second = ids.allocate("wxid_bob")
        assert ids.bind_pending_outgoing("wxid_bob", "srv-1", now) == first
        assert ids.bind_pending_outgoing("wxid_bob", "srv-2", now) == second
        assert ids.message_by_server("srv-1") == first
        assert ids.message_by_server("srv-2") == second
        # 两个号都绑完了，没得绑返回 None；别的会话不会捡走这条
        assert ids.bind_pending_outgoing("wxid_bob", "srv-3", now) is None
        assert ids.bind_pending_outgoing("wxid_alice", "srv-1", now) is None
    finally:
        ids.close()


def test_stale_or_foreign_ids_are_not_bound(tmp_path):
    """隔得太远的、收件消息的号、坏参数，都不吃绑定。"""
    ids = MessageIds(str(tmp_path / "msg.db"))
    try:
        stale = ids.allocate("wxid_bob")
        # 本地库回读到的这条与发出时刻差了整整一天：不认领，免得绑到别的消息身上
        assert ids.bind_pending_outgoing("wxid_bob", "srv-late", time.time() - 86400) is None
        assert ids.lookup(stale)["server_id"] == ""
        # 刚刚发出的那条还在，按它自己的时刻就绑得上
        assert ids.bind_pending_outgoing("wxid_bob", "srv-now", time.time()) == stale

        incoming = ids.ensure("wxid_bob", 9, server_id="srv-in", outgoing=False)
        assert ids.bind_pending_outgoing("wxid_bob", "srv-x", time.time()) is None
        assert ids.lookup(incoming)["server_id"] == "srv-in"

        assert ids.bind_pending_outgoing("", "srv-y", time.time()) is None
        assert ids.bind_pending_outgoing("wxid_bob", "", time.time()) is None
        assert ids.bind_pending_outgoing("wxid_bob", "srv-z", 0.0) is None
    finally:
        ids.close()


def test_module_level_bind_takes_the_send_time(isolated_ids):
    """桥接按位置传发出时刻：模块级包装的参数要与 MessageIds 同名同序。"""
    mid = isolated_ids.allocate_message_id("wxid_bob")
    assert isolated_ids.bind_pending_outgoing("wxid_bob", "srv-1", time.time()) == mid
    assert isolated_ids.message_by_server("srv-1") == mid
