"""本地库的解密判据：文件没动就不重解，动了才解。

真实的库是加密的，这里只放可 stat 的占位文件，并用替身换掉 ``decrypt_to_file``，
验的是「同一份文件被跳过」与「改动后会重解」。另有一组用例盯 WAL：新行先写进
``-wal``、主库要等检查点才更新，只 stat 主库会让刚到的图片行长期不可见。
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from flandora import wx_crypto, wx_store

MD5 = "d" * 32

DBS = (
    ("session", "session.db"),
    ("message", "message_0.db"),
    ("message", "message_resource.db"),
    ("contact", "contact.db"),
    ("hardlink", "hardlink.db"),
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """造出五个库的占位文件，并记录每次解密请求。"""
    data_dir = tmp_path / "wx_files"
    work_dir = tmp_path / "wx_cache"
    for sub, name in DBS:
        target = data_dir / "db_storage" / sub / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"encrypted-" + name.encode())

    decrypted: list[str] = []

    def fake_decrypt_to_file(passphrase, src, dst):
        decrypted.append(os.path.basename(src))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(b"plain")
        return 5

    monkeypatch.setattr(wx_crypto, "decrypt_to_file", fake_decrypt_to_file)
    return wx_store.WxStore(str(data_dir), b"pass", str(work_dir)), decrypted


def test_first_refresh_decrypts_every_database(store):
    """第一次刷新把五个库都解出来。"""
    db, decrypted = store
    assert db.refresh() is True
    assert sorted(decrypted) == sorted(name for _, name in DBS)


def test_unchanged_databases_are_skipped(store):
    """文件没动就不重复解：每轮轮询都调 refresh()，靠这条把它变成 stat 的成本。"""
    db, decrypted = store
    db.refresh()
    decrypted.clear()

    assert db.refresh() is False
    assert decrypted == []


def test_a_touched_database_is_decrypted_again(store):
    """文件变了就重解，只解那一个。"""
    db, decrypted = store
    db.refresh()
    decrypted.clear()

    os.utime(os.path.join(db.data_dir, "db_storage", "message", "message_0.db"), (10, 10))
    assert db.refresh() is True
    assert decrypted == ["message_0.db"]


def test_wal_change_decrypts_a_tracked_database_again(store):
    """hardlink.db 的新行只在 WAL 里时，主库不动也要重解。"""
    db, decrypted = store
    db.refresh()
    decrypted.clear()

    wal = os.path.join(db.data_dir, "db_storage", "hardlink", "hardlink.db-wal")
    with open(wal, "wb") as fh:
        fh.write(b"row for the image that just arrived")

    assert db.refresh() is True
    assert decrypted == ["hardlink.db"]


def test_wal_change_does_not_redo_the_big_message_database(store):
    """message_0.db 体积大、检查点也勤，不把 WAL 纳入判据（避免每条消息重解）。"""
    db, decrypted = store
    db.refresh()
    decrypted.clear()

    wal = os.path.join(db.data_dir, "db_storage", "message", "message_0.db-wal")
    with open(wal, "wb") as fh:
        fh.write(b"new message rows")

    assert db.refresh() is False
    assert decrypted == []


def test_rows_that_only_live_in_the_wal_become_visible(tmp_path, monkeypatch):
    """WAL 变化后重解，按 md5 就能查回刚到的图片文件。"""
    data_dir = tmp_path / "wx_files"
    work_dir = tmp_path / "wx_cache"
    hardlink = data_dir / "db_storage" / "hardlink" / "hardlink.db"
    hardlink.parent.mkdir(parents=True)
    hardlink.write_bytes(b"encrypted-hardlink")
    rows: list[tuple[str, str]] = []

    def fake_decrypt_to_file(passphrase, src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        con = sqlite3.connect(dst)
        con.execute(
            "CREATE TABLE IF NOT EXISTS image_hardlink_info_v4"
            " (md5 TEXT, md5_hash TEXT, file_name TEXT)"
        )
        for md5, name in rows:
            con.execute(
                "INSERT INTO image_hardlink_info_v4 (md5, md5_hash, file_name) VALUES (?, ?, ?)",
                (md5, md5, name),
            )
        con.commit()
        con.close()
        return os.path.getsize(dst)

    monkeypatch.setattr(wx_crypto, "decrypt_to_file", fake_decrypt_to_file)
    store = wx_store.WxStore(str(data_dir), b"pass", str(work_dir))

    store.refresh()
    assert store.image_path(MD5) is None

    rows.append((MD5, "deadbeef.dat"))
    with open(str(hardlink) + "-wal", "wb") as fh:
        fh.write(b"the row landed here")

    store.refresh()
    assert store.image_path(MD5) == "deadbeef.dat"


def test_contact_revision_advances_only_when_contact_db_is_redecrypted(store):
    """通讯录版本号只在 contact.db 真的重解时前进，昵称缓存靠它失效。"""
    db, decrypted = store
    db.refresh()
    assert db.contact_revision == 1

    assert db.refresh() is False
    assert db.contact_revision == 1
    decrypted.clear()

    os.utime(os.path.join(db.data_dir, "db_storage", "contact", "contact.db"), (10, 10))
    assert db.refresh() is True
    assert decrypted == ["contact.db"]
    assert db.contact_revision == 2


def test_contact_wal_change_also_advances_the_revision(store):
    """改名常常先落在 contact.db-wal 里，主库不动也要能感知到。"""
    db, decrypted = store
    db.refresh()
    decrypted.clear()

    wal = os.path.join(db.data_dir, "db_storage", "contact", "contact.db-wal")
    with open(wal, "wb") as fh:
        fh.write(b"new nickname row")

    assert db.refresh() is True
    assert decrypted == ["contact.db"]
    assert db.contact_revision == 2


def test_a_touched_message_database_does_not_advance_the_revision(store):
    db, _decrypted = store
    db.refresh()

    os.utime(os.path.join(db.data_dir, "db_storage", "message", "message_0.db"), (10, 10))
    db.refresh()

    assert db.contact_revision == 1
