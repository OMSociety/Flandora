"""网络适配器：配置规范化 + 四类入口的实际收发。

用真的端口跑，覆盖这些行为：WS 服务端能处理动作并推送事件、api 角色不推事件、
HTTP 服务端按路径收动作、HTTP 上报把事件 POST 出去、反向 WS 主动连上并能被推送。
配置规范化单独测：写错的配置必须报错，不能悄悄变成一个什么都不监听的桥接。
"""

import asyncio
import json
import socket

import pytest

from flandora import ob_network


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _event(text: str = "你好") -> dict:
    return {
        "time": 1750000000,
        "self_id": 1,
        "post_type": "message",
        "message_type": "private",
        "message_id": 9001,
        "user_id": 7,
        "group_id": 0,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "sender": {"user_id": 7, "nickname": "张三"},
    }


# ---------- 配置规范化 ----------


def test_normalize_defaults():
    node = ob_network.normalize_node({"type": "ws_server", "port": 7999}, 0)
    assert node["name"] == "ws_server-0"
    assert node["host"] == "127.0.0.1"
    assert node["path"] == "/"
    assert node["role"] == "universal"
    assert node["enabled"] is True
    assert node["message_format"] == ""


def test_normalize_rejects_bad_input():
    with pytest.raises(ob_network.NetworkConfigError):
        ob_network.normalize_node({"type": "carrier_pigeon"}, 0)
    with pytest.raises(ob_network.NetworkConfigError):
        ob_network.normalize_node({"type": "ws_server"}, 0)  # 缺 port
    with pytest.raises(ob_network.NetworkConfigError):
        ob_network.normalize_node({"type": "ws_server", "port": 1, "role": "god"}, 0)
    with pytest.raises(ob_network.NetworkConfigError):
        ob_network.normalize_node({"type": "http_client", "url": "ftp://x/"}, 0)
    with pytest.raises(ob_network.NetworkConfigError):
        ob_network.normalize_node({"type": "ws_client", "url": "http://x/"}, 0)
    with pytest.raises(ob_network.NetworkConfigError):
        ob_network.normalize_node({"type": "ws_server", "port": 1, "message_format": "xml"}, 0)


def test_normalize_nodes_rejects_duplicates_and_non_list():
    with pytest.raises(ob_network.NetworkConfigError):
        ob_network.normalize_nodes({"type": "ws_server"})
    with pytest.raises(ob_network.NetworkConfigError):
        ob_network.normalize_nodes(
            [
                {"type": "ws_server", "name": "a", "port": 1},
                {"type": "ws_server", "name": "a", "port": 2},
            ]
        )
    assert ob_network.normalize_nodes(None) == []


def test_legacy_node_keeps_old_config_working():
    node = ob_network.legacy_node("127.0.0.1", 7999, "t0ken")
    assert node["type"] == "ws_server"
    assert node["port"] == 7999
    assert node["access_token"] == "t0ken"


def test_format_for_node_switches_message_shape():
    payload = _event()
    assert (
        ob_network.format_for_node(payload, {"message_format": ""}, "array")["message"]
        == payload["message"]
    )
    stringy = ob_network.format_for_node(payload, {"message_format": "string"}, "array")
    assert stringy["message"] == "你好"
    assert stringy["raw_message"] == "你好"
    notice = {"post_type": "notice", "notice_type": "x"}
    assert ob_network.format_for_node(notice, {"message_format": "string"}, "array") == notice


# ---------- WS 服务端 ----------


def test_ws_server_serves_actions_and_pushes_events():
    from websockets.asyncio.client import connect

    async def main():
        port = _free_port()
        mgr = ob_network.NetworkManager()
        mgr.configure(
            [
                ob_network.normalize_node(
                    {
                        "type": "ws_server",
                        "name": "t",
                        "host": "127.0.0.1",
                        "port": port,
                        "access_token": "tk",
                    },
                    0,
                )
            ]
        )
        mgr.start(asyncio.get_running_loop())
        try:
            async with connect(f"ws://127.0.0.1:{port}/?access_token=tk") as ws:
                await ws.send(json.dumps({"action": "get_login_info", "echo": "e1"}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                assert resp["echo"] == "e1"
                assert resp["status"] == "ok"

                # 推送要走到这条连接上
                sent = await mgr._broadcast(_event("广播"))
                assert sent == 1
                ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                assert ev["message_id"] == 9001

                # 从别的线程调同步 broadcast（桥接线程就是这么调的）
                sent = await asyncio.to_thread(mgr.broadcast, _event("跨线程"))
                assert sent == 1
                ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                assert ev["raw_message"] == "跨线程"
        finally:
            await mgr.stop()

    asyncio.run(main())


def test_api_role_gets_no_events():
    from websockets.asyncio.client import connect

    async def main():
        port = _free_port()
        mgr = ob_network.NetworkManager()
        mgr.configure(
            [
                ob_network.normalize_node(
                    {
                        "type": "ws_server",
                        "name": "a",
                        "host": "127.0.0.1",
                        "port": port,
                        "role": "api",
                    },
                    0,
                )
            ]
        )
        mgr.start(asyncio.get_running_loop())
        try:
            async with connect(f"ws://127.0.0.1:{port}/") as ws:
                await ws.send(json.dumps({"action": "get_login_info", "echo": "e1"}))
                assert json.loads(await asyncio.wait_for(ws.recv(), timeout=10))["echo"] == "e1"
                assert await mgr._broadcast(_event()) == 0
        finally:
            await mgr.stop()

    asyncio.run(main())


# ---------- HTTP 服务端 ----------


def test_http_server_serves_actions():
    async def main():
        import requests

        port = _free_port()
        mgr = ob_network.NetworkManager()
        mgr.configure(
            [
                ob_network.normalize_node(
                    {"type": "http_server", "name": "h", "host": "127.0.0.1", "port": port}, 0
                )
            ]
        )
        mgr.start(asyncio.get_running_loop())
        try:
            # 等 uvicorn 起监听
            for _ in range(100):
                try:
                    resp = await asyncio.to_thread(
                        requests.post,
                        f"http://127.0.0.1:{port}/",
                        json={"action": "get_login_info", "echo": "http"},
                        timeout=2,
                    )
                    break
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(0.1)
            else:
                raise AssertionError("HTTP 服务端没有起来")
            body = resp.json()
            assert body["echo"] == "http"
            assert body["status"] == "ok"
        finally:
            await mgr.stop()

    asyncio.run(main())


# ---------- HTTP 上报 ----------


def test_http_client_posts_events():
    async def main():
        import uvicorn
        from fastapi import FastAPI, Request

        received: list[dict] = []
        app = FastAPI()

        @app.post("/hook")
        async def hook(request: Request):  # noqa: ANN202
            received.append(await request.json())
            return {"ok": True}

        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
        )
        task = asyncio.create_task(server.serve())
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            mgr = ob_network.NetworkManager()
            mgr.configure(
                [
                    ob_network.normalize_node(
                        {
                            "type": "http_client",
                            "name": "up",
                            "url": f"http://127.0.0.1:{port}/hook",
                        },
                        0,
                    )
                ]
            )
            mgr.start(asyncio.get_running_loop())
            try:
                assert await mgr._broadcast(_event("上报")) == 1
                assert received and received[0]["raw_message"] == "上报"
            finally:
                await mgr.stop()
        finally:
            server.should_exit = True
            await task

    asyncio.run(main())


# ---------- 反向 WS ----------


def test_ws_client_connects_and_receives_events():
    from websockets.asyncio.server import serve

    async def main():
        got: list[dict] = []
        connected = asyncio.Event()

        async def handler(ws):
            connected.set()
            async for raw in ws:
                got.append(json.loads(raw))

        port = _free_port()
        async with serve(handler, "127.0.0.1", port):
            mgr = ob_network.NetworkManager()
            mgr.configure(
                [
                    ob_network.normalize_node(
                        {
                            "type": "ws_client",
                            "name": "rev",
                            "url": f"ws://127.0.0.1:{port}/ws",
                            "reconnect_interval_ms": 1000,
                        },
                        0,
                    )
                ]
            )
            mgr.start(asyncio.get_running_loop())
            try:
                await asyncio.wait_for(connected.wait(), timeout=10)
                for _ in range(50):  # 等管理器记下这条连接
                    if await mgr._broadcast(_event("反向")) == 1:
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("反向 WS 事件没有送到")
                for _ in range(30):  # 发送是异步落地，等对端真的收到
                    if got:
                        break
                    await asyncio.sleep(0.1)
                assert got and got[0]["raw_message"] == "反向"
            finally:
                await mgr.stop()

    asyncio.run(main())


def test_status_reports_targets():
    mgr = ob_network.NetworkManager()
    mgr.configure(
        [
            ob_network.normalize_node({"type": "ws_server", "name": "s", "port": 7001}, 0),
            ob_network.normalize_node(
                {"type": "http_client", "name": "c", "url": "http://x.invalid/hook"}, 1
            ),
        ]
    )
    rows = {r["name"]: r for r in mgr.status()}
    assert rows["s"]["target"] == "127.0.0.1:7001/"
    assert rows["s"]["state"] == "stopped"
    assert rows["c"]["target"] == "http://x.invalid/hook"
    assert rows["c"]["message_format"] == "（跟随全局）"
