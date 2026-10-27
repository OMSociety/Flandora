"""收件循环遇到口令校验不过时的行为：歇一拍继续，不停摆。

真正的库是加密的，这里把 ``build_source`` 换成替身：第一轮抛 ``WrongKey``，
之后正常返回一条消息——验的是循环没有退出，而且消息照旧被吃进去。
"""

from __future__ import annotations

import threading

import pytest

from flandora import bridge_core, wx_crypto
from flandora.bridge_core import Flandora
from flandora.runtime import LIFECYCLE


class _StubSource:
    poll_seconds = 0.01

    def __init__(self):
        self.store = object()
        self.rounds = 0

    def prime(self):
        self.rounds = 0

    def poll(self):
        self.rounds += 1
        if self.rounds == 1:
            raise wx_crypto.WrongKey("口令与账号对不上")
        if self.rounds == 2:
            return [{"rawid": "wxlocal:a:1", "content": "歇一拍之后照样收", "sourceName": "某人"}]
        LIFECYCLE.begin_stop()
        return []


@pytest.fixture
def core(monkeypatch):
    source = _StubSource()
    monkeypatch.setattr(bridge_core.wx_source, "build_source", lambda **kw: source)
    monkeypatch.setattr(bridge_core.time, "sleep", lambda _s: None)
    got = []
    monkeypatch.setattr(Flandora, "_ingest", lambda self, data: got.append(data) or True)
    LIFECYCLE.begin_start()
    return Flandora(sender=None), source, got


def test_wrong_key_round_does_not_stop_the_loop(core):
    instance, source, got = core
    instance.listen_local_db()

    assert source.rounds >= 3
    assert [d["content"] for d in got] == ["歇一拍之后照样收"]


def test_wrong_key_is_reported_but_not_at_every_round(core, caplog):
    instance, _source, _got = core
    with caplog.at_level("WARNING"):
        instance.listen_local_db()
    warnings = [r for r in caplog.records if "口令校验不过" in r.getMessage()]
    assert len(warnings) == 1  # 只有第一轮记一次，后面不再刷屏


def test_loop_stops_when_the_lifecycle_says_so(core):
    instance, source, _got = core
    threading.Thread(target=instance.listen_local_db, daemon=True).start()
    # _StubSource 在第 3 轮结束时置 STOPPING，循环应随之退出
    for _ in range(200):
        if LIFECYCLE.snapshot().state != "running":
            break
        threading.Event().wait(0.01)
    assert LIFECYCLE.snapshot().state != "running"
