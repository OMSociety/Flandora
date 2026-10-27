"""本地库的解密范围：会话表与消息库分开解，各自只碰自己那几个文件。

真实的库是加密的，这里只放可 stat 的占位文件，并用替身换掉 ``decrypt_to_file``，
验的是「哪几个库被解」与「mtime 没动就不重解」。
"""

from __future__ import annotations

import os

import pytest

from flandora import wx_crypto, wx_store

SESSION = ("session", "session.db")
MESSAGE_SIDE = (
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
    for sub, name in (SESSION, *MESSAGE_SIDE):
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


def test_refresh_session_only_decrypts_the_session_database(store):
    """会话表先行：只解 session.db，消息库不动。"""
    db, decrypted = store
    assert db.refresh_session() is True
    assert decrypted == ["session.db"]


def test_refresh_messages_decrypts_the_message_side(store):
    """判断到有新消息时，解消息本体与它依赖的索引库。"""
    db, decrypted = store
    assert db.refresh_messages() is True
    assert sorted(decrypted) == sorted(name for _, name in MESSAGE_SIDE)


def test_unchanged_database_is_not_decrypted_again(store):
    """文件没动就不重复解：这是每轮轮询里最贵的一步。"""
    db, decrypted = store
    assert db.refresh_session() is True
    assert db.refresh_session() is False
    assert decrypted == ["session.db"]

    os.utime(
        os.path.join(db.data_dir, "db_storage", "session", "session.db"),
        (10, 10),
    )
    assert db.refresh_session() is True
    assert decrypted == ["session.db", "session.db"]


def test_full_refresh_covers_every_database(store):
    """显式整刷把五个库都解一遍。"""
    db, decrypted = store
    assert db.refresh() is True
    assert sorted(decrypted) == sorted(name for _, name in (SESSION, *MESSAGE_SIDE))
