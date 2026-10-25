"""OneBot v11 网络入口的启动器。

入口本身（HTTP 服务端 / WS 服务端 / 反向 WS / HTTP 上报）在 ``ob_network`` 里，
这里只负责：起一个专用事件循环线程，把 ``config.OB_NETWORKS`` 交给管理器，
然后守着循环直到进程退出。
"""

import asyncio
import logging

from . import config, ob_network

log = logging.getLogger("flandora")


def _run_ob_server():
    """后台线程：运行 OneBot 网络入口。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_ob_server_main())
    except Exception as e:  # noqa: BLE001
        log.error(f"[OB11] 网络入口线程退出：{e}")
    finally:
        try:
            loop.run_until_complete(ob_network.manager().stop())
        except Exception:  # noqa: BLE001
            pass
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass


async def _ob_server_main():
    """按配置起所有入口，然后常驻。取消即停。"""
    mgr = ob_network.manager()
    mgr.configure(config.OB_NETWORKS)
    mgr.start(asyncio.get_running_loop())
    for node in mgr.nodes():
        log.info(
            f"[OB11] 入口 {node['name']}（{node['type']}，role={node['role']}，"
            f"{'启用' if node['enabled'] else '未启用'}）"
        )
    try:
        await asyncio.Future()
    finally:
        await mgr.stop()
