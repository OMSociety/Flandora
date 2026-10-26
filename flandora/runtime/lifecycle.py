"""进程生命周期状态机：启停阶段与暂停的唯一写者。

只有 :class:`Lifecycle` 的方法能改状态；其余模块一律通过
:meth:`Lifecycle.snapshot` 取一份不可变快照再判断，不直接读字段。

架构边界：进程级状态只允许放在 ``flandora/runtime/`` 的 ``LIFECYCLE`` 与
``RUNTIME`` 上（见 ``flandora/runtime/runtime.py``）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class RunState(Enum):
    """桥接所处的阶段。``STARTING`` 与 ``RUNNING`` 都算「已起」。"""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


@dataclass(frozen=True)
class Snapshot:
    """某一刻的运行状态快照。

    调用方拿到的是拷贝：本对象不会随后续状态变化而变，判断时不必再持锁。
    """

    state: RunState
    started_at: float | None
    paused: bool

    @property
    def is_active(self) -> bool:
        """已起或在起：``/api/status.running`` 与 ``get_status`` 的 ``online`` 用这个口径。"""
        return self.state in (RunState.STARTING, RunState.RUNNING)

    @property
    def is_online(self) -> bool:
        """在线 = 已起且未暂停：心跳与 ``/api/status.good`` 用这个口径。"""
        return self.is_active and not self.paused


class Lifecycle:
    """桥接生命周期状态机；本类是这些状态的唯一写者。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RunState.STOPPED
        self._started_at: float | None = None
        self._paused = False
        # 运行请求事件：begin_start 置位、begin_stop 清零，供收件循环与等待方判断去留
        self._run_requested = threading.Event()
        self._thread: threading.Thread | None = None

    def begin_start(self) -> bool:
        """进入 STARTING；已在起、已在跑或正在停时返回 False，调用方据此跳过重复启动。"""
        with self._lock:
            if self._state in (RunState.STARTING, RunState.RUNNING, RunState.STOPPING):
                return False
            self._state = RunState.STARTING
            self._paused = False
            self._started_at = time.time()
            self._run_requested.set()
            return True

    def mark_running(self) -> None:
        """STARTING → RUNNING：桥接线程真正跑起来之后调用。"""
        with self._lock:
            if self._state is RunState.STARTING:
                self._state = RunState.RUNNING

    def begin_stop(self) -> bool:
        """进入 STOPPING，并让 :meth:`should_continue` 立刻返回 False。

        已经停止或正在停止时返回 False。
        """
        with self._lock:
            if self._state in (RunState.STOPPED, RunState.STOPPING):
                return False
            self._state = RunState.STOPPING
            self._run_requested.clear()
            return True

    def mark_stopped(self) -> None:
        """STOPPING → STOPPED：收尾完成、桥接线程已退出（或确认没起）之后调用。"""
        with self._lock:
            self._state = RunState.STOPPED
            self._started_at = None
            self._paused = False
            self._run_requested.clear()
            self._thread = None

    def should_continue(self) -> bool:
        """收件循环是否该继续；``begin_stop`` 之后立刻为 False。"""
        return self._run_requested.is_set()

    def attach_thread(self, thread: threading.Thread) -> None:
        """登记承载桥接循环的线程，供 :meth:`join` 收尾。"""
        with self._lock:
            self._thread = thread

    def join(self, timeout: float) -> bool:
        """等桥接线程退出；超时或没有登记线程时如实返回 False。"""
        with self._lock:
            thread = self._thread
        if thread is None:
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def pause(self) -> bool:
        """置为暂停，返回是否真的切换（只有已起且未暂停时成立）。

        暂停的产品口径：它只让心跳与状态回报变成「已暂停/离线」（``online`` 与
        ``good`` 为 False、面板显示已暂停），收件与推送照常进行——是否停手由
        OneBot 客户端自己决定，桥接不改收件行为。
        """
        with self._lock:
            if not self._is_active_locked() or self._paused:
                return False
            self._paused = True
            return True

    def resume(self) -> bool:
        """取消暂停，返回是否真的切换（只有已起且处于暂停时成立）。"""
        with self._lock:
            if not self._is_active_locked() or not self._paused:
                return False
            self._paused = False
            return True

    def snapshot(self) -> Snapshot:
        """当前状态的一份不可变快照。"""
        with self._lock:
            return Snapshot(self._state, self._started_at, self._paused)

    def _is_active_locked(self) -> bool:
        return self._state in (RunState.STARTING, RunState.RUNNING)


LIFECYCLE = Lifecycle()
