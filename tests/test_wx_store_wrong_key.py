"""读库时口令校验不过：一次巧合不该让收件停摆。

微信写库的间隙解密会读到半行，表现为 ``WrongKey``；这里验的是「同一轮里先重试
一次」与「连着几轮都不过才当成真错」。
"""

from __future__ import annotations

import os

import pytest

from flandora import wx_crypto, wx_store

DBS = (("session", "session.db"), ("message", "message_0.db"))


def _store(tmp_path):
    data_dir = tmp_path / "wx_files"
    for sub, name in DBS:
        target = data_dir / "db_storage" / sub / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"encrypted-" + name.encode())
    return wx_store.WxStore(str(data_dir), b"pass", str(tmp_path / "wx_cache"))


def test_a_transient_wrong_key_is_retried_in_the_same_round(tmp_path, monkeypatch):
    """第一次读到半行、等一拍再读就对：这一轮照常完成。"""
    db = _store(tmp_path)
    calls = {"n": 0}

    def flaky(passphrase, src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise wx_crypto.WrongKey("口令与账号对不上")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(b"plain")
        return 5

    monkeypatch.setattr(wx_crypto, "decrypt_to_file", flaky)
    monkeypatch.setattr(wx_store.time, "sleep", lambda _s: None)

    assert db.refresh() is True
    assert calls["n"] == 3  # 第一库重试一次，第二库一次成功


def test_a_persistent_wrong_key_still_reaches_the_caller(tmp_path, monkeypatch):
    """两次都不过就照旧抛出去，交给收件循环决定怎么处理。"""
    db = _store(tmp_path)

    def always_wrong(passphrase, src, dst):
        raise wx_crypto.WrongKey("口令与账号对不上")

    monkeypatch.setattr(wx_crypto, "decrypt_to_file", always_wrong)
    monkeypatch.setattr(wx_store.time, "sleep", lambda _s: None)

    with pytest.raises(wx_crypto.WrongKey):
        db.refresh()
