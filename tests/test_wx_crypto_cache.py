"""口令派生的缓存：同一个库刷新多次，PBKDF2 只付一次成本。

256000 轮 PBKDF2 是本机收件链路里最贵的一步（约 190 ms），而每个库的 salt 固定，
所以按 (口令摘要, salt) 缓存派生结果；这里用替身数它被真正调了几次。
"""

from __future__ import annotations

import hashlib

import pytest

from flandora import wx_crypto

SALT_A = b"A" * 16
SALT_B = b"B" * 16


@pytest.fixture(autouse=True)
def clean_cache():
    wx_crypto._KEY_CACHE.clear()
    yield
    wx_crypto._KEY_CACHE.clear()


@pytest.fixture
def counted_pbkdf2(monkeypatch):
    calls = []
    real = hashlib.pbkdf2_hmac

    def counting(name, password, salt, iterations, dklen=None):
        calls.append((name, salt))
        return real(name, password, salt, iterations, dklen)

    monkeypatch.setattr(wx_crypto.hashlib, "pbkdf2_hmac", counting)
    return calls


def test_same_passphrase_and_salt_derives_once(counted_pbkdf2):
    first = wx_crypto.derive_keys(b"pass", SALT_A)
    second = wx_crypto.derive_keys(b"pass", SALT_A)
    assert first == second
    # 一次派生是两轮 PBKDF2（enc 与 mac），第二次直接命中缓存
    assert len(counted_pbkdf2) == 2


def test_another_salt_or_passphrase_derives_again(counted_pbkdf2):
    wx_crypto.derive_keys(b"pass", SALT_A)
    wx_crypto.derive_keys(b"pass", SALT_B)
    wx_crypto.derive_keys(b"other", SALT_A)
    assert len(counted_pbkdf2) == 6


def test_cache_does_not_grow_without_bound(monkeypatch):
    # 便宜替身：这条只关心缓存容量的上限，不必真的付 40 次 256000 轮
    monkeypatch.setattr(
        wx_crypto.hashlib, "pbkdf2_hmac", lambda *a, **k: b"\x00" * (k.get("dklen") or 32)
    )
    for i in range(wx_crypto._KEY_CACHE_LIMIT + 8):
        wx_crypto.derive_keys(b"pass", i.to_bytes(16, "big"))
    assert len(wx_crypto._KEY_CACHE) <= wx_crypto._KEY_CACHE_LIMIT
