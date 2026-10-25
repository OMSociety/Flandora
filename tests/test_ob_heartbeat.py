"""心跳与生命周期：连上先道 connect，之后按间隔推 heartbeat，停止前道 disable。

心跳是框架判断「协议端还活着、账号还在线」的依据，和 WebSocket 有没有断是两件事。
这组用例用真端口跑一遍完整时序。
"""

import asyncio
import json
import socket

import pytest
from fastapi.responses import JSONResponse

from flandora import config, ob_network
from flandora.webui import app as webui_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _node(port: int) -> dict:
    return ob_network.normalize_node(
        {"type": "ws_server", "name": "hb", "host": "127.0.0.1", "port": port}, 0
    )


def test_lifecycle_and_heartbeat_are_pushed(monkeypatch):
    from websockets.asyncio.client import connect

    monkeypatch.setattr(config, "OB_HEARTBEAT_SECONDS", 1)

    async def main():
        port = _free_port()
        mgr = ob_network.NetworkManager()
        mgr.configure([_node(port)])
        mgr.start(asyncio.get_running_loop())
        async with connect(f"ws://127.0.0.1:{port}/", open_timeout=10) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            assert first["post_type"] == "meta_event"
            assert first["meta_event_type"] == "lifecycle"
            assert first["sub_type"] == "connect"

            beat = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            assert beat["post_type"] == "meta_event"
            assert beat["meta_event_type"] == "heartbeat"
            assert beat["interval"] == 1
            assert set(beat["status"]) >= {"online", "good"}

            # 停止前要道别：disable 只能趁连接还在时发出去
            await mgr.stop()
            bye = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            assert bye["meta_event_type"] == "lifecycle"
            assert bye["sub_type"] == "disable"

    asyncio.run(main())


def test_no_heartbeat_when_interval_is_zero(monkeypatch):
    from websockets.asyncio.client import connect

    monkeypatch.setattr(config, "OB_HEARTBEAT_SECONDS", 0)

    async def main():
        port = _free_port()
        mgr = ob_network.NetworkManager()
        mgr.configure([_node(port)])
        mgr.start(asyncio.get_running_loop())
        try:
            async with connect(f"ws://127.0.0.1:{port}/", open_timeout=10) as ws:
                # 连接事件照发：它描述通道就绪，与心跳开关无关
                hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                assert hello["meta_event_type"] == "lifecycle"
                assert hello["sub_type"] == "connect"
                # 但心跳一条都不该来
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(), timeout=0.6)
        finally:
            await mgr.stop()

    asyncio.run(main())


def test_heartbeat_status_reflects_runtime():
    """在线 = 已起且未暂停；sender 就绪才算 good。"""
    from flandora.runtime import LIFECYCLE, RUNTIME

    LIFECYCLE.begin_start()
    LIFECYCLE.mark_running()
    RUNTIME.bridge = object()
    RUNTIME.sender = object()
    assert ob_network.NetworkManager._heartbeat_status() == {"online": True, "good": True}

    RUNTIME.sender = None
    assert ob_network.NetworkManager._heartbeat_status() == {"online": True, "good": False}

    assert LIFECYCLE.pause() is True
    assert ob_network.NetworkManager._heartbeat_status() == {"online": False, "good": False}

    assert LIFECYCLE.resume() is True
    RUNTIME.bridge = None
    assert ob_network.NetworkManager._heartbeat_status() == {"online": False, "good": False}


@pytest.fixture()
def saved_config(monkeypatch, tmp_path):
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)
    with config.temporary(tmp_path / "config.json") as store:
        yield store


@pytest.mark.parametrize("bad", [3, 5000, "abc", None])
def test_save_refuses_bad_heartbeat(saved_config, bad):
    rest = webui_app.api_put_config({"ob_heartbeat_seconds": bad}, webui_app.Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert saved_config.read_raw() == {}


def test_save_accepts_zero_and_live_updates(saved_config):
    rest = webui_app.api_put_config({"ob_heartbeat_seconds": 0}, webui_app.Response())

    assert rest["ok"] is True
    assert "ob_heartbeat_seconds" in rest["restart_required"]
    assert saved_config.read_raw()["ob_heartbeat_seconds"] == 0
    assert config.OB_HEARTBEAT_SECONDS == 0
