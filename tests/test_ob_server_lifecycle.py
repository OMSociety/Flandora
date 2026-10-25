"""验证 OneBotServer 的起停真的收尾：停止后端口立刻可复用，重启不遗留线程。

这里盯的是「停止」这条路径的最后一公里：标志翻了、线程还在、端口还占着，
下次启动就会撞端口，或者每次重启多一个空转的事件循环。用例直接连入口
（真监听、真握手），不看内部计数。

两种跑法等价：

    python -m pytest tests/test_ob_server_lifecycle.py -q
    python tests/test_ob_server_lifecycle.py
"""

import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
import threading

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import config, ob_network, ob_server  # noqa: E402

TOKEN = "lifecycle-token-4c1d"
# 与 ob_server.OneBotServer 里给入口线程起的名字一致；用来确认退出后没有残留
THREAD_NAME = "ob11-server"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_is_free(port: int) -> bool:
    """能否独占绑定该端口。

    不能带 ``SO_REUSEADDR``：Windows 上带着它去绑一个正在监听的端口会成功，
    这个检查就永远为真。
    """
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _roundtrip(port: int) -> bool:
    """连一次并要回 echo；入口没起来时返回 False，不抛。"""

    async def run():
        from websockets.asyncio.client import connect

        async with connect(f"ws://127.0.0.1:{port}/?access_token={TOKEN}") as ws:
            await ws.send(json.dumps({"action": "__selftest__", "echo": "life"}))
            resp = await asyncio.wait_for(ws.recv(), timeout=15)
        return json.loads(resp).get("echo") == "life"

    try:
        return asyncio.run(run())
    except Exception:  # noqa: BLE001 - 连不上就是没起，由断言给出结论
        return False


def _entry_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == THREAD_NAME]


@pytest.fixture
def entry_port():
    """给入口一个现取的空闲端口，用例结束后还原配置。"""
    d = os.environ.get("FLANDORA_TEST_TMP") or os.path.join(REPO_ROOT, ".pytest-tmp")
    os.makedirs(d, exist_ok=True)
    cfg_dir = tempfile.mkdtemp(prefix="wwb-life-", dir=d)
    cfg_path = os.path.join(cfg_dir, "config.json")

    port = _free_port()
    nodes = [
        ob_network.normalize_node(
            {
                "type": "ws_server",
                "name": "lifecycle",
                "host": "127.0.0.1",
                "port": port,
                "path": "/",
                "role": "api",
                "access_token": TOKEN,
            },
            0,
        )
    ]
    with open(os.path.join(REPO_ROOT, "flandora", "config.example.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["ob_networks"] = nodes
    cfg["ob_heartbeat_seconds"] = 0
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

    try:
        with config.temporary(cfg_path):
            yield port
    finally:
        shutil.rmtree(cfg_dir, ignore_errors=True)


def test_stop_releases_port_and_allows_restart(entry_port):
    """起 → 停 → 端口可再次绑定 → 再起 → 再停，全程不残留入口线程。"""
    srv = ob_server.ONEBOT_SERVER
    assert srv.start(), "入口第一次没起来"
    try:
        assert srv.is_running()
        assert _roundtrip(entry_port), "入口起来了但连不上"
    finally:
        stopped = srv.stop()

    assert stopped, "stop() 没等到入口线程退出"
    assert not srv.is_running()
    assert _port_is_free(entry_port), "停止后端口仍被占用，下次启动会撞端口"
    assert not _roundtrip(entry_port), "停止后还能连上入口"
    assert not _entry_threads(), "停止后入口线程还在"

    assert srv.start(), "同一进程内第二次启动失败"
    try:
        assert _roundtrip(entry_port), "重启后连不上"
    finally:
        assert srv.stop(), "第二次 stop() 没等到退出"

    assert _port_is_free(entry_port)
    assert not _entry_threads()


def test_start_is_idempotent_while_running(entry_port):
    """已经在跑时再 start() 返回 False，不会起第二个入口。"""
    srv = ob_server.ONEBOT_SERVER
    assert srv.start()
    try:
        assert srv.start() is False
        assert len(_entry_threads()) == 1
    finally:
        assert srv.stop()


def test_stop_without_start_is_false(entry_port):
    """没起过就 stop()：返回 False，不抛。"""
    assert ob_server.ONEBOT_SERVER.stop() is False


def test_start_fails_when_port_is_taken(entry_port):
    """端口被别的进程占着时启动必须报失败，而不是留一个连不上的「运行中」。

    占位要靠 ``SO_EXCLUSIVEADDRUSE``：Windows 上只 bind 不 listen 的套接字挡不住
    后来的 ``SO_REUSEADDR`` 绑定（asyncio 创建监听时带着它），启动会「成功」。
    """
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if exclusive is None:
        pytest.skip("当前平台没有 SO_EXCLUSIVEADDRUSE，挡不住后者覆盖绑定")

    with socket.socket() as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        blocker.bind(("127.0.0.1", entry_port))
        blocker.listen(1)
        started = ob_server.ONEBOT_SERVER.start()

    assert started is False, "端口被占用时启动竟然报成功"
    assert not ob_server.ONEBOT_SERVER.is_running()
    assert not _entry_threads()


def main() -> int:
    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
