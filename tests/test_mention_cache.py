"""改名后的 @ 识别：昵称与成员名册随通讯录版本失效，不再等重启或超时。

以前这两处缓存一个跟着 store 对象走、一个按 10 分钟超时，桥接运行期间改名就
会漏判——正文里的 ``@新名`` 既不被当成提及，也解析不成 at 段。这里用替身把
``contact_revision`` 走一遍，验缓存会跟着版本号作废。
"""

from __future__ import annotations

import logging

import pytest

from flandora import bridge_core
from flandora.bridge_core import Flandora


class _Store:
    """收件源 store 的最小替身：昵称、名册与通讯录版本号。"""

    def __init__(self, nickname: str = "旧名") -> None:
        self.my_wxid = "wxid_me"
        self.contact_revision = 1
        self.nickname_value = nickname
        self.names = {"wxid_me": nickname, "wxid_a": "阿甲"}
        self.nick_calls = 0
        self.name_calls = 0

    def self_nickname(self) -> str:
        self.nick_calls += 1
        return self.nickname_value

    def display_names(self) -> dict[str, str]:
        self.name_calls += 1
        return dict(self.names)


@pytest.fixture
def bridge(monkeypatch):
    store = _Store()
    monkeypatch.setattr(bridge_core.RUNTIME, "_store", store)
    yield Flandora(None), store
    monkeypatch.setattr(bridge_core.RUNTIME, "_store", None)


def test_nickname_is_cached_within_one_revision(bridge):
    core, store = bridge
    assert core.self_nickname() == "旧名"
    assert core.self_nickname() == "旧名"
    assert store.nick_calls == 1


def test_rename_after_reload_is_picked_up(bridge):
    """改名后 contact.db 重解一次，版本号一变昵称立刻跟着换。"""
    core, store = bridge
    assert core.self_nickname() == "旧名"

    store.nickname_value = "长征LongMarch"
    store.names["wxid_me"] = "长征LongMarch"
    store.contact_revision += 1

    assert core.self_nickname() == "长征LongMarch"
    assert store.nick_calls == 2


def test_unreadable_nickname_returns_empty_and_warns_once(bridge, caplog):
    core, store = bridge

    def broken():
        raise OSError("contact.db 尚未解密")

    store.self_nickname = broken
    with caplog.at_level(logging.WARNING, logger="flandora"):
        assert core.self_nickname() == ""
        assert core.self_nickname() == ""

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "读取自己昵称失败" in warnings[0].getMessage()


def test_member_names_follow_the_revision(bridge):
    core, store = bridge
    session = "123@chatroom"

    assert core.resolve_names(session)["wxid_me"] == "旧名"
    assert core.resolve_names(session)["wxid_me"] == "旧名"
    assert store.name_calls == 1

    store.names["wxid_me"] = "长征LongMarch"
    store.contact_revision += 1

    assert core.resolve_names(session)["wxid_me"] == "长征LongMarch"
    assert store.name_calls == 2


def test_member_names_still_expire_after_ten_minutes(bridge, monkeypatch):
    """版本号没变时保留 10 分钟兜底，避免通讯录长期不刷新。"""
    core, store = bridge
    session = "123@chatroom"
    core.resolve_names(session)

    first = core._member_cache[session]
    core._member_cache[session] = (first[0] - 601, first[1], first[2])
    core.resolve_names(session)

    assert store.name_calls == 2


def test_empty_contact_table_is_not_cached(bridge, caplog):
    core, store = bridge
    store.names = {}
    with caplog.at_level(logging.WARNING, logger="flandora"):
        assert core.resolve_names("1@chatroom") == {}
        assert core.resolve_names("1@chatroom") == {}

    assert store.name_calls == 2
    assert any("通讯录为空" in r.getMessage() for r in caplog.records)


def test_mention_gate_uses_the_fresh_nickname(bridge):
    """改名后同一段文字应当被认成「在叫我」。"""
    core, store = bridge
    assert core._is_mentioned({"content": "@旧名 hello"}) is True

    store.nickname_value = "长征LongMarch"
    store.contact_revision += 1

    assert core._is_mentioned({"content": "@长征LongMarch hello"}) is True
    assert core._is_mentioned({"content": "@旧名 hello"}) is False
