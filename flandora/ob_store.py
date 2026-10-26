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

log = logging.getLogger("flandora")

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


def _local_hour_start(ts: float) -> int:
    """本地时区下 ``ts`` 所在那一小时的起点（Unix 秒）。"""
    lt = time.localtime(ts)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, 0, 0, 0, 0, -1)))


class MessageStore:
    """收发两边的事件按 ``message_id`` 存档，可按 ID 取、按会话列、按文本搜。"""

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

    def stats(self) -> dict:
        """行数与时间跨度（秒）。老库可能为空，这时两端都是 0。"""
        with self._lock:
            row = self._con.execute(
                "SELECT COUNT(*) AS n, MIN(time) AS oldest, MAX(time) AS newest FROM messages"
            ).fetchone()
        return {
            "rows": int(row["n"] or 0),
            "oldest": int(row["oldest"] or 0),
            "newest": int(row["newest"] or 0),
        }

    # ---------- 面板统计（按 time 聚合，读的是长期事实） ----------

    def counts_since(self, since: float) -> dict:
        """``since`` 之后的条数：``events`` 是全部推送事件，``messages`` 只算消息类。

        面板的「今日消息」用它：消息类对应「接收消息」，全部对应「已推送事件」。
        """
        with self._lock:
            row = self._con.execute(
                "SELECT COUNT(*) AS events,"
                " SUM(CASE WHEN post_type = 'message' THEN 1 ELSE 0 END) AS messages"
                " FROM messages WHERE time >= ?",
                (int(since),),
            ).fetchone()
        return {"events": int(row["events"] or 0), "messages": int(row["messages"] or 0)}

    def hourly_messages_since(self, since: float) -> dict:
        """``since`` 之后按小时分桶的消息条数，键是那一小时的起点（本地时区，Unix 秒）。"""
        with self._lock:
            rows = self._con.execute(
                "SELECT time FROM messages WHERE time >= ? AND post_type = 'message'",
                (int(since),),
            ).fetchall()
        buckets: dict[int, int] = {}
        for row in rows:
            start = _local_hour_start(row["time"])
            buckets[start] = buckets.get(start, 0) + 1
        return buckets

    def active_chats_since(self, since: float, limit: int = 10) -> list:
        """``since`` 之后发消息最多的会话，多的在前。

        群按 ``group_id`` 归并、私聊按 ``user_id`` 归并（私聊取负号，与群 ID 不撞）。
        名称只取存档里写下的群名/昵称：查不到就是空串，由调用方拿 ``id`` 去名字
        索引里再问一次——存档不替上层决定显示什么。
        """
        limit = max(1, min(int(limit or 10), 100))
        with self._lock:
            rows = self._con.execute(
                "SELECT group_id, group_name, user_id, nickname FROM messages"
                " WHERE time >= ? AND post_type = 'message'",
                (int(since),),
            ).fetchall()
        chats: dict[int, dict] = {}
        for row in rows:
            group_id = int(row["group_id"] or 0)
            user_id = int(row["user_id"] or 0)
            key = group_id if group_id else -user_id
            item = chats.get(key)
            if item is None:
                name = str(row["group_name"] or "") if group_id else str(row["nickname"] or "")
                chats[key] = {"name": name, "id": group_id or user_id, "count": 1}
            else:
                item["count"] += 1
        return sorted(chats.values(), key=lambda c: (-c["count"], c["id"]))[:limit]

    # ---------- 清理（只由使用者显式触发，见模块说明） ----------

    def prune_before(self, cutoff: float) -> int:
        """删掉 ``time`` 早于 ``cutoff``（秒）的存档，返回删掉的条数。

        删掉的 ``message_id`` 就再也答不出原文了——客户端的撤回与引用会拿到空，
        所以这个动作必须由人明确按下来，不放进任何后台任务。
        注意删完文件不会自己变小，要回收空间得再调用 ``vacuum()``。
        """
        with self._lock:
            cur = self._con.execute("DELETE FROM messages WHERE time < ?", (int(cutoff),))
            self._con.commit()
            return int(cur.rowcount or 0)

    def clear(self) -> int:
        """清空整个存档，返回删掉的条数。"""
        with self._lock:
            cur = self._con.execute("DELETE FROM messages")
            self._con.commit()
            return int(cur.rowcount or 0)

    def vacuum(self) -> None:
        """重写库文件，把删掉的行腾出来的页还给系统。

        VACUUM 要拿排他锁并整库重写，大库上会卡一下，所以单独一个动作。
        """
        with self._lock:
            self._con.commit()
            self._con.execute("VACUUM")

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
