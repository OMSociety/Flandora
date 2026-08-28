"""
运行监控模块：事件总线 + 历史缓冲 + 日志转发。

- EventBus：线程安全事件分发，供 Web UI 的 WebSocket 实时推送
- LogRelay：logging.Handler，把桥接日志转发到总线（实时日志页面）
- record_inbound / record_outbound / bump：消息事件记录与计数（消息监控与统计）

所有公开函数内部都做了异常吞没，监控逻辑绝不影响桥接主流程。
"""

import asyncio
import collections
import logging
import threading
import time

log = logging.getLogger("ob11-bridge")


class EventBus:
    """进程内事件总线。

    publish() 可从任意线程调用：
    - log / msg 类型事件会存入历史缓冲（供新连接的 WS 客户端快照）
    - 若 uvicorn 事件循环已注册，则通过 call_soon_threadsafe 转发给所有订阅者
    """

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subs: set[asyncio.Queue] = set()
        self.logs: collections.deque = collections.deque(maxlen=500)
        self.messages: collections.deque = collections.deque(maxlen=300)
        self.counters = {"recv": 0, "pushed": 0, "sent": 0, "dropped": 0}
        self._lock = threading.Lock()

    def set_loop(self, loop):
        self.loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subs.discard(q)

    def publish(self, event: dict):
        try:
            etype = event.get("type", "")
            with self._lock:
                if etype == "log":
                    self.logs.append(event)
                elif etype == "msg":
                    self.messages.appendleft(event)  # 新的在前
                    if event.get("dir") == "in":
                        self.counters["recv"] += 1
                    elif event.get("dir") == "out":
                        self.counters["sent"] += 1
            loop = self.loop
            if loop is not None and self.subs:
                for q in list(self.subs):
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, event)
                    except Exception:
                        pass
        except Exception:
            pass

    def bump(self, key: str, n: int = 1):
        with self._lock:
            if key in self.counters:
                self.counters[key] += n


bus = EventBus()


class LogRelay(logging.Handler):
    """把 ob11-bridge 日志记录转发到事件总线。"""

    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            bus.publish({
                "type": "log",
                "ts": record.created,
                "level": record.levelname,
                "text": self.format(record),
            })
        except Exception:
            pass


_relay_attached = False


def attach_log_relay():
    """给桥接相关 logger 挂上实时转发（幂等）。

    ob11-bridge：桥接主逻辑；weflow-bridge：UIA 发送器（uia_sender）。
    """
    global _relay_attached
    if _relay_attached:
        return
    for name in ("ob11-bridge", "weflow-bridge"):
        target = logging.getLogger(name)
        if not any(isinstance(h, LogRelay) for h in target.handlers):
            target.addHandler(LogRelay())
    _relay_attached = True


# ============ 消息事件记录（供 bridge_core / ob_protocol 调用） ============


def record_inbound(contact: str, sender: str, kind: str, text: str):
    try:
        bus.publish({
            "type": "msg",
            "ts": time.time(),
            "dir": "in",
            "kind": kind,
            "contact": contact or "",
            "sender": sender or "",
            "text": (text or "")[:200],
        })
    except Exception:
        pass


def record_outbound(contact: str, kind: str, text: str):
    try:
        bus.publish({
            "type": "msg",
            "ts": time.time(),
            "dir": "out",
            "kind": kind,
            "contact": contact or "",
            "sender": "",
            "text": (text or "")[:200],
        })
    except Exception:
        pass


def notify_status():
    """通知前端状态有变化（前端收到后重新拉取 /api/status）。"""
    try:
        bus.publish({"type": "status"})
    except Exception:
        pass


def bump(key: str, n: int = 1):
    """计数器 +n（recv/pushed/sent/dropped）。永不抛异常。"""
    try:
        bus.bump(key, n)
    except Exception:
        pass
