"""面板的事件消费端：把核心侧总线转给 WebSocket。

事件本体与历史缓冲在 ``flandora/runtime/events.py``，由核心层（``bridge_core`` /
``ob_protocol`` / ``ob_network``）直接发布——核心不再 import 表现层。本模块只做
一件事：订阅 :data:`flandora.runtime.events.EVENTS`，按浏览器能直接消费的帧格式
推出去；首帧快照与 30 秒保活 ping 也在这里。

日志中继（``attach_log_relay``）同样属于核心侧，由组合根在启动时挂上，本模块不碰。
"""

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from ..runtime import events
from . import auth


async def ws_endpoint(websocket: WebSocket):
    """实时推送：首帧快照，其后逐条转发；30 秒没事件就发一帧 ping 保活。"""
    if auth.enabled() and not auth.sessions.valid(websocket.cookies.get(auth.COOKIE_NAME)):
        # 先关再收：没登录的连接拿不到任何事件
        await websocket.close(code=4401)
        return
    await websocket.accept()
    q = events.EVENTS.subscribe()
    try:
        await websocket.send_json(events.EVENTS.panel_snapshot())
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
            except asyncio.TimeoutError:
                event = {"type": "ping"}
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except (RuntimeError, OSError):
        # 客户端断开后再 send：starlette 抛 RuntimeError，传输层抛 OSError
        # （uvicorn 的 ClientDisconnected 就是 IOError）。连接已经没了，不必刷屏。
        pass
    finally:
        events.EVENTS.unsubscribe(q)
