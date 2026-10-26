"""网络适配器：配置规范化 + 四类入口的实际收发。

用真的端口跑，覆盖这些行为：WS 服务端能处理动作并推送事件、api 角色不推事件、
HTTP 服务端按路径收动作、HTTP 上报把事件 POST 出去、反向 WS 主动连上并能被推送
（握手头按 OneBot v11 带 X-Self-ID / X-Client-Role，身份变了会重连）。
配置规范化单独测：写错的配置必须报错，不能悄悄变成一个什么都不监听的桥接。
"""

import asyncio
import json
import socket

import pytest
import websockets

from flandora import ob_network


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _recv_event(ws, timeout: float = 10) -> dict:
    """收下一条消息事件，跳过 meta_event。

    客户端一连接就会先收到 lifecycle(connect)，那是通道就绪的信号，
    不属于本组用例关心的消息推送。
    """
    while True:
        payload = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if payload.get("post_type") != "meta_event":
            return payload


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


def test_default_node_carries_the_general_settings():
    node = ob_network.default_node("127.0.0.1", 7999, "t0ken")
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
                resp = await _recv_event(ws)
                assert resp["echo"] == "e1"
                assert resp["status"] == "ok"

                # 推送要走到这条连接上
                sent = await mgr._broadcast(_event("广播"))
                assert sent == 1
                ev = await _recv_event(ws)
                assert ev["message_id"] == 9001

                # 从别的线程调同步 broadcast（桥接线程就是这么调的）
                sent = await asyncio.to_thread(mgr.broadcast, _event("跨线程"))
                assert sent == 1
                ev = await _recv_event(ws)
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
                    {
                        "type": "http_server",
                        "name": "h",
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
            # 等 uvicorn 起监听
            for _ in range(100):
                try:
                    resp = await asyncio.to_thread(
                        requests.post,
                        f"http://127.0.0.1:{port}/",
                        json={"action": "get_login_info", "echo": "http"},
                        headers={"Authorization": "Bearer tk"},
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


def test_http_server_requires_token():
    """http_server 的令牌必填：空令牌等于把发消息的能力交给任意网页。"""
    with pytest.raises(ob_network.NetworkConfigError) as err:
        ob_network.normalize_node({"type": "http_server", "name": "h", "port": 5700}, 0)
    assert "access_token" in str(err.value)


def test_http_server_turns_away_cross_site_requests():
    """动作只收 POST + JSON，且只认本机回环的 Origin / Host。"""

    async def main():
        import requests

        port = _free_port()
        mgr = ob_network.NetworkManager()
        mgr.configure(
            [
                ob_network.normalize_node(
                    {
                        "type": "http_server",
                        "name": "h",
                        "host": "127.0.0.1",
                        "port": port,
                        "access_token": "tk",
                    },
                    0,
                )
            ]
        )
        mgr.start(asyncio.get_running_loop())
        url = f"http://127.0.0.1:{port}/"
        auth = {"Authorization": "Bearer tk"}

        async def send(**kwargs):
            return await asyncio.to_thread(requests.request, timeout=3, **kwargs)

        try:
            for _ in range(100):
                try:
                    await send(
                        method="POST", url=url, json={"action": "get_login_info"}, headers=auth
                    )
                    break
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(0.1)
            else:
                raise AssertionError("HTTP 服务端没有起来")

            # GET 带 action（跨站页面用 <img> 就能发的那种）
            got = await send(method="GET", url=url + "?action=get_login_info&echo=x", headers=auth)
            assert got.status_code == 405, got.text
            # 非 JSON 请求体（跨站的简单请求）
            plain = await send(
                method="POST",
                url=url,
                data='{"action": "get_login_info"}',
                headers={**auth, "Content-Type": "text/plain"},
            )
            assert plain.status_code == 400, plain.text
            # 别的网站的页面来请求
            foreign = await send(
                method="POST",
                url=url,
                json={"action": "get_login_info"},
                headers={**auth, "Origin": "https://example.com"},
            )
            assert foreign.status_code == 403, foreign.text
            # DNS rebinding：域名指向回环，但 Host 还是那个域名
            rebound = await send(
                method="POST",
                url=url,
                json={"action": "get_login_info"},
                headers={**auth, "Host": "evil.example.com"},
            )
            assert rebound.status_code == 403, rebound.text
            # 本机页面的 POST 照常通
            ok = await send(
                method="POST",
                url=url,
                json={"action": "get_login_info", "echo": "local"},
                headers={**auth, "Origin": f"http://127.0.0.1:{port}"},
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["echo"] == "local"
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


def test_ws_client_handshake_headers(isolated_ids):
    """握手要带 OneBot v11 的 X-Self-ID 与 X-Client-Role，否则严格服务端直接拒绝。"""
    self_id = isolated_ids.contact_id("wxid_bot")
    isolated_ids.SELF.set_wxid("wxid_bot")

    headers = ob_network.handshake_headers(
        ob_network.normalize_node({"type": "ws_client", "url": "ws://x.invalid/ws"}, 0)
    )
    assert headers["X-Self-ID"] == str(self_id)
    assert headers["X-Client-Role"] == "Universal"
    assert "Authorization" not in headers

    for role, expected in (("api", "API"), ("event", "Event"), ("universal", "Universal")):
        role_headers = ob_network.handshake_headers(
            ob_network.normalize_node(
                {
                    "type": "ws_client",
                    "url": "ws://x.invalid/ws",
                    "role": role,
                    "access_token": "tk",
                },
                0,
            )
        )
        assert role_headers["X-Client-Role"] == expected
        assert role_headers["Authorization"] == "Bearer tk"


def test_handshake_headers_wait_for_a_known_identity(isolated_ids):
    """还没认出登录账号时不建连：规范没有「未知身份」的写法，发 * 会被严格服务端拒绝。"""
    isolated_ids.SELF.set_wxid("")
    with pytest.raises(ob_network.IdentityNotReady):
        ob_network.handshake_headers(
            ob_network.normalize_node({"type": "ws_client", "url": "ws://x.invalid/ws"}, 0)
        )


def test_ws_client_reconnects_after_identity_change(isolated_ids):
    """登录账号切换后重连换掉 X-Self-ID：事件里的 self_id 变了，握手头必须跟着变。"""
    from websockets.asyncio.server import serve

    async def main():
        seen: list[str] = []
        reconnected = asyncio.Event()

        async def handler(ws):
            seen.append(ws.request.headers["X-Self-ID"])
            if len(seen) > 1:
                reconnected.set()
            try:
                async for _ in ws:
                    pass
            except websockets.exceptions.ConnectionClosed:
                pass

        isolated_ids.SELF.set_wxid("wxid_before")
        first = str(isolated_ids.SELF.user_id())

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
                            # 退避间隔拉到一分钟：这一轮重连只可能来自身份变化
                            "reconnect_interval_ms": 60000,
                        },
                        0,
                    )
                ]
            )
            mgr.start(asyncio.get_running_loop())
            try:
                for _ in range(100):
                    if seen:
                        break
                    await asyncio.sleep(0.05)
                assert seen == [first]

                isolated_ids.SELF.set_wxid("wxid_after")
                second = str(isolated_ids.SELF.user_id())
                assert second != first
                mgr.refresh_identity()

                await asyncio.wait_for(reconnected.wait(), timeout=10)
                assert seen[-1] == second
            finally:
                await mgr.stop()

    asyncio.run(main())


def test_ws_client_connects_and_receives_events(isolated_ids):
    from websockets.asyncio.server import serve

    isolated_ids.SELF.set_wxid("wxid_bot")

    async def main():
        got: list[dict] = []
        connected = asyncio.Event()

        async def handler(ws):
            connected.set()
            try:
                async for raw in ws:
                    got.append(json.loads(raw))
            except websockets.exceptions.ConnectionClosed:
                pass  # 单向关连接是正常收尾

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
                # 发送是异步落地：等到「非 meta 事件」真的收到为止。
                # 连接时先到的 lifecycle(connect) 不算数，否则会提前判过。
                messages: list[dict] = []
                for _ in range(50):
                    messages = [e for e in got if e.get("post_type") != "meta_event"]
                    if messages:
                        break
                    await asyncio.sleep(0.1)
                assert messages and messages[0]["raw_message"] == "反向"
            finally:
                await mgr.stop()

    asyncio.run(main())
