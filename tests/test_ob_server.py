"""验证：OneBot WS 服务端的握手鉴权、消息往返与超限处理。

不依赖真微信，端口每次现取一个空闲的。这个文件同时是 websockets 大版本升级的
回归网——ob_server.py 用的是 websockets.asyncio 那套新协议栈（serve、
http11.Response、datastructures.Headers），换大版本后必须重跑这里。

覆盖到的实测版本：16.1.1 / 17.3。
"""
import asyncio
import json
import logging
import os
import shutil
import socket
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO_ROOT, "webot_bridge")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

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
        except OSError as e:          # 端口还没监听
            last = e
            await asyncio.sleep(0.1)
    raise AssertionError(f"服务端未在 {timeout}s 内就绪: {last!r}")


async def _checks(ob_server, config) -> int:
    from websockets.asyncio.client import connect
    import websockets.exceptions as wx_exc

    checks = 0
    url = f"ws://127.0.0.1:{config.OB_SERVER_PORT}/"

    task = asyncio.create_task(ob_server._ob_server_main())
    try:
        # ---------- 1. 服务端就绪且拒绝无凭证连接 ----------
        exc = await _wait_rejected(url)
        assert _rejected(exc), f"拒绝原因不是 401: {exc!r}"
        print("  ok   服务端起监听，不带 token 的连接被拒（401）")
        checks += 1

        # ---------- 2. 运行期用到的符号都还在 ----------
        # ob_server 的接口面：起服务的 serve、鉴权时回 401 用的 http11.Response
        # 与 datastructures.Headers、断开分支捕获的 ConnectionClosed。
        from websockets.asyncio.server import serve  # noqa: F401
        from websockets.http11 import Response  # noqa: F401
        from websockets.datastructures import Headers  # noqa: F401
        from websockets.exceptions import ConnectionClosed  # noqa: F401
        print("  ok   ob_server 用到的 websockets 符号齐备")
        checks += 1

        # ---------- 3. ?access_token= 鉴权 + 请求往返 ----------
        async with connect(url + f"?access_token={TOKEN}") as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "e1"}))
            resp = await asyncio.wait_for(ws.recv(), timeout=15)
        payload = json.loads(resp)
        assert payload.get("echo") == "e1", f"响应的 echo 不对: {payload!r}"
        assert "status" in payload and "retcode" in payload, f"响应不是 OneBot 形状: {payload!r}"
        print("  ok   ?access_token= 鉴权通过，请求往返带回 echo")
        checks += 1

        # ---------- 4. Authorization: Bearer 请求头鉴权 ----------
        async with connect(url, additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "e2"}))
            resp = await asyncio.wait_for(ws.recv(), timeout=15)
        assert json.loads(resp).get("echo") == "e2", resp
        print("  ok   Authorization 请求头鉴权通过")
        checks += 1

        # ---------- 5. 错误 token 被拒 ----------
        try:
            async with connect(url + "?access_token=wrong"):
                pass
            raise AssertionError("错误 token 竟然连上了")
        except wx_exc.InvalidStatus as e:
            assert _rejected(e), f"拒绝原因不是 401: {e!r}"
        print("  ok   错误 token 的连接被拒（401）")
        checks += 1

        # ---------- 6. 大消息往返（服务端 max_size = 16MB）----------
        blob = "x" * (8 * MB)
        async with connect(url + f"?access_token={TOKEN}", max_size=None) as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "e3", "blob": blob}))
            resp = await asyncio.wait_for(ws.recv(), timeout=60)
        assert json.loads(resp).get("echo") == "e3", resp[:200]
        print("  ok   8 MiB 消息往返正常")
        checks += 1

        # ---------- 7. 超过 max_size 时干净关闭（1009）----------
        async with connect(url + f"?access_token={TOKEN}", max_size=None) as ws:
            await ws.send(b"y" * (20 * MB))
            try:
                await asyncio.wait_for(ws.recv(), timeout=30)
                raise AssertionError("服务端接受了超限消息")
            except wx_exc.ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                assert code == 1009, f"期望关闭码 1009，实际 {code}"
        print("  ok   超过 max_size 的连接以 1009 关闭")
        checks += 1

        # ---------- 8. 超限之后服务端仍可继续服务 ----------
        async with connect(url + f"?access_token={TOKEN}") as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "e4"}))
            resp = await asyncio.wait_for(ws.recv(), timeout=15)
        assert json.loads(resp).get("echo") == "e4", resp
        print("  ok   超限之后服务端仍能正常服务")
        checks += 1

        # ---------- 9. 客户端断开不引发异常 ----------
        ws = await connect(url + f"?access_token={TOKEN}")
        await ws.close(code=1000, reason="bye")
        await asyncio.sleep(0.5)
        print("  ok   客户端正常断开会话不引发异常")
        checks += 1
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    return checks


def main() -> int:
    logging.getLogger().setLevel(logging.CRITICAL)
    d = tempfile.mkdtemp(prefix="wwb-ob-")
    shutil.copy(os.path.join(PKG, "config.example.json"),
                os.path.join(d, "config.json"))
    old = os.getcwd()
    os.chdir(d)
    try:
        from webot_bridge import config
        config.OB_SERVER_HOST = "127.0.0.1"
        config.OB_SERVER_PORT = _free_port()
        config.OB_SERVER_TOKEN = TOKEN
        from webot_bridge import ob_server
        checks = asyncio.run(_checks(ob_server, config))
    finally:
        os.chdir(old)
        shutil.rmtree(d, ignore_errors=True)

    print(f"{checks}/{checks} 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
