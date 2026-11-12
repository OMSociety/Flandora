"""出站群名的取法：发送前的实时群名，而不是刚建群时的群标识。

真机踩过：新加的群，contact 行还停在 ``-wal`` 里，群名册退化成群标识
``43350942786@chatroom``，拿它去微信搜索框永远选不中会话，回复就发不出去。
"""

from __future__ import annotations

import pytest

from flandora import ob_protocol
from flandora import runtime as runtime_module

GROUP = "43350942786@chatroom"


class _Store:
    def __init__(self, name: str, refreshes: int = 0) -> None:
        self.name = name
        self.refreshes = refreshes

    def refresh_contacts(self) -> bool:
        self.refreshes += 1
        return False

    def groups(self):
        return [{"username": GROUP, "name": self.name}]


@pytest.fixture
def store(monkeypatch):
    fake = _Store("全员禁言")
    monkeypatch.setattr(runtime_module.RUNTIME, "_store", fake)
    yield fake
    monkeypatch.setattr(runtime_module.RUNTIME, "_store", None)


def test_group_name_is_read_from_the_roster(store):
    assert ob_protocol._group_name(GROUP) == "全员禁言"
    assert store.refreshes == 1


def test_group_id_is_not_a_usable_name(monkeypatch):
    fake = _Store(GROUP)
    monkeypatch.setattr(runtime_module.RUNTIME, "_store", fake)
    try:
        assert ob_protocol._group_name(GROUP) == ""
    finally:
        monkeypatch.setattr(runtime_module.RUNTIME, "_store", None)


def test_missing_store_returns_empty(monkeypatch):
    monkeypatch.setattr(runtime_module.RUNTIME, "_store", None)
    assert ob_protocol._group_name(GROUP) == ""
