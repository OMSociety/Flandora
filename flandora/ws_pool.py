"""
共享的 WS 连接池：bridge 服务端（ob_server）与协议处理（ob_protocol）共用。
避免循环导入。
"""

import asyncio
import json
import logging
import threading

log = logging.getLogger("ob11-bridge")

# 已连接的 WS 客户端（多个 MaiBot 实例可同时连接）
_clients: set = set()
_loop: asyncio.AbstractEventLoop | None = None
_ws_ready = threading.Event()


def broadcast(payload: dict) -> int:
    """向所有连接广播 JSON 载荷，返回成功发送数。"""
    if not _clients or _loop is None:
        return 0
    text = json.dumps(payload, ensure_ascii=False)
    sent = 0
    for ws in list(_clients):
        try:
            fut = asyncio.run_coroutine_threadsafe(ws.send(text), _loop)
            fut.result(timeout=5)
            sent += 1
        except Exception as e:
            log.warning(f"[OB11] 发送失败: {e}")
    return sent
