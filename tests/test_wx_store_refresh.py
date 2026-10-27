"""本地库的解密判据：文件没动就不重解，动了才解。

真实的库是加密的，这里只放可 stat 的占位文件，并用替身换掉 ``decrypt_to_file``，
验的是「同一份文件被跳过」与「改动后会重解」。
"""

from __future__ import annotations

import os

import pytest

from flandora import wx_crypto, wx_store

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
