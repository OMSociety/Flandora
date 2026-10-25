"""OneBot v11 网络入口的起停器。

入口本身（HTTP 服务端 / WS 服务端 / 反向 WS / HTTP 上报）在 ``ob_network`` 里。
这里只持有一个专用事件循环线程：``start()`` 把 ``config.OB_NETWORKS`` 交给管理器，
等服务端入口真的进入监听才报成功，``stop()`` 通知循环停下、等线程退出并释放端口。

只有 ``ONEBOT_SERVER`` 一个实例：启停必须成对，入口线程不允许无人持有地空转。
"""

from __future__ import annotations

import asyncio
import logging
import threading

from . import config, ob_network
from .runtime import RUNTIME

log = logging.getLogger("flandora")

# start() 等入口就绪的上限；事件循环与监听套接字本该在毫秒级就绪
START_TIMEOUT = 5.0


class OneBotServer:
    """OneBot 网络入口线程的唯一持有者。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._startup = threading.Event()
        self._startup_ok = False

    def start(self) -> bool:
        """起线程并等入口就绪；线程已在跑或起不来时返回 False。"""
        if not RUNTIME.claim_ob_server():
            return False
        with self._lock:
            self._startup = threading.Event()
            self._startup_ok = False
        thread = threading.Thread(target=self._run, daemon=True, name="ob11-server")
        with self._lock:
            self._thread = thread
        thread.start()
        if not self._startup.wait(timeout=START_TIMEOUT):
            # 线程没在期限内就绪：停掉它并交还所有权，不让半个入口挂着
            log.error("[OB11] 网络入口 %.1f 秒内未就绪，放弃启动", START_TIMEOUT)
            self.stop(timeout=START_TIMEOUT)
            return False
        if not self._startup_ok:
            self.stop(timeout=START_TIMEOUT)
            return False
        return True

    def stop(self, timeout: float = 5.0) -> bool:
        """通知事件循环停下并等线程退出；线程没在跑时返回 False。

        返回 False 也可能是「已通知但线程超时未退」——两种情况都记了日志，
        调用方不必自己再判线程状态。超时后保留线程引用，便于再次 stop 重试。
        """
        with self._lock:
            thread = self._thread
            loop = self._loop
        if thread is None or not thread.is_alive():
            with self._lock:
                self._thread = None
                self._loop = None
            RUNTIME.release_ob_server()
            return False
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError as e:
                # 循环已经关闭：线程会在下一轮自行退出，仍然 join 等它收尾
                log.warning("[OB11] 事件循环已关闭，无法通知停止：%s", e)
        thread.join(timeout)
        if thread.is_alive():
            log.error("[OB11] 网络入口线程 %.1f 秒内未退出", timeout)
            return False
        with self._lock:
            self._thread = None
            self._loop = None
        RUNTIME.release_ob_server()
        return True

    def is_running(self) -> bool:
        with self._lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    # ---------------------------------------------------------------- 线程体

    def _run(self) -> None:
        """入口线程：建循环 → 配入口 → 等监听就绪 → 常驻；循环停下后收尾。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # 循环引用先挂上：起不来时 start() 会调 stop()，那时也要能通知到这个循环
        with self._lock:
            self._loop = loop
        try:
            mgr = ob_network.manager()
            mgr.configure(config.OB_NETWORKS)
            mgr.start(loop)
            for node in mgr.nodes():
                log.info(
                    f"[OB11] 入口 {node['name']}（{node['type']}，role={node['role']}，"
                    f"{'启用' if node['enabled'] else '未启用'}）"
                )
            try:
                ready, pending = loop.run_until_complete(self._await_ready(mgr))
            except RuntimeError:
                # 等就绪的过程中被 stop() 叫停
                ready, pending = False, ["启动被中止"]
        except Exception as e:  # noqa: BLE001
            log.error(f"[OB11] 网络入口启动失败：{e}")
            self._startup.set()
            self._wind_down(loop)
            return
        if not ready:
            log.error(f"[OB11] 网络入口未在 {START_TIMEOUT:.0f} 秒内就绪：{'、'.join(pending)}")
            self._startup.set()
            self._wind_down(loop)
            return
        with self._lock:
            self._startup_ok = True
        self._startup.set()
        try:
            loop.run_forever()
        finally:
            self._wind_down(loop)

    async def _await_ready(self, mgr) -> tuple[bool, list[str]]:
        """等到服务端入口都进入监听，或超时。

        「线程起来了」不等于「端口在听」：绑不上端口时入口会退避重试，
        这时启动方若已经报成功，面板上是一个假运行状态。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + START_TIMEOUT
        while True:
            ready, pending = mgr.local_readiness()
            if ready:
                return True, []
            if loop.time() >= deadline:
                return False, pending
            await asyncio.sleep(0.05)

    def _wind_down(self, loop: asyncio.AbstractEventLoop) -> None:
        """停入口 → 关循环 → 交还所有权。启动失败与正常停止共用这一条收尾路径。"""
        try:
            loop.run_until_complete(ob_network.manager().stop())
        except Exception as e:  # noqa: BLE001
            log.error(f"[OB11] 网络入口停止失败：{e}")
        try:
            loop.close()
        except Exception as e:  # noqa: BLE001
            log.error(f"[OB11] 事件循环关闭失败：{e}")
        RUNTIME.release_ob_server()
        with self._lock:
            self._loop = None


ONEBOT_SERVER = OneBotServer()
