"""核心侧事件总线：收件、出站与状态变更往这里发，面板侧只消费。

依赖方向：``bridge_core`` / ``ob_protocol`` / ``ob_network`` 是核心层，不允许
反向 import 表现层（``webui``）。事件在核心侧落地，面板再从 :data:`EVENTS`
订阅并转成 WebSocket 推送，因此本模块只依赖标准库。

监控不能拖慢桥接：``publish`` 与各记录函数都不抛异常（历史实现即如此），
调用方不必为监控失败写兜底。但「面板没连上」不等于「事件总线不存在」——
核心侧缺了本模块就是接线断了，按失败处理，不再静默降级。
"""

import asyncio
import collections
import logging
import threading
import time

log = logging.getLogger("flandora")


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

    def logs_copy(self) -> list[dict]:
        """历史日志的一份拷贝（读取方各自切片，不持有总线内部队列）。"""
        with self._lock:
            return list(self.logs)

    def messages_copy(self) -> list[dict]:
        """最近消息的一份拷贝（新的在前）。"""
        with self._lock:
            return list(self.messages)

    def counters_copy(self) -> dict[str, int]:
        with self._lock:
            return dict(self.counters)

    def panel_snapshot(self) -> dict:
        """面板新连接的首帧：历史日志与最近消息在同一把锁下取出。"""
        with self._lock:
            return {
                "type": "snapshot",
                "logs": list(self.logs),
                "messages": list(self.messages),
            }


EVENTS = EventBus()


class LogRelay(logging.Handler):
    """把 flandora 日志记录转发到事件总线。"""

    def __init__(self):
        super().__init__()
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record):
        try:
            EVENTS.publish(
                {
                    "type": "log",
                    "ts": record.created,
                    "level": record.levelname,
                    "text": self.format(record),
                }
            )
        except Exception:
            pass


_relay_attached = False


def attach_log_relay():
    """给桥接相关 logger 挂上实时转发（幂等）。

    flandora：桥接主逻辑；flandora-uia：UIA 发送器（uia_sender）。
    """
    global _relay_attached
    if _relay_attached:
        return
    for name in ("flandora", "flandora-uia"):
        target = logging.getLogger(name)
        if not any(isinstance(h, LogRelay) for h in target.handlers):
            target.addHandler(LogRelay())
    _relay_attached = True


# ============ 消息事件记录（供 bridge_core / ob_protocol / 面板调用） ============


def record_inbound(contact: str, sender: str, kind: str, text: str):
    try:
        EVENTS.publish(
            {
                "type": "msg",
                "ts": time.time(),
                "dir": "in",
                "kind": kind,
                "contact": contact or "",
                "sender": sender or "",
                "text": (text or "")[:200],
            }
        )
    except Exception:
        pass


def record_outbound(contact: str, kind: str, text: str):
    try:
        EVENTS.publish(
            {
                "type": "msg",
                "ts": time.time(),
                "dir": "out",
                "kind": kind,
                "contact": contact or "",
                "sender": "",
                "text": (text or "")[:200],
            }
        )
    except Exception:
        pass


def notify_status():
    """通知前端状态有变化（前端收到后重新拉取 /api/status）。"""
    try:
        EVENTS.publish({"type": "status"})
    except Exception:
        pass


def bump(key: str, n: int = 1):
    """计数器 +n（recv/pushed/sent/dropped）。永不抛异常。"""
    try:
        EVENTS.bump(key, n)
    except Exception:
        pass
