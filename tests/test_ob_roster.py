"""名册类查询动作：好友、群、群成员。

这些动作的数据来自微信本地 `contact.db`，所以测试造一个同结构的小库，
再把它当作「已解密的 contact.db」塞给真的 `WxStore`。断言两件事：
筛选规则（群、公众号、文件传输助手、自己、已删好友都不算好友），
以及查不到时回 ``RETCODE_NOT_FOUND`` 而不是空对象——空对象会让客户端
以为「这个人存在但没有资料」。
"""

import asyncio
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import action_catalog, ob_ids, ob_protocol, wx_store  # noqa: E402

SCHEMA = """
CREATE TABLE contact (
    id INTEGER PRIMARY KEY,
    username TEXT,
    local_type INTEGER DEFAULT 0,
    alias TEXT DEFAULT '',
    flag INTEGER DEFAULT 0,
    delete_flag INTEGER DEFAULT 0,
    remark TEXT DEFAULT '',
    nick_name TEXT DEFAULT '',
    big_head_url TEXT DEFAULT '',
    description TEXT DEFAULT ''
);
CREATE TABLE chat_room (
    id INTEGER PRIMARY KEY,
    username TEXT,
    owner TEXT
);
CREATE TABLE chat_room_info_detail (
    room_id_ INTEGER,
    username_ TEXT,
    announcement_ TEXT DEFAULT ''
);
CREATE TABLE chatroom_member (
    room_id INTEGER,
    member_id INTEGER
);
"""

# (id, username, alias, flag, delete_flag, remark, nick_name, big_head_url)
CONTACTS = [
    (1, "wxid_self", "", 0, 0, "", "我", ""),
    (2, "wxid_zhangsan", "", 4, 0, "", "张三", "https://example.invalid/1.jpg"),
    (3, "wxid_lisi", "lisi888", 6, 0, "老李", "李四", "https://example.invalid/2.jpg"),
    (4, "wxid_deleted", "", 0, 1, "", "已删好友", ""),
    (5, "gh_news", "", 0, 0, "", "某公众号", ""),
    (6, "filehelper", "", 0, 0, "", "文件传输助手", ""),
    (7, "wxid_wangwu", "", 0, 0, "", "王五", ""),
    (10, "111@chatroom", "", 0, 0, "", "测试群", ""),
    (11, "222@chatroom", "", 0, 0, "", "二群", ""),
]

GROUPS = [(10, "111@chatroom", "wxid_zhangsan"), (11, "222@chatroom", "wxid_lisi")]

GROUP_DETAIL = [(10, "111@chatroom", "今晚八点开会")]

MEMBERS = [(10, 2), (10, 3), (10, 7), (11, 2)]


def _make_store(tmp_path) -> wx_store.WxStore:
    work = tmp_path / "work"
    work.mkdir()
    db = work / "contact.db"
    con = sqlite3.connect(db)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO contact (id, username, alias, flag, delete_flag, remark,"
            " nick_name, big_head_url) VALUES (?,?,?,?,?,?,?,?)",
            CONTACTS,
        )
        con.executemany("INSERT INTO chat_room VALUES (?,?,?)", GROUPS)
        con.executemany("INSERT INTO chat_room_info_detail VALUES (?,?,?)", GROUP_DETAIL)
        con.executemany("INSERT INTO chatroom_member VALUES (?,?)", MEMBERS)
        con.commit()
    finally:
        con.close()

    store = wx_store.WxStore(
        data_dir=str(tmp_path / "wx"),
        passphrase=b"",
        work_dir=str(work),
        my_wxid="wxid_self",
    )
    store._decrypted.add("contact.db")
    return store


def _call(payload: dict) -> dict:
    return asyncio.run(ob_protocol._handle_ob_api(payload))


def _wire(monkeypatch, store):
    monkeypatch.setattr(ob_protocol, "_store", lambda: store)


def test_contacts_exclude_groups_official_accounts_and_self(tmp_path):
    store = _make_store(tmp_path)
    names = {c["username"] for c in store.contacts()}
    assert names == {"wxid_zhangsan", "wxid_lisi", "wxid_wangwu"}


def test_contacts_prefer_remark_and_expose_starred(tmp_path):
    store = _make_store(tmp_path)
    by_name = {c["username"]: c for c in store.contacts()}
    assert by_name["wxid_lisi"]["name"] == "老李"
    assert by_name["wxid_lisi"]["remark"] == "老李"
    assert by_name["wxid_lisi"]["nick_name"] == "李四"
    assert by_name["wxid_lisi"]["starred"] is True
    assert by_name["wxid_zhangsan"]["name"] == "张三"
    assert by_name["wxid_zhangsan"]["starred"] is False


def test_groups_carry_owner_member_count_and_announcement(tmp_path):
    store = _make_store(tmp_path)
    by_name = {g["username"]: g for g in store.groups()}
    assert set(by_name) == {"111@chatroom", "222@chatroom"}
    assert by_name["111@chatroom"]["name"] == "测试群"
    assert by_name["111@chatroom"]["member_count"] == 3
    assert by_name["111@chatroom"]["owner"] == "wxid_zhangsan"
    assert by_name["111@chatroom"]["announcement"] == "今晚八点开会"
    assert by_name["222@chatroom"]["member_count"] == 1
    assert by_name["222@chatroom"]["announcement"] == ""


def test_group_members_mark_owner(tmp_path):
    store = _make_store(tmp_path)
    members = store.group_members("111@chatroom")
    roles = {m["username"]: m["role"] for m in members}
    assert roles == {
        "wxid_zhangsan": "owner",
        "wxid_lisi": "member",
        "wxid_wangwu": "member",
    }
    assert store.group_members("999@chatroom") == []


def test_friend_list_returns_integer_ids(tmp_path, monkeypatch, isolated_ids):
    _wire(monkeypatch, _make_store(tmp_path))
    resp = _call({"action": "get_friend_list", "params": {}, "echo": "f1"})
    assert resp["status"] == "ok"
    rows = resp["data"]
    assert len(rows) == 3
    for row in rows:
        assert isinstance(row["user_id"], int) and row["user_id"] > 0
        assert ob_ids.handle_of(row["user_id"]).startswith("wxid_")
    by_handle = {ob_ids.handle_of(r["user_id"]): r for r in rows}
    assert by_handle["wxid_lisi"]["nickname"] == "李四"
    assert by_handle["wxid_lisi"]["remark"] == "老李"


def test_group_list_returns_member_counts(tmp_path, monkeypatch, isolated_ids):
    _wire(monkeypatch, _make_store(tmp_path))
    resp = _call({"action": "get_group_list", "params": {}, "echo": "g1"})
    assert resp["status"] == "ok"
    rows = {ob_ids.handle_of(g["group_id"]): g for g in resp["data"]}
    assert rows["111@chatroom"]["group_name"] == "测试群"
    assert rows["111@chatroom"]["member_count"] == 3
    assert rows["111@chatroom"]["max_member_count"] == 3


def test_group_info_and_member_info(tmp_path, monkeypatch, isolated_ids):
    _wire(monkeypatch, _make_store(tmp_path))
    gid = ob_ids.contact_id("111@chatroom")

    info = _call({"action": "get_group_info", "params": {"group_id": gid}, "echo": "i1"})
    assert info["status"] == "ok"
    assert info["data"]["group_name"] == "测试群"
    assert info["data"]["member_count"] == 3

    uid = ob_ids.contact_id("wxid_zhangsan")
    member = _call(
        {
            "action": "get_group_member_info",
            "params": {"group_id": gid, "user_id": uid},
            "echo": "i2",
        }
    )
    assert member["status"] == "ok"
    assert member["data"]["role"] == "owner"
    assert member["data"]["nickname"] == "张三"

    listed = _call({"action": "get_group_member_list", "params": {"group_id": gid}, "echo": "i3"})
    assert listed["status"] == "ok"
    assert len(listed["data"]) == 3
    assert {m["role"] for m in listed["data"]} == {"owner", "member"}


def test_stranger_info_reads_contact(tmp_path, monkeypatch, isolated_ids):
    _wire(monkeypatch, _make_store(tmp_path))
    uid = ob_ids.contact_id("wxid_wangwu")
    resp = _call({"action": "get_stranger_info", "params": {"user_id": uid}, "echo": "s1"})
    assert resp["status"] == "ok"
    assert resp["data"]["nickname"] == "王五"
    assert resp["data"]["sex"] == "unknown"


def test_unknown_ids_report_not_found(tmp_path, monkeypatch, isolated_ids):
    _wire(monkeypatch, _make_store(tmp_path))
    # 没经过 ID 层分配过的整数：映射表里查不到
    stranger = _call({"action": "get_stranger_info", "params": {"user_id": 123456789}})
    assert stranger["status"] == "failed"
    assert stranger["retcode"] == action_catalog.RETCODE_NOT_FOUND

    group = _call({"action": "get_group_info", "params": {"group_id": 123456789}})
    assert group["status"] == "failed"
    assert group["retcode"] == action_catalog.RETCODE_NOT_FOUND

    # 已知的群 + 不在这个群里的人
    gid = ob_ids.contact_id("111@chatroom")
    outsider = ob_ids.contact_id("wxid_outsider")
    member = _call(
        {"action": "get_group_member_info", "params": {"group_id": gid, "user_id": outsider}}
    )
    assert member["status"] == "failed"
    assert member["retcode"] == action_catalog.RETCODE_NOT_FOUND


def test_roster_is_empty_when_store_missing(monkeypatch, isolated_ids):
    monkeypatch.setattr(ob_protocol, "_store", lambda: None)
    resp = _call({"action": "get_friend_list", "params": {}})
    assert resp["status"] == "ok"
    assert resp["data"] == []
