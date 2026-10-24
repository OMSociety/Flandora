"""消息存档：推送过的事件按 message_id 落库，供 get_msg 与历史检索取回。

测试分两层：一层直接打 ``MessageStore``（存档语义、覆盖、筛选、损坏行），
一层经 ``_handle_ob_api`` 打 ``get_msg``（找不到要回 1406 而不是空对象）。
"""

import asyncio
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import action_catalog, ob_store  # noqa: E402


def _event(mid: int, *, group: int = 0, user: int = 7, text: str = "你好") -> dict:
    return {
        "time": 1750000000 + mid,
        "self_id": 1,
        "post_type": "message",
        "message_type": "group" if group else "private",
        "message_id": mid,
        "user_id": user,
        "group_id": group,
        "group_name": "测试群" if group else "",
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "sender": {"user_id": user, "nickname": "张三"},
    }


def _call(payload: dict) -> dict:
    from flandora import ob_protocol

    return asyncio.run(ob_protocol._handle_ob_api(payload))


def _store(tmp_path) -> ob_store.MessageStore:
    return ob_store.MessageStore(str(tmp_path / "messages.db"))


def test_save_and_get_roundtrip(tmp_path):
    store = _store(tmp_path)
    assert store.save_event(_event(11)) == 11
    got = store.get(11)
    assert got is not None
    assert got["message_id"] == 11
    assert got["raw_message"] == "你好"
    assert got["message"] == [{"type": "text", "data": {"text": "你好"}}]
    assert store.count() == 1
    store.close()


def test_events_without_message_id_are_skipped(tmp_path):
    store = _store(tmp_path)
    assert store.save_event({"post_type": "meta_event", "time": 1}) is None
    assert store.save_event({"post_type": "message", "message_id": 0}) is None
    assert store.save_event({"post_type": "message", "message_id": -3}) is None
    assert store.count() == 0
    store.close()


def test_same_id_is_replaced_not_duplicated(tmp_path):
    store = _store(tmp_path)
    store.save_event(_event(5, text="旧"))
    store.save_event(_event(5, text="新"))
    assert store.count() == 1
    assert store.get(5)["raw_message"] == "新"
    store.close()


def test_recent_orders_newest_first_and_respects_filters(tmp_path):
    store = _store(tmp_path)
    for mid in range(1, 6):
        store.save_event(_event(mid, group=100 if mid % 2 else 0))
    ids = [e["message_id"] for e in store.recent(10)]
    assert ids == [5, 4, 3, 2, 1]
    assert [e["message_id"] for e in store.recent(2)] == [5, 4]
    grouped = [e["message_id"] for e in store.recent(10, group_id=100)]
    assert grouped == [5, 3, 1]
    private = [e["message_id"] for e in store.recent(10, user_id=7)]
    assert len(private) == 5
    store.close()


def test_search_matches_raw_message(tmp_path):
    store = _store(tmp_path)
    store.save_event(_event(1, text="今晚开会"))
    store.save_event(_event(2, text="明天见"))
    hits = store.search("开会")
    assert [e["message_id"] for e in hits] == [1]
    assert store.search("   ") == []
    store.close()


def test_corrupt_row_is_skipped(tmp_path):
    store = _store(tmp_path)
    store.save_event(_event(1))
    with sqlite3.connect(tmp_path / "messages.db") as con:
        con.execute("UPDATE messages SET event_json = '{oops' WHERE message_id = 1")
    assert store.get(1) is None
    assert store.recent(10) == []
    store.close()


def test_store_survives_reopen(tmp_path):
    first = _store(tmp_path)
    first.save_event(_event(9))
    first.close()
    second = ob_store.MessageStore(str(tmp_path / "messages.db"))
    assert second.get(9)["message_id"] == 9
    second.close()


def test_get_msg_returns_stored_event(tmp_path, isolated_store):
    ob_store.message_store().save_event(_event(42, group=100, text="原文"))
    resp = _call({"action": "get_msg", "params": {"message_id": 42}, "echo": "e1"})
    assert resp["status"] == "ok"
    assert resp["retcode"] == action_catalog.RETCODE_OK
    assert resp["data"]["message_id"] == 42
    assert resp["data"]["raw_message"] == "原文"
    assert resp["echo"] == "e1"


def test_get_msg_unknown_id_is_not_found(isolated_store):
    resp = _call({"action": "get_msg", "params": {"message_id": 987654}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_NOT_FOUND


def test_get_msg_requires_integer_id(isolated_store):
    resp = _call({"action": "get_msg", "params": {"message_id": "abc"}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM


def test_push_event_archives(isolated_store, monkeypatch):
    from flandora import ob_protocol

    pushed = []
    monkeypatch.setattr(ob_protocol, "push_event_broadcast", lambda e: pushed.append(e) or 1)
    assert ob_protocol.push_event(_event(77)) == 1
    assert pushed and pushed[0]["message_id"] == 77
    assert ob_store.message_store().get(77)["message_id"] == 77


def test_archive_failure_does_not_block_push(isolated_store, monkeypatch):
    from flandora import ob_protocol

    class Boom:
        def save_event(self, event):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ob_store, "message_store", lambda: Boom())
    pushed = []
    monkeypatch.setattr(ob_protocol, "push_event_broadcast", lambda e: pushed.append(e) or 1)
    assert ob_protocol.push_event(_event(78)) == 1
    assert pushed[0]["message_id"] == 78
