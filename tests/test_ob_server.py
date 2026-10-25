"""验证：OneBot WS 服务端的握手鉴权、消息往返与超限处理。

不依赖真微信，端口每次现取一个空闲的。这个文件同时是 websockets 大版本升级的
回归网——ob_server.py 用的是 websockets.asyncio 那套新协议栈（serve、
http11.Response、datastructures.Headers），换大版本后必须重跑这里。

覆盖到的实测版本：16.1.1 / 17.3。

**两处与 ob_networks 改造对齐的约定**（改这块时别改回去）：

1. 服务端只读 ``config.OB_NETWORKS``，不再看 ``OB_SERVER_PORT``。只改端口号
   会让它起在 config.json 里的默认端口上，测试连的却是随机空闲端口。
2. 入口的 ``role`` 取 ``api``：该角色的入口在连接建立时**不推 lifecycle(connect)**
   （见 ``ob_network._announce_connect``），所以第一条收到的就是动作响应。
   用 ``universal`` 的话要先吃掉一条 meta_event。

两种跑法等价：

    python -m pytest tests/test_ob_server.py -q
    python tests/test_ob_server.py
"""

import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import ob_network  # noqa: E402

TOKEN = "test-token-8f3a"
MB = 1024 * 1024


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _rejected(exc) -> bool:
    """连接被服务端拒绝（401）。不同版本 InvalidStatus 的字段略有差异，取到就核。"""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return code in (None, 401)


async def _wait_rejected(url: str, timeout: float = 15.0):
    """等服务端就绪，返回那次「不带 token」的拒绝异常。

    用真实握手探测而不是连一下 TCP 就断——后者会让 websockets 13.x 在
    Python 3.13 上打出内部断言噪声（它自己的缺陷，与本项目无关）。
    """
    import websockets.exceptions as wx_exc
    from websockets.asyncio.client import connect

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last = None
    while loop.time() < deadline:
        try:
            async with connect(url):
                pass
            raise AssertionError("不带 token 竟然连上了")
        except wx_exc.InvalidStatus as e:
            return e
        except OSError as e:  # 端口还没监听
            last = e
            await asyncio.sleep(0.1)
    raise AssertionError(f"服务端未在 {timeout}s 内就绪: {last!r}")


@pytest.fixture(scope="module")
def server():
    """起一个真实监听的服务端（module 作用域：起停成本高，用例共享）。

    返回 (port, base_url)。服务端跑在独立线程自己的事件循环里，
    与 pytest 的事件循环互不干扰。
    """
    d = os.environ.get("FLANDORA_TEST_TMP") or os.path.join(REPO_ROOT, ".pytest-tmp")
    os.makedirs(d, exist_ok=True)
    cfg_dir = tempfile.mkdtemp(prefix="wwb-ob-", dir=d)
    cfg_path = os.path.join(cfg_dir, "config.json")

    from flandora import config

    port = _free_port()
    # 关键：服务端真正读的是 OB_NETWORKS。role=api 避开 lifecycle(connect)。
    nodes = [
        ob_network.normalize_node(
            {
                "type": "ws_server",
                "name": "test",
                "host": "127.0.0.1",
                "port": port,
                "path": "/",
                "role": "api",
                "access_token": TOKEN,
            },
            0,
        )
    ]
    # 以示例配置为底，只改端口与心跳：心跳会往会话里塞 meta_event，这里关掉
    with open(os.path.join(REPO_ROOT, "flandora", "config.example.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["ob_networks"] = nodes
    cfg["ob_heartbeat_seconds"] = 0
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

    import threading

    loop = asyncio.new_event_loop()
    started = threading.Event()
    stop_flag = threading.Event()

    async def _serve():
        mgr = ob_network.manager()
        mgr.configure(config.OB_NETWORKS)
        mgr.start(asyncio.get_running_loop())
        started.set()
        while not stop_flag.is_set():
            await asyncio.sleep(0.05)
        await mgr.stop()

    def _run():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        finally:
            loop.close()

    with config.temporary(cfg_path):
        t = threading.Thread(target=_run, daemon=True, name="ob-server-test")
        t.start()
        if not started.wait(timeout=15):
            raise RuntimeError("服务端未能在 15s 内启动")
        try:
            yield port, f"ws://127.0.0.1:{port}/"
        finally:
            stop_flag.set()
            t.join(timeout=15)
    shutil.rmtree(cfg_dir, ignore_errors=True)


# ===== 1. 握手鉴权 =====


def test_server_rejects_connection_without_token(server):
    """起监听之后，不带 token 的连接必须被拒（401）。"""
    _, url = server

    async def run():

        exc = await _wait_rejected(url)
        assert _rejected(exc), f"拒绝原因不是 401: {exc!r}"

    asyncio.run(run())


def test_websockets_symbols_still_exist():
    """ob_server 用到的 websockets 符号齐备（大版本升级的回归网）。"""
    from websockets.asyncio.server import serve  # noqa: F401
    from websockets.datastructures import Headers  # noqa: F401
    from websockets.exceptions import ConnectionClosed  # noqa: F401
    from websockets.http11 import Response  # noqa: F401


def test_access_token_query_is_accepted(server):
    """?access_token= 鉴权通过，请求往返带回 echo。"""
    _, url = server

    async def run():
        from websockets.asyncio.client import connect

        async with connect(url + f"?access_token={TOKEN}") as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "e1"}))
            resp = await asyncio.wait_for(ws.recv(), timeout=15)
        payload = json.loads(resp)
        assert payload.get("echo") == "e1", f"响应的 echo 不对: {payload!r}"
        assert "status" in payload and "retcode" in payload, f"响应不是 OneBot 形状: {payload!r}"

    asyncio.run(run())


def test_authorization_header_is_accepted(server):
    """Authorization: Bearer 请求头同样认。"""
    _, url = server

    async def run():
        from websockets.asyncio.client import connect

        async with connect(url, additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "e2"}))
            resp = await asyncio.wait_for(ws.recv(), timeout=15)
        assert json.loads(resp).get("echo") == "e2", resp

    asyncio.run(run())


def test_wrong_token_is_rejected(server):
    """错误 token 的连接被拒（401）。"""
    _, url = server

    async def run():
        import websockets.exceptions as wx_exc
        from websockets.asyncio.client import connect

        try:
            async with connect(url + "?access_token=wrong"):
                pass
            raise AssertionError("错误 token 竟然连上了")
        except wx_exc.InvalidStatus as e:
            assert _rejected(e), f"拒绝原因不是 401: {e!r}"

    asyncio.run(run())


# ===== 2. 大消息与超限 =====


def test_large_message_roundtrip(server):
    """大消息往返（服务端 max_size = 16MB）。"""
    _, url = server

    async def run():
        from websockets.asyncio.client import connect

        blob = "x" * (8 * MB)
        async with connect(url + f"?access_token={TOKEN}", max_size=None) as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "e3", "blob": blob}))
            resp = await asyncio.wait_for(ws.recv(), timeout=60)
        assert json.loads(resp).get("echo") == "e3", resp[:200]

    asyncio.run(run())


def test_oversized_message_closes_with_1009(server):
    """超过 max_size 时干净关闭（1009），不是把进程搞崩。"""
    _, url = server

    async def run():
        import websockets.exceptions as wx_exc
        from websockets.asyncio.client import connect

        async with connect(url + f"?access_token={TOKEN}", max_size=None) as ws:
            await ws.send(b"y" * (20 * MB))
            try:
                await asyncio.wait_for(ws.recv(), timeout=30)
                raise AssertionError("服务端接受了超限消息")
            except wx_exc.ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                assert code == 1009, f"期望关闭码 1009，实际 {code}"

    asyncio.run(run())


# ===== 3. 韧性与收尾 =====


def test_server_survives_oversized_connection(server):
    """超限断开之后服务端仍可继续服务。"""
    _, url = server

    async def run():
        from websockets.asyncio.client import connect

        async with connect(url + f"?access_token={TOKEN}") as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "e4"}))
            resp = await asyncio.wait_for(ws.recv(), timeout=15)
        assert json.loads(resp).get("echo") == "e4", resp

    asyncio.run(run())


def test_client_disconnect_is_quiet(server):
    """客户端正常断开会话不引发异常。"""
    _, url = server

    async def run():
        from websockets.asyncio.client import connect

        ws = await connect(url + f"?access_token={TOKEN}")
        await ws.close(code=1000, reason="bye")
        await asyncio.sleep(0.5)

    asyncio.run(run())


def main() -> int:
    """脚本直跑：本文件的用例依赖 module 级 fixture，改委托给 pytest。"""
    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
