"""OneBot ID 层：微信的字符串标识 ↔ OneBot 的 32 位整数 ID。

OneBot v11 里 ``user_id`` / ``group_id`` / ``self_id`` / ``message_id`` 都是整数，
微信那边却是 wxid、``xxx@chatroom`` 这类字符串。两者之间需要一份稳定、可反查、
不撞号的映射；这里把它分成两块：

- **联系人 ID**（``data/id_map.json``）：先查表，未命中就用 sha256 折叠出候选整数
  再线性探测一个空位落表。因此重启、换机器、改昵称都不会让某个人的 ID 变化，
  而且人可以直接打开这个文件查「这个数字是谁」。
- **消息 ID**（``data/msg_ids.db``）：SQLite 自增进来的 32 位正整数，按
  ``(会话, 本地库行号)`` 幂等分配。``delete_msg`` / ``get_msg`` 靠它回查原消息。

进程内另有 ``int → 原始标识`` 的反向缓存，只用于日志与调试：丢了可以从磁盘重建，
不进额外存储。

机器人自身身份（``SELF``）与「整数 ID → 显示名」的索引（``remember_name`` /
``lookup_name``）也放在这里：两者都是账号级的，换登录账号必须一起复位，而
OneBot 的 ``self_id`` 与发送时搜索用的名字本来就都从这一层取。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time

from . import config

log = logging.getLogger("flandora")

# OneBot 的 ID 是有符号 32 位整数，这里只用正数区间，0 留给「未设置」
ID_MIN = 1
ID_MAX = 2**31 - 1
_RANGE = ID_MAX - ID_MIN + 1
_NAMESPACE = "flandora:id:v1:"
# 线性探测的上限：折叠碰撞本就极小，遇到上千次连续占用说明表被写坏了
_PROBE_LIMIT = 1000


def fold_handle(handle: str) -> int:
    """把微信标识折叠成 32 位正整数候选值（确定性：同输入永远同输出）。"""
    digest = hashlib.sha256((_NAMESPACE + handle).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % _RANGE + ID_MIN


class ContactIds:
    """微信标识（wxid / 群 ID）→ 稳定整数 ID 的持久化映射。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._by_handle: dict[str, int] = {}
        self._by_id: dict[int, str] = {}
        self._load()

    # ---------------------------------------------------------------- 持久化
    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            log.error(f"[ID] 无法读取 {self.path}（{e}），本次运行将重新分配 ID")
            return
        if not isinstance(raw, dict) or not isinstance(raw.get("ids"), dict):
            log.error(f"[ID] {self.path} 结构不对，本次运行将重新分配 ID")
            return

        repaired = 0
        for handle, oid in raw["ids"].items():
            if not isinstance(oid, int) or isinstance(oid, bool) or not ID_MIN <= oid <= ID_MAX:
                log.error(f"[ID] {handle!r} 的 ID {oid!r} 不合法，已丢弃")
                repaired += 1
                continue
            if oid in self._by_id:
                # 文件被手改出重复 ID 时不能让两个人共用一个身份：后来者重新分配
                log.error(f"[ID] {handle!r} 与 {self._by_id[oid]!r} 撞号 {oid}，已重新分配")
                repaired += 1
                continue
            self._by_handle[handle] = oid
            self._by_id[oid] = handle
        if repaired:
            self.save()

    def save(self) -> None:
        """原子落盘（先写临时文件再替换），避免断电留下半个 JSON。"""
        with self._lock:
            payload = {
                "version": 1,
                "note": "微信标识 → OneBot 整数 ID。删掉这个文件会让所有 ID 重新分配。",
                "ids": dict(sorted(self._by_handle.items(), key=lambda kv: kv[1])),
            }
            tmp = f"{self.path}.tmp"
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                    fh.write("\n")
                os.replace(tmp, self.path)
            except OSError as e:
                log.error(f"[ID] 写入 {self.path} 失败：{e}")

    # ---------------------------------------------------------------- 查询与分配
    def get(self, handle: str) -> int:
        """取标识对应的整数 ID，没有就分配一个并落盘。"""
        if not isinstance(handle, str) or not handle:
            raise ValueError("联系人标识必须是非空字符串")
        with self._lock:
            known = self._by_handle.get(handle)
            if known is not None:
                return known
            candidate = fold_handle(handle)
            for _ in range(_PROBE_LIMIT):
                if candidate not in self._by_id:
                    self._by_handle[handle] = candidate
                    self._by_id[candidate] = handle
                    log.info(f"[ID] 新增映射 {handle} → {candidate}")
                    self.save()
                    return candidate
                candidate = candidate % ID_MAX + 1 if candidate < ID_MAX else ID_MIN
            raise RuntimeError(f"连续 {_PROBE_LIMIT} 个候选 ID 都被占用，{self.path} 可能已损坏")

    def handle(self, oid: int) -> str | None:
        """整数 ID 反查微信标识，查不到返回 None。"""
        with self._lock:
            return self._by_id.get(oid)

    def items(self) -> dict[str, int]:
        with self._lock:
            return dict(self._by_handle)

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_handle)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS msg_ids (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    talker     TEXT    NOT NULL,
    local_id   INTEGER,
    server_id  TEXT    NOT NULL DEFAULT '',
    outgoing   INTEGER NOT NULL DEFAULT 0,
    created    REAL    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_ids_talker_local
    ON msg_ids(talker, local_id) WHERE local_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_msg_ids_server
    ON msg_ids(server_id) WHERE server_id != '';
"""


class MessageIds:
    """message_id 发号表：按 (会话, 本地库行号) 幂等分配，支持回查。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ensure(
        self,
        talker: str,
        local_id: int,
        *,
        server_id: str = "",
        outgoing: bool = False,
    ) -> int:
        """按 (会话, 本地行号) 取已有 ID，没有就分配。同一行永远只对应一个 ID。"""
        if not isinstance(talker, str) or not talker:
            raise ValueError("会话标识必须是非空字符串")
        if not isinstance(local_id, int) or isinstance(local_id, bool):
            raise ValueError("本地行号必须是整数")
        with self._lock:
            row = self._conn.execute(
                "SELECT message_id FROM msg_ids WHERE talker = ? AND local_id = ?",
                (talker, local_id),
            ).fetchone()
            if row is not None:
                return int(row["message_id"])
            cur = self._conn.execute(
                "INSERT INTO msg_ids (talker, local_id, server_id, outgoing, created)"
                " VALUES (?, ?, ?, ?, ?)",
                (talker, local_id, server_id, int(bool(outgoing)), time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def allocate(self, talker: str, *, outgoing: bool = True) -> int:
        """给一条不在本地库里的消息（自己发出的）发一个新号。"""
        if not isinstance(talker, str) or not talker:
            raise ValueError("会话标识必须是非空字符串")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO msg_ids (talker, local_id, server_id, outgoing, created)"
                " VALUES (?, NULL, '', ?, ?)",
                (talker, int(bool(outgoing)), time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def message_by_server(self, server_id: str) -> int | None:
        """按微信服务端消息号回查 message_id。

        引用消息的 `<refermsg><svrid>` 就是被引消息的服务端号，收到引用时
        靠它把 `reply` 段还原成真正见过的 message_id；没见过就返回 None。
        """
        if not server_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT message_id FROM msg_ids WHERE server_id = ?"
                " ORDER BY message_id DESC LIMIT 1",
                (str(server_id),),
            ).fetchone()
        return int(row["message_id"]) if row is not None else None

    def lookup(self, message_id: int) -> dict | None:
        """按 message_id 回查归属（会话、本地行号、方向）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT message_id, talker, local_id, server_id, outgoing, created"
                " FROM msg_ids WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "message_id": int(row["message_id"]),
            "talker": row["talker"],
            "local_id": row["local_id"],
            "server_id": row["server_id"],
            "outgoing": bool(row["outgoing"]),
            "created": row["created"],
        }

    def __len__(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM msg_ids").fetchone()[0])


# ---------------------------------------------------------------- 模块级单例
# 懒加载：import 时不碰磁盘，测试可以把 data 目录指到临时位置。

_contact_ids: ContactIds | None = None
_message_ids: MessageIds | None = None
_singleton_lock = threading.Lock()


def contact_ids() -> ContactIds:
    global _contact_ids
    with _singleton_lock:
        if _contact_ids is None:
            _contact_ids = ContactIds(os.path.join(config.data_dir(), "id_map.json"))
        return _contact_ids


def message_ids() -> MessageIds:
    global _message_ids
    with _singleton_lock:
        if _message_ids is None:
            _message_ids = MessageIds(os.path.join(config.data_dir(), "msg_ids.db"))
        return _message_ids


def contact_id(handle: str) -> int:
    """微信标识 → OneBot 整数 ID。"""
    return contact_ids().get(handle)


def handle_of(oid: int) -> str | None:
    """OneBot 整数 ID → 微信标识。"""
    return contact_ids().handle(oid)


def ensure_message_id(talker: str, local_id: int, **kwargs) -> int:
    return message_ids().ensure(talker, local_id, **kwargs)


def allocate_message_id(talker: str, **kwargs) -> int:
    return message_ids().allocate(talker, **kwargs)


# ---------------------------------------------------------------- 机器人身份


class SelfIdentity:
    """机器人自身身份：登录账号的 wxid、OneBot ``self_id`` 与昵称缓存。

    三者必须一起变：沿用上一个账号的 ``user_id`` 或昵称，事件里的 ``self_id``
    与 ``get_login_info`` 就会指错人，客户端据此回消息会发到错的地方。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wxid = ""
        self._user_id = 0
        self._nickname = ""

    def set_wxid(self, wxid: str) -> int:
        """按登录账号重设身份：重新分配 ``user_id`` 并作废昵称缓存，返回新 ``user_id``。"""
        handle = str(wxid or "")
        oid = contact_id(handle) if handle else 0
        with self._lock:
            self._wxid = handle
            self._user_id = oid
            self._nickname = ""
            return oid

    def user_id(self) -> int:
        with self._lock:
            return self._user_id

    def wxid(self) -> str:
        with self._lock:
            return self._wxid

    def nickname(self) -> str:
        """缓存的昵称；空串表示还没解析过。"""
        with self._lock:
            return self._nickname

    def set_nickname(self, name: str) -> None:
        with self._lock:
            self._nickname = str(name or "")


SELF = SelfIdentity()


# ---------------------------------------------------------------- 名字索引
# OneBot 整数 ID → 微信里显示的联系名/群名。收件链路写入、协议与面板读取，
# 因此读写都过同一把锁；对外只给拷贝，遍历期间不会被写坏。

_names: dict[int, str] = {}
_names_lock = threading.Lock()


def remember_name(ob_id: int, name: str) -> None:
    """记下某个 OneBot ID 的显示名（覆盖旧值）。"""
    with _names_lock:
        _names[int(ob_id)] = str(name or "")


def lookup_name(ob_id: int) -> str:
    """取显示名；没记过返回空串。"""
    with _names_lock:
        return _names.get(int(ob_id), "")


def name_snapshot() -> dict[int, str]:
    """名字索引的拷贝：调用方遍历时桥接线程仍可写入。"""
    with _names_lock:
        return dict(_names)


def clear_names() -> None:
    """清空名字索引（切换登录账号或测试换数据目录时用）。"""
    with _names_lock:
        _names.clear()


def reset_singletons() -> None:
    """丢弃进程内单例与账号级索引（切换登录账号或测试换目录时用）。"""
    global _contact_ids, _message_ids
    with _singleton_lock:
        if _message_ids is not None:
            try:
                _message_ids.close()
            except sqlite3.Error:
                pass
        _contact_ids = None
        _message_ids = None
    SELF.set_wxid("")
    clear_names()
