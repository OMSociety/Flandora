"""本地库收件的轮询逻辑：新会话出现时既不丢刚到的消息，也不重放历史。

真实的 ``WxLocalSource`` 读的是解密后的微信 SQLite；这里用一个内存替身把
「会话列表 + 每个会话的消息 + 显示名」喂进去，只验游标与展示名的处理。
"""

from __future__ import annotations

import time

from flandora.wx_source import PRIME_LIMIT, WxLocalSource


class _StubStore:
    """替身：会话、消息、显示名都由测试预置，``refresh()`` 只计数。"""

    def __init__(self, sessions=(), messages=None, names=None):
        self._sessions = [dict(s) for s in sessions]
        self._messages = {k: list(v) for k, v in (messages or {}).items()}
        self._names = dict(names or {})
        self.refreshed = 0

    # ---- WxLocalSource 用到的那几个方法 ----
    def refresh(self):
        self.refreshed += 1

    def sessions(self):
        return [dict(s) for s in self._sessions]

    def messages(self, username, after_local_id=0, limit=50):
        rows = [m for m in self._messages.get(username, []) if m["local_id"] > after_local_id]
        return [dict(m) for m in rows[:limit]]

    def display_names(self):
        return dict(self._names)

    def image_ready(self):
        return False

    # ---- 测试用的写入口 ----
    def add_session(self, username, last_local_id, is_group=False):
        self._sessions.append(
            {"username": username, "is_group": is_group, "last_local_id": last_local_id}
        )

    def add_message(self, username, local_id, create_time, text, outgoing=False):
        self._messages.setdefault(username, []).append(
            {
                "local_id": local_id,
                "server_id": "",
                "local_type": 1,
                "create_time": create_time,
                "sender": "wxid_other",
                "kind": "text",
                "text": text,
                "outgoing": outgoing,
            }
        )

    def remember(self, username, name):
        self._names[username] = name


def _source(store, backfill_seconds=0):
    src = WxLocalSource(store, backfill_seconds=backfill_seconds)
    src.started_at = int(time.time())
    return src


def test_existing_session_pushes_each_message_once():
    """常规路径：启动前的那条算历史，之后进来的每条只推一次。"""
    store = _StubStore([{"username": "wxid_a", "is_group": False, "last_local_id": 1}])
    store.add_message("wxid_a", 1, int(time.time()) - 5, "启动前的消息")
    src = _source(store)
    src.prime()
    assert src.poll() == []

    store.add_message("wxid_a", 2, int(time.time()), "进来一条")
    assert [m["content"] for m in src.poll()] == ["进来一条"]
    assert src.poll() == []


def test_new_session_keeps_the_message_that_made_it_appear():
    """跑起来之后才出现的会话：让它现身的那条消息要推出去。"""
    store = _StubStore()
    src = _source(store)
    src.prime()
    assert src.poll() == []

    store.add_session("wxid_new", 1)
    store.add_message("wxid_new", 1, int(time.time()), "你好呀")
    out = src.poll()
    assert [m["content"] for m in out] == ["你好呀"]
    assert out[0]["sessionId"] == "wxid_new"


def test_new_session_does_not_replay_history():
    """旧会话以新面孔出现（本地库重建、会话被删又恢复）：历史照旧不重放。"""
    store = _StubStore()
    src = _source(store)
    src.prime()

    old = src.started_at - 3600
    store.add_session("wxid_old", 3)
    for local_id in (1, 2, 3):
        store.add_message("wxid_old", local_id, old, f"旧消息{local_id}")
    assert src.poll() == []

    store.add_message("wxid_old", 4, int(time.time()), "新消息")
    assert [m["content"] for m in src.poll()] == ["新消息"]


def test_new_session_with_long_history_does_not_replay_it():
    """存量比一次回看的条数还多时，也不从头重放。"""
    store = _StubStore()
    src = _source(store)
    src.prime()

    old = src.started_at - 7200
    total = PRIME_LIMIT + 10
    store.add_session("wxid_big", total)
    for local_id in range(1, total + 1):
        store.add_message("wxid_big", local_id, old, f"旧消息{local_id}")
    assert src.poll() == []

    store.add_message("wxid_big", total + 1, int(time.time()), "新消息")
    assert [m["content"] for m in src.poll()] == ["新消息"]


def test_new_session_honours_the_backfill_window():
    """开了回填时，新会话也按同一个窗口把窗口内的消息补上。"""
    store = _StubStore()
    src = WxLocalSource(store, backfill_seconds=600)
    src.prime()

    now = int(time.time())
    store.add_session("wxid_new", 2)
    store.add_message("wxid_new", 1, now - 120, "窗口内的旧消息")
    store.add_message("wxid_new", 2, now - 1, "刚到的消息")
    assert [m["content"] for m in src.poll()] == ["窗口内的旧消息", "刚到的消息"]


def test_new_session_refreshes_display_names():
    """新会话的显示名要现取一次：启动时那份名单里还没有它。"""
    store = _StubStore(names={"wxid_a": "老朋友"})
    src = _source(store)
    src.prime()

    store.add_session("wxid_new", 1)
    store.add_message("wxid_new", 1, int(time.time()), "在吗")
    store.remember("wxid_new", "新朋友")
    assert [m["sourceName"] for m in src.poll()] == ["新朋友"]
