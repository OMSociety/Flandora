"""
OneBot v11 WebSocket 服务端模块（对接 MaiBot 微信适配器）。

MaiBot 的微信适配器作为 WS 客户端连接本服务端：
- 接收 MaiBot 发来的 action 请求（send_msg 等），处理后响应 echo
- 向所有连接的 MaiBot 客户端推送 OneBot 事件（消息等）
- 支持 Bearer token 校验
"""

import asyncio
import json
import logging
import threading

import websockets
from websockets.asyncio.server import serve

import config
from ws_pool import _clients, _ws_ready, broadcast
import ws_pool

log = logging.getLogger("ob11-bridge")

# 兼容旧引用
push_event_broadcast = broadcast


def _run_ob_server():
    """后台线程：运行 OneBot WS 服务端。"""
    ws_pool._loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ws_pool._loop)
    try:
        ws_pool._loop.run_until_complete(_ob_server_main())
    finally:
        try:
            ws_pool._loop.close()
        except Exception:
            pass
        _ws_ready.clear()


def _token_ok(connection, request):
    """校验 Authorization: Bearer <token> 请求头。"""
    if not config.OB_SERVER_TOKEN:
        return None
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {config.OB_SERVER_TOKEN}"
    if auth == expected:
        return None
    # 也兼容 ?access_token= 形式
    qs = request.query
    if qs and qs.get("access_token") == config.OB_SERVER_TOKEN:
        return None
    log.warning(f"[OB11] 鉴权失败: Authorization={auth!r}")
    from websockets.http11 import Response
    from websockets.datastructures import Headers
    return Response(401, "Unauthorized", Headers({"Connection": "close"}), b"Unauthorized")


async def _ws_handler(ws):
    """处理单个 WS 客户端连接。"""
    from ob_protocol import _handle_ob_api  # 延迟导入，避免循环

    peer = ws.remote_address
    log.info(f"[OB11] ✅ MaiBot 客户端已连接: {peer}")
    _clients.add(ws)
    _ws_ready.set()
    try:
        from webui import monitor
        monitor.notify_status()
    except Exception:
        pass
    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("[OB11] 收到无效 JSON")
                continue
            # 直接 await 处理，把响应发回给发起者（同一连接）
            try:
                resp = await _handle_ob_api(data)
                if resp is not None:
                    await ws.send(json.dumps(resp, ensure_ascii=False))
            except Exception as e:
                log.error(f"[OB11] 处理 API 异常: {e}")
                # 尝试回错误响应避免客户端超时
                try:
                    await ws.send(json.dumps({
                        "status": "failed", "retcode": 1, "data": {},
                        "echo": data.get("echo", ""),
                    }, ensure_ascii=False))
                except Exception:
                    pass
    except websockets.exceptions.ConnectionClosed:
        log.info(f"[OB11] 客户端断开: {peer}")
    except Exception as e:
        log.error(f"[OB11] 连接异常: {e}")
    finally:
        _clients.discard(ws)
        log.info(f"[OB11] 剩余客户端: {len(_clients)}")
        try:
            from webui import monitor
            monitor.notify_status()
        except Exception:
            pass


async def _ob_server_main():
    """WebSocket 服务端主协程。"""
    host = config.OB_SERVER_HOST
    port = config.OB_SERVER_PORT
    log.info(f"[OB11] OneBot WS 服务端监听 {host}:{port}（token={config.OB_SERVER_TOKEN or '无'}）")
    async with serve(
        _ws_handler,
        host,
        port,
        process_request=_token_ok,
        max_size=16 * 1024 * 1024,  # 16MB，允许大图片 base64
    ) as server:
        await server.serve_forever()
