"""保存网络入口后立刻生效：新增的起、删掉的停、改过的重建、没动的不动。

入口改动以前要重启桥接才生效，面板上那条「待保存」写的就是「文件里有、运行态里
没有」。这组用例用真端口把 apply 的四种情况各跑一遍，最后再走一次「保存配置 →
运行态跟着变」的完整链路。
"""

import asyncio
import socket

from flandora import config, ob_network


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _node(port: int, name: str = "default", **extra) -> dict:
    return ob_network.normalize_node(
        {"type": "ws_server", "name": name, "host": "127.0.0.1", "port": port, **extra}, 0
    )


async def _wait_listening(mgr: ob_network.NetworkManager, names: list[str]) -> None:
    """等到「正在监听的入口正好是这几个」为止。"""
    for _ in range(300):
        live = sorted(n["name"] for n in mgr.status() if n["state"] == "listening")
        if live == sorted(names):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"入口状态没到位：{mgr.status()}")


def test_apply_starts_stops_and_rebuilds_entries():
    from websockets.asyncio.client import connect

    async def main():
        first, second, moved = _free_port(), _free_port(), _free_port()
        mgr = ob_network.NetworkManager()
        mgr.configure([_node(first)])
        mgr.start(asyncio.get_running_loop())
        try:
            await _wait_listening(mgr, ["default"])

            # 新增一条入口：保存后立刻监听，不必重启桥接
            await asyncio.wrap_future(mgr.apply([_node(first), _node(second, "extra")]))
            await _wait_listening(mgr, ["default", "extra"])

            # 改端口：那条入口按新配置重建
            await asyncio.wrap_future(mgr.apply([_node(moved), _node(second, "extra")]))
            await _wait_listening(mgr, ["default", "extra"])
            targets = {n["name"]: n["target"] for n in mgr.status()}
            assert targets["default"] == f"127.0.0.1:{moved}/"
            assert targets["extra"] == f"127.0.0.1:{second}/"

            # 没变的入口不重建：连着的客户端不会被一次保存踢下线
            async with connect(f"ws://127.0.0.1:{moved}/", open_timeout=10) as ws:
                await asyncio.wait_for(ws.recv(), timeout=10)  # lifecycle(connect)
                await asyncio.wrap_future(mgr.apply([_node(moved)]))
                assert [n["connections"] for n in mgr.status()] == [1]
                await asyncio.wait_for(ws.ping(), timeout=10)  # 连接还是活的

            # 删掉一条：连接与监督任务一起收掉
            await asyncio.wrap_future(mgr.apply([]))
            await _wait_listening(mgr, [])
            assert mgr.status() == []

            # 停用：留在配置里但不监听
            await asyncio.wrap_future(mgr.apply([_node(moved, enabled=False)]))
            states = {n["name"]: n["state"] for n in mgr.status()}
            assert states == {"default": "disabled"}
        finally:
            await mgr.stop()

    asyncio.run(main())


def test_apply_before_start_only_records_the_config():
    """入口线程还没起来时 apply 不排任务，只把配置镜像换掉。"""
    mgr = ob_network.NetworkManager()
    mgr.configure([_node(1)])

    assert mgr.apply([_node(2, "extra")]) is None
    assert [n["name"] for n in mgr.status()] == ["extra"]


def test_saving_config_applies_entries_live(tmp_path):
    """保存 ob_networks 后运行态立刻多出那条入口。"""

    async def main():
        port = _free_port()
        with config.temporary(tmp_path / "config.json") as store:
            mgr = ob_network.NetworkManager()
            mgr.configure([])
            mgr.start(asyncio.get_running_loop())
            try:
                assert mgr.status() == []
                store.update(
                    {
                        "ob_networks": [
                            {
                                "type": "ws_server",
                                "name": "saved",
                                "host": "127.0.0.1",
                                "port": port,
                            }
                        ]
                    }
                )
                await _wait_listening(mgr, ["saved"])
            finally:
                await mgr.stop()

    asyncio.run(main())
