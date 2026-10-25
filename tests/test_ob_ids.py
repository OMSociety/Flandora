"""OneBot ID 层：稳定映射、幂等发号、坏文件自愈。"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402

from flandora.ob_ids import (  # noqa: E402
    ID_MAX,
    ID_MIN,
    ContactIds,
    MessageIds,
    fold_handle,
)


def test_fold_is_deterministic_and_in_range():
    a = fold_handle("wxid_abc123")
    b = fold_handle("wxid_abc123")
    assert a == b
    assert ID_MIN <= a <= ID_MAX
    assert fold_handle("wxid_other") != a


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


def test_id_map_json_is_human_readable(tmp_path):
    path = tmp_path / "id_map.json"
    ids = ContactIds(str(path))
    oid = ids.get("wxid_alice")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ids"]["wxid_alice"] == oid


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
