"""桥接运行期对象的唯一持有者。

发送器、桥接实例、OneBot 入口的占用状态、新版本提示都比一次请求活得久、又比
进程短，统一收在 :class:`AppRuntime` 上：读写都过同一个锁，调用方拿到的始终是
同一份事实。

架构边界：进程级状态只允许放在 ``flandora/runtime/``（本对象与
``lifecycle.LIFECYCLE``），其它模块不得自建共享状态。
"""

from __future__ import annotations

import threading
from typing import Any

from .lifecycle import LIFECYCLE


class AppRuntime:
    """桥接运行期可变对象的持有者；读写都过内部锁。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sender: Any = None
        self._bridge: Any = None
        self._ob_server_claimed = False
        self._update_available: dict[str, Any] | None = None

    @property
    def sender(self):
        """当前的发送器；桥接没起或还没初始化完时为 None。"""
        with self._lock:
            return self._sender

    @sender.setter
    def sender(self, value) -> None:
        with self._lock:
            self._sender = value

    @property
    def bridge(self):
        """当前的桥接实例（``bridge_core.Flandora``）；收件线程未就绪时为 None。"""
        with self._lock:
            return self._bridge

    @bridge.setter
    def bridge(self, value) -> None:
        with self._lock:
            self._bridge = value

    def claim_ob_server(self) -> bool:
        """原子 test-and-set：拿到 OneBot 入口所有权时返回 True，已有线程时返回 False。"""
        with self._lock:
            if self._ob_server_claimed:
                return False
            self._ob_server_claimed = True
            return True

    def release_ob_server(self) -> None:
        """交还 OneBot 入口所有权（线程退出时调用）。"""
        with self._lock:
            self._ob_server_claimed = False

    @property
    def update_available(self) -> dict[str, Any] | None:
        """后台查到的新版本信息；没有新版或还没查到时为 None。"""
        with self._lock:
            return self._update_available

    @update_available.setter
    def update_available(self, value: dict[str, Any] | None) -> None:
        with self._lock:
            self._update_available = value

    def online_flags(self) -> dict[str, bool]:
        """``online`` 与 ``good`` 的唯一判定：心跳与 ``get_status`` 共用这一份。

        online = 桥接已起、未暂停，且收件侧实例已挂上；good 再要求发送器就绪。
        两处各自判一次会让同一个暂停状态在两个接口里说法不同。
        """
        snap = LIFECYCLE.snapshot()
        with self._lock:
            bridge_ready = self._bridge is not None
            sender_ready = self._sender is not None
        online = snap.is_online and bridge_ready
        return {"online": online, "good": online and sender_ready}


RUNTIME = AppRuntime()
