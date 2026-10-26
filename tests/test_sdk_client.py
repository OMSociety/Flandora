"""客户端走真网络：本地起一个 OneBot 假服务端，验证 echo 关联、事件分发与错误归类。"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from flandora_sdk import ApiError, FlandoraHttp, FlandoraWS, MessageChain, TransportError
from websockets.asyncio.server import serve


class FakeBridge:
    """最小 OneBot 服务端：按动作名回固定响应，另外推一条事件。"""

    def __init__(self):
        self.received = []
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.port = None
        self.ready = threading.Event()
        self._server = None

    def start(self):
        self.thread.start()
        assert self.ready.wait(5), "假服务端没能起来"
        return self

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._serve())
        self.loop.run_forever()

    async def _serve(self):
        self._server = await serve(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        self.ready.set()

    async def _handle(self, ws):
        await ws.send(
            json.dumps(
                {
                    "time": 1770000000,
                    "self_id": 1,
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": 1001,
                    "user_id": 2002,
                    "raw_message": "启动后的第一条",
                    "message": [{"type": "text", "data": {"text": "启动后的第一条"}}],
                }
            )
        )
        async for raw in ws:
            payload = json.loads(raw)
            self.received.append(payload)
            action = payload.get("action")
            echo = payload.get("echo")
            if action == "silent":
                continue
            if action == "fail_me":
                await ws.send(
                    json.dumps(
                        {
                            "status": "failed",
                            "retcode": 1404,
                            "message": "这个动作没有实现",
                            "echo": echo,
                        }
                    )
                )
                continue
            data = (
                {"user_id": 1, "nickname": "芙兰朵露"}
                if action == "get_login_info"
                else {"ok": True}
            )
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))

    def stop(self):
        if self._server is not None:

            async def _shutdown():
                self._server.close()
                await self._server.wait_closed()

            asyncio.run_coroutine_threadsafe(_shutdown(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def bridge():
    server = FakeBridge().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def http_bridge():
    """HTTP 服务端；带 token 时校验 Bearer 头。"""

    state = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server 的约定
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            state["requests"].append(payload)
            if self.headers.get("Authorization") != "Bearer secret":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"status":"failed","retcode":1401}')
                return
            action = payload.get("action")
            body = (
                {
                    "status": "failed",
                    "retcode": 1404,
                    "message": "没有",
                    "echo": payload.get("echo"),
                }
                if action == "fail_me"
                else {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 9},
                    "echo": payload.get("echo"),
                }
            )
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_ws_call_returns_data_and_sends_echo(bridge):
    with FlandoraWS(f"ws://127.0.0.1:{bridge.port}", timeout=5) as client:
        info = client.get_login_info()
    assert info == {"user_id": 1, "nickname": "芙兰朵露"}
    assert bridge.received[-1]["action"] == "get_login_info"
    assert bridge.received[-1]["echo"]


def test_ws_api_failure_raises_api_error(bridge):
    with FlandoraWS(f"ws://127.0.0.1:{bridge.port}", timeout=5) as client:
        with pytest.raises(ApiError) as err:
            client.call("fail_me")
    assert err.value.retcode == 1404
    assert "没有实现" in str(err.value)


def test_ws_unsolicited_event_reaches_handler(bridge):
    seen = []
    with FlandoraWS(f"ws://127.0.0.1:{bridge.port}", timeout=5) as client:
        client.on("message", seen.append)
        client.get_login_info()
    assert seen and seen[0]["raw_message"] == "启动后的第一条"


def test_ws_timeout_raises_transport_error(bridge):
    with FlandoraWS(f"ws://127.0.0.1:{bridge.port}", timeout=0.6) as client:
        started = time.monotonic()
        with pytest.raises(TransportError):
            client.call("silent")
        assert time.monotonic() - started < 5


def test_ws_send_group_msg_normalizes_chain(bridge):
    with FlandoraWS(f"ws://127.0.0.1:{bridge.port}", timeout=5) as client:
        client.send_group_msg(1001, MessageChain().at(2002).text(" 收到"))
    sent = bridge.received[-1]
    assert sent["action"] == "send_group_msg"
    assert sent["params"]["group_id"] == 1001
    assert sent["params"]["message"] == [
        {"type": "at", "data": {"qq": 2002}},
        {"type": "text", "data": {"text": " 收到"}},
    ]


def test_ws_send_by_handle_keeps_the_handle(bridge):
    with FlandoraWS(f"ws://127.0.0.1:{bridge.port}", timeout=5) as client:
        client.send_private_msg(" wxid_zhangsan ", "hi")
        client.send_group_msg("50071592949@chatroom", "hi")
    assert bridge.received[-2]["params"]["user_id"] == "wxid_zhangsan"
    assert bridge.received[-1]["params"]["group_id"] == "50071592949@chatroom"


def test_ws_connect_refused_is_transport_error():
    client = FlandoraWS("ws://127.0.0.1:1", timeout=0.5)
    with pytest.raises(TransportError):
        client.connect()


def test_http_call_round_trip(http_bridge):
    base_url, state = http_bridge
    with FlandoraHttp(base_url, token="secret", timeout=5) as client:
        data = client.call(
            "send_private_msg", user_id=2002, message=[{"type": "text", "data": {"text": "hi"}}]
        )
    assert data == {"message_id": 9}
    assert state["requests"][-1]["action"] == "send_private_msg"


def test_http_api_failure_raises_api_error(http_bridge):
    base_url, _ = http_bridge
    with FlandoraHttp(base_url, token="secret", timeout=5) as client:
        with pytest.raises(ApiError):
            client.call("fail_me")


def test_http_wrong_token_keeps_the_retcode(http_bridge):
    """401 也带着 OneBot 错误体回来：retcode 要留住，调用方才能按 retcode 分流。"""
    base_url, _ = http_bridge
    with FlandoraHttp(base_url, token="wrong", timeout=5) as client:
        with pytest.raises(ApiError) as err:
            client.call("get_status")
    assert err.value.retcode == 1401


def test_http_unreachable_is_transport_error():
    with FlandoraHttp("http://127.0.0.1:1/", timeout=0.5) as client:
        with pytest.raises(TransportError):
            client.call("get_status")


def _error_server(status, body, content_type="text/plain"):
    """只回固定错误的 HTTP 服务端，用来验 SDK 对非 OneBot 错误的归类。"""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server 的约定
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_http_400_with_onebot_body_keeps_the_message():
    body = json.dumps(
        {"status": "failed", "retcode": 1406, "message": "本地库里没有 wxid_x 的资料"}
    ).encode("utf-8")
    server, thread = _error_server(400, body, "application/json")
    try:
        with FlandoraHttp(f"http://127.0.0.1:{server.server_address[1]}/", timeout=5) as client:
            with pytest.raises(ApiError) as err:
                client.call("get_stranger_info", user_id=1)
        assert err.value.retcode == 1406
        assert "本地库里没有" in str(err.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_http_error_without_onebot_body_is_a_transport_error():
    server, thread = _error_server(502, b"<html>bad gateway</html>", "text/html")
    try:
        with FlandoraHttp(f"http://127.0.0.1:{server.server_address[1]}/", timeout=5) as client:
            with pytest.raises(TransportError) as err:
                client.call("get_status")
        assert "502" in str(err.value) and "bad gateway" in str(err.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
