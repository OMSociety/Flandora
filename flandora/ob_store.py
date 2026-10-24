"""已推送事件的消息存档。

为什么要有这一层：``message_id`` 是客户端做撤回、引用、取原文的唯一凭据，
而它只在事件推送的那一刻有效——微信本地库里的行号会随微信自己的清理而消失，
内存里的事件更是重启即无。客户端过后拿 ``message_id`` 来问「这条是什么」，
桥接必须答得出来，所以推送出去的事件落一份 SQLite。

存档不自动清理：``message_id`` 一旦发给客户端就是长期契约，静默回收会让
老客户端的撤回与引用突然指向不存在的消息。要腾空间由使用者显式删库。

表结构
    messages
        message_id   主键，来自 ob_ids 的发号表
        time         事件时间（秒）
        post_type    事件类型（message / message_sent / notice …，为后续事件面预留）
        message_type group / private
        user_id      发言人（整数，经 ID 层映射）
        group_id     群（私聊为 0）
        nickname     发言人昵称
        group_name   群名（私聊为空）
        raw_message  CQ 码字符串
        message_json message 字段原文（数组或字符串，保持推送时的形态）
        event_json   整条事件的 JSON，取原文时按它还原
        created      落库时间（浮点秒）
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time

from . import config

log = logging.getLogger("ob11-bridge")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id   INTEGER PRIMARY KEY,
    time         INTEGER NOT NULL,
    post_type    TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT '',
    user_id      INTEGER NOT NULL DEFAULT 0,
    group_id     INTEGER NOT NULL DEFAULT 0,
    nickname     TEXT NOT NULL DEFAULT '',
    group_name   TEXT NOT NULL DEFAULT '',
    raw_message  TEXT NOT NULL DEFAULT '',
    message_json TEXT NOT NULL DEFAULT '[]',
    event_json   TEXT NOT NULL,
    created      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(time DESC);
CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id, message_id DESC);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, message_id DESC);
"""

_COLUMNS = (
    "message_id",
    "time",
    "post_type",
    "message_type",
    "user_id",
    "group_id",
    "nickname",
    "group_name",
    "raw_message",
    "message_json",
    "event_json",
    "created",
)


class MessageStore:
    """推送过的事件按 ``message_id`` 存档，可按 ID 取、按会话列、按文本搜。"""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.executescript(_SCHEMA)
            self._con.commit()

    # ---------- 写 ----------

    def save_event(self, event: dict) -> int | None:
        """存一条已推送的事件。

        没有合法 ``message_id`` 的事件（心跳、生命周期一类）不入库：它们不是
        客户端能用 ID 回查的东西。同一 ID 重复推送按覆盖处理——内容只会更完整，
        不会把旧存档顶掉成空。
        """
        message_id = event.get("message_id")
        if not isinstance(message_id, int) or message_id <= 0:
            return None
        sender = event.get("sender") or {}
        message = event.get("message", [])
        try:
            message_json = json.dumps(message, ensure_ascii=False)
            event_json = json.dumps(event, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            log.error("事件无法序列化，跳过存档（message_id=%s）：%s", message_id, e)
            return None
        row = (
            message_id,
            int(event.get("time") or time.time()),
            str(event.get("post_type") or ""),
            str(event.get("message_type") or ""),
            int(event.get("user_id") or 0),
            int(event.get("group_id") or 0),
            str(sender.get("nickname") or ""),
            str(event.get("group_name") or ""),
            str(event.get("raw_message") or ""),
            message_json,
            event_json,
            time.time(),
        )
        placeholders = ", ".join("?" * len(_COLUMNS))
        with self._lock:
            self._con.execute(
                f"INSERT OR REPLACE INTO messages ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                row,
            )
            self._con.commit()
        return message_id

    # ---------- 读 ----------

    def get(self, message_id: int) -> dict | None:
        """按 ``message_id`` 取回整条事件；没有就返回 None。"""
        with self._lock:
            row = self._con.execute(
                "SELECT event_json FROM messages WHERE message_id = ?", (int(message_id),)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["event_json"])
        except ValueError as e:
            log.error("存档损坏，无法还原 message_id=%s：%s", message_id, e)
            return None

    def recent(self, limit: int = 50, *, group_id: int = 0, user_id: int = 0) -> list[dict]:
        """最近的存档事件，新的在前。

        ``group_id`` / ``user_id`` 只可能是 ID 层发过的整数，0 表示不筛。
        """
        limit = max(1, min(int(limit or 50), 1000))
        sql = "SELECT event_json FROM messages"
        where = []
        args: list[int] = []
        if group_id:
            where.append("group_id = ?")
            args.append(int(group_id))
        if user_id:
            where.append("user_id = ?")
            args.append(int(user_id))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY message_id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._con.execute(sql, args).fetchall()
        out = []
        for row in rows:
            try:
                out.append(json.loads(row["event_json"]))
            except ValueError as e:
                log.error("存档损坏，跳过一条记录：%s", e)
        return out

    def search(self, keyword: str, limit: int = 50) -> list[dict]:
        """在 raw_message 里按子串搜，新的在前。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        limit = max(1, min(int(limit or 50), 1000))
        with self._lock:
            rows = self._con.execute(
                "SELECT event_json FROM messages WHERE raw_message LIKE ?"
                " ORDER BY message_id DESC LIMIT ?",
                (f"%{keyword}%", limit),
            ).fetchall()
        out = []
        for row in rows:
            try:
                out.append(json.loads(row["event_json"]))
            except ValueError:
                continue
        return out

    def count(self) -> int:
        with self._lock:
            row = self._con.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return int(row["n"])

    def close(self):
        with self._lock:
            self._con.close()


_store: MessageStore | None = None
_store_lock = threading.Lock()


def message_store() -> MessageStore:
    """进程内单例。路径固定为配置目录下的 messages.db。"""
    global _store
    with _store_lock:
        if _store is None:
            _store = MessageStore(os.path.join(config.data_dir(), "messages.db"))
        return _store


def reset_singletons():
    """丢弃单例并关连接。切账号或测试换目录时调用。"""
    global _store
    with _store_lock:
        if _store is not None:
            try:
                _store.close()
            except Exception as e:  # noqa: BLE001
                log.debug("关闭消息存档失败：%s", e)
            _store = None
