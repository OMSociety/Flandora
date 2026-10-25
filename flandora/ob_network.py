"""OneBot v11 网络适配器：HTTP 服务端 / WS 服务端 / 反向 WS / HTTP 事件上报。

一个桥接实例可以同时挂多个入口，每个入口一行配置（``ob_networks``）：

- ``ws_server``：等客户端连进来（最常用，MaiBot 一类正向 WS 客户端）。
- ``http_server``：暴露 HTTP API，客户端每次 POST 一个动作。
- ``ws_client``：主动连到机器人框架的 WS 服务端（AstrBot / NoneBot2 的反向 WS）。
- ``http_client``：把事件 POST 到框架的 HTTP 端点（Event 上报）。

每种入口都有 ``role``：``api`` 只处理动作请求，``event`` 只推事件，``universal``
两者都做。``message_format`` 可逐入口覆盖全局 ``ob_message_format``——同一个事件
对不同入口可以分别按数组或 CQ 字符串送出。

线程模型：所有适配器活在同一个事件循环里（``ob_server.OneBotServer`` 建的那个线程），
而事件由桥接线程推送。``broadcast()`` 因此是同步函数，内部用
``run_coroutine_threadsafe`` 切回循环，与旧 ``ws_pool.broadcast`` 的调用方式一致。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.asyncio.server import serve

from .action_catalog import (
    RETCODE_AUTH_FAILED,
    RETCODE_BAD_PARAM,
    RETCODE_INTERNAL,
    RETCODE_NOT_FOUND,
)

log = logging.getLogger("flandora")

KIND_WS_SERVER = "ws_server"
KIND_HTTP_SERVER = "http_server"
KIND_WS_CLIENT = "ws_client"
KIND_HTTP_CLIENT = "http_client"
KINDS = (KIND_WS_SERVER, KIND_HTTP_SERVER, KIND_WS_CLIENT, KIND_HTTP_CLIENT)

ROLE_API = "api"
ROLE_EVENT = "event"
ROLE_UNIVERSAL = "universal"
ROLES = (ROLE_API, ROLE_EVENT, ROLE_UNIVERSAL)

MAX_MESSAGE_SIZE = 16 * 1024 * 1024  # 16MB，允许大图片 base64


class NetworkConfigError(ValueError):
    """``ob_networks`` 里的某一项不合法。消息里带下标与字段名，便于直接改配置。"""


# ============ 配置规范化 ============


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")


def _as_int(value, default: int, *, minimum: int = 0, maximum: int = 65535) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, n))


def normalize_node(raw: dict, index: int) -> dict:
    """把配置里的一项规范化成一个入口。非法项抛 ``NetworkConfigError``。"""
    if not isinstance(raw, dict):
        raise NetworkConfigError(f"第 {index} 个入口不是对象：{raw!r}")
    kind = str(raw.get("type") or "").strip().lower()
    if kind not in KINDS:
        raise NetworkConfigError(
            f"第 {index} 个入口的 type={raw.get('type')!r} 不认识（可选：{'/'.join(KINDS)}）"
        )
    name = str(raw.get("name") or "").strip() or f"{kind}-{index}"
    node = {
        "type": kind,
        "name": name,
        "enabled": _as_bool(raw.get("enabled"), True),
        "path": str(raw.get("path") or "/").strip() or "/",
        "role": str(raw.get("role") or ROLE_UNIVERSAL).strip().lower(),
        "access_token": str(raw.get("access_token") or "").strip(),
        "message_format": str(raw.get("message_format") or "").strip().lower(),
        "timeout_ms": _as_int(raw.get("timeout_ms"), 5000, minimum=100, maximum=120000),
        "reconnect_interval_ms": _as_int(
            raw.get("reconnect_interval_ms"), 5000, minimum=1000, maximum=600000
        ),
        "host": str(raw.get("host") or "127.0.0.1").strip() or "127.0.0.1",
        "port": _as_int(raw.get("port"), 0, minimum=0, maximum=65535),
        "url": str(raw.get("url") or "").strip(),
    }
    if node["role"] not in ROLES:
        raise NetworkConfigError(
            f"入口 {name} 的 role={raw.get('role')!r} 不认识（可选：{'/'.join(ROLES)}）"
        )
    if node["message_format"] and node["message_format"] not in ("array", "string"):
        raise NetworkConfigError(
            f"入口 {name} 的 message_format={raw.get('message_format')!r} 不认识"
            "（可选：array / string，留空表示跟随全局）"
        )
    if kind in (KIND_WS_SERVER, KIND_HTTP_SERVER):
        if not node["port"]:
            raise NetworkConfigError(f"入口 {name} 缺少 port")
    else:
        if not node["url"]:
            raise NetworkConfigError(f"入口 {name} 缺少 url")
        parsed = urlparse(node["url"])
        if parsed.scheme not in ("ws", "wss", "http", "https"):
            raise NetworkConfigError(f"入口 {name} 的 url 协议不认识：{node['url']!r}")
        if kind == KIND_WS_CLIENT and parsed.scheme not in ("ws", "wss"):
            raise NetworkConfigError(f"入口 {name} 是 ws_client，url 必须是 ws:// 或 wss://")
        if kind == KIND_HTTP_CLIENT and parsed.scheme not in ("http", "https"):
            raise NetworkConfigError(f"入口 {name} 是 http_client，url 必须是 http:// 或 https://")
    return node


def normalize_nodes(raw) -> list[dict]:
    """规范化整张 ``ob_networks``。非列表直接报错，不静默当成空。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise NetworkConfigError(f"ob_networks 必须是数组：{type(raw).__name__}")
    nodes: list[dict] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        node = normalize_node(item, i)
        if node["name"] in seen:
            # 名字用于状态展示与日志，重名会让人分不清哪个入口在报错
            raise NetworkConfigError(f"入口名字重复：{node['name']}")
        seen.add(node["name"])
        nodes.append(node)
    return nodes


def default_node(host: str, port: int, token: str) -> dict:
    """没有 ``ob_networks`` 时，按通用设置里的 host/port/token 造一个 WS 服务端。"""
    return normalize_node(
        {
            "type": KIND_WS_SERVER,
            "name": "default",
            "host": host,
            "port": port,
            "access_token": token,
            "role": ROLE_UNIVERSAL,
            "path": "/",
        },
        0,
    )


# ============ 事件按入口整形 ============


def format_for_node(payload: dict, node: dict, default_format: str = "array") -> dict:
    """按入口的 ``message_format`` 调整 ``message`` 字段。

    事件在桥接侧统一按数组构造，``raw_message`` 恒为 CQ 字符串，所以这里只需要
    「要不要把 message 换成 raw_message」。不带 message 的事件（通知、心跳）原样返回。
    """
    fmt = node.get("message_format") or default_format
    if fmt != "string":
        return payload
    if not isinstance(payload.get("message"), list):
        return payload
    out = dict(payload)
    out["message"] = payload.get("raw_message", "")
    return out


# ============ 适配器管理 ============


class NetworkManager:
    """持有全部入口的运行态。一个进程一个实例。"""

    def __init__(self):
        self._nodes: list[dict] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self._connections: dict[str, set] = {}  # 入口名 → 已连接的 WS
        self._clients: dict[str, object] = {}  # 入口名 → 反向 WS 连上的那个连接
        self._state: dict[str, str] = {}
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()
        self._started = False
        self._heartbeat_interval = 0

    # ---------- 配置 ----------

    def configure(self, nodes: list[dict]):
        self._nodes = list(nodes)
        for node in self._nodes:
            self._state.setdefault(node["name"], "stopped")
            self._connections.setdefault(node["name"], set())

    def nodes(self) -> list[dict]:
        return list(self._nodes)

    def local_readiness(self) -> tuple[bool, list[str]]:
        """本机监听是否已经就绪，返回 ``(全部就绪, 还没就绪的入口名)``。

        只有「服务端」类入口参与判定：它们绑不上端口就是本进程的问题，必须让
        启动方知道。反向 WS 与 HTTP 上报是客户端，连不上外部框架是常态，重连
        由监督任务负责，不该让本进程的启动等着它。
        """
        pending = [
            node["name"]
            for node in self._nodes
            if node["enabled"]
            and node["type"] in (KIND_WS_SERVER, KIND_HTTP_SERVER)
            and self._state.get(node["name"]) not in ("listening", "connected")
        ]
        return (not pending), pending

    @property
    def loop(self):
        return self._loop

    # ---------- 生命周期 ----------

    def start(self, loop: asyncio.AbstractEventLoop):
        """在给定循环里为每个启用的入口起一个监督任务。"""
        self._loop = loop
        with self._lock:
            if self._started:
                return
            self._started = True
        for node in self._nodes:
            if not node["enabled"]:
                self._state[node["name"]] = "disabled"
                continue
            self._tasks.append(
                loop.create_task(self._supervise(node), name=f"obnet-{node['name']}")
            )
        if not self._tasks:
            log.warning("[OB11] 没有启用的网络入口，事件无处可发")

        from . import config

        interval = int(getattr(config, "OB_HEARTBEAT_SECONDS", 0) or 0)
        if interval > 0 and self._tasks:
            self._heartbeat_interval = interval
            self._tasks.append(
                loop.create_task(self._heartbeat_loop(interval), name="obnet-heartbeat")
            )

    async def stop(self):
        # 先道别再拆线：生命周期的 disable 只能趁连接还在时发。
        # 与心跳开关无关——连接事件描述的是通道，不是心跳。
        try:
            from .ob_protocol import make_lifecycle_event

            await self._broadcast(make_lifecycle_event("disable"))
        except Exception as e:  # noqa: BLE001
            log.debug(f"[OB11] 生命周期 disable 未送达：{e}")
        with self._lock:
            self._started = False
            tasks = self._tasks
            self._tasks = []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for conns in self._connections.values():
            for ws in list(conns):
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
            conns.clear()
        self._clients.clear()

    async def _announce_connect(self, node: dict, ws):
        """给新连上的客户端补一条 lifecycle(connect)。

        lifecycle 描述的是「这条连接」的开始，不是进程的开始：框架晚连上来
        也应该先收到一条 connect，否则它无法确认事件通道已经就绪。
        """
        if node.get("role") == ROLE_API:
            return
        from .ob_protocol import make_lifecycle_event

        try:
            await _safe_send(ws, make_lifecycle_event("connect"), node["name"])
        except Exception as e:  # noqa: BLE001
            log.debug(f"[OB11] lifecycle(connect) 未送达：{e}")

    async def _heartbeat_loop(self, interval: int):
        """按间隔推 heartbeat。

        lifecycle 由每条连接自己发（见 `_announce_connect`）：它描述的是
        「这条连接」的开始，与进程启动无关。心跳投递失败只记 debug——
        某个入口掉了由它的监督任务负责重连，不该让心跳循环自己退出。
        """
        from .ob_protocol import make_heartbeat_event

        try:
            while True:
                await asyncio.sleep(interval)
                status = self._heartbeat_status()
                await self._broadcast(make_heartbeat_event(interval, **status))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error(f"[OB11] 心跳循环异常退出：{e}")

    @staticmethod
    def _heartbeat_status() -> dict:
        """心跳里的状态：判定只有一份，见 ``RUNTIME.online_flags()``。"""
        from .runtime import RUNTIME

        return RUNTIME.online_flags()

    async def _supervise(self, node: dict):
        """起一个入口，出错就记下来并退避重试——单个入口挂掉不该拖垮其余入口。"""
        handler = {
            KIND_WS_SERVER: self._serve_ws,
            KIND_HTTP_SERVER: self._serve_http,
            KIND_WS_CLIENT: self._run_ws_client,
            KIND_HTTP_CLIENT: self._run_http_client,
        }[node["type"]]
        delay = 3.0
        while True:
            try:
                self._state[node["name"]] = "starting"
                self._errors.pop(node["name"], None)
                await handler(node)
                # 正常返回（例如服务器被关掉）：不重试
                self._state[node["name"]] = "stopped"
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._state[node["name"]] = "error"
                self._errors[node["name"]] = f"{type(e).__name__}: {e}"
                log.error(f"[OB11] 入口 {node['name']} 异常，{delay:.0f}s 后重试：{e}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)

    # ---------- WS 服务端 ----------

    async def _serve_ws(self, node: dict):
        async def handler(ws):
            await self._handle_ws(node, ws)

        def process_request(connection, request):
            return _authorize(node, request)

        self._state[node["name"]] = "starting"
        async with serve(
            handler,
            node["host"],
            node["port"],
            process_request=process_request,
            max_size=MAX_MESSAGE_SIZE,
        ):
            # 状态与日志放在绑定之后：绑不上端口时这里不该已经报「监听」
            self._state[node["name"]] = "listening"
            log.info(
                f"[OB11] WS 服务端 {node['name']} 监听 {node['host']}:{node['port']}"
                f"{node['path']}（role={node['role']}，token={'有' if node['access_token'] else '无'}）"
            )
            await asyncio.Future()  # 由 stop() 取消

    async def _handle_ws(self, node: dict, ws):

        name = node["name"]
        path = getattr(getattr(ws, "request", None), "path", "") or ""
        want = node["path"]
        if want != "/" and path.split("?")[0] != want:
            log.warning(f"[OB11] 入口 {name} 只收 {want}，拒绝 {path}")
            await ws.close(code=1008, reason="path mismatch")
            return

        conns = self._connections.setdefault(name, set())
        conns.add(ws)
        self._state[name] = "connected"
        log.info(f"[OB11] 客户端连上入口 {name}: {ws.remote_address}")
        _notify_status()
        await self._announce_connect(node, ws)
        try:
            async for raw in ws:
                if node["role"] == ROLE_EVENT:
                    log.warning(f"[OB11] 入口 {name} 是 event 角色，收到动作请求已忽略")
                    continue
                await _serve_action(ws, raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:  # noqa: BLE001
            log.error(f"[OB11] 入口 {name} 连接异常：{e}")
        finally:
            conns.discard(ws)
            if not conns and self._state.get(name) == "connected":
                self._state[name] = "listening"
            log.info(f"[OB11] 入口 {name} 剩余客户端：{len(conns)}")
            _notify_status()

    # ---------- HTTP 服务端 ----------

    async def _serve_http(self, node: dict):
        import uvicorn

        from .ob_protocol import _handle_ob_api

        path = node["path"]
        token = node["access_token"]

        async def app(scope, receive, send):
            if scope["type"] != "http":
                return
            req_path = scope.get("path") or "/"
            if path != "/" and req_path != path:
                await _asgi_json(send, 404, {"status": "failed", "retcode": RETCODE_NOT_FOUND})
                return
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            query = {
                k: v[0] for k, v in parse_qs(scope.get("query_string", b"").decode()).items() if v
            }
            if token and headers.get("authorization", "") != f"Bearer {token}":
                if query.get("access_token") != token:
                    await _asgi_json(
                        send, 401, {"status": "failed", "retcode": RETCODE_AUTH_FAILED}
                    )
                    return
            body = b""
            if scope.get("method") == "POST":
                while True:
                    msg = await receive()
                    body += msg.get("body", b"")
                    if not msg.get("more_body"):
                        break
            payload = {}
            if body:
                try:
                    payload = json.loads(body)
                except ValueError:
                    await _asgi_json(send, 400, {"status": "failed", "retcode": RETCODE_BAD_PARAM})
                    return
                if not isinstance(payload, dict):
                    await _asgi_json(send, 400, {"status": "failed", "retcode": RETCODE_BAD_PARAM})
                    return
            if not payload.get("action"):
                # 没带 action 的 GET：把 query 里的参数当作动作参数
                action = query.get("action") or ""
                if not action:
                    await _asgi_json(
                        send,
                        400,
                        {
                            "status": "failed",
                            "retcode": RETCODE_BAD_PARAM,
                            "message": "缺少 action",
                        },
                    )
                    return
                params = {k: v for k, v in query.items() if k not in ("action", "echo")}
                payload = {"action": action, "params": params, "echo": query.get("echo", "")}
            resp = await _handle_ob_api(payload)
            await _asgi_json(send, 200, resp or {})

        config_ = uvicorn.Config(
            app,
            host=node["host"],
            port=node["port"],
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        server = uvicorn.Server(config_)
        task = asyncio.ensure_future(server.serve())
        # uvicorn 在 serve() 内部才绑定端口：等它真的 started 再报「监听」
        while not server.started:
            if task.done():
                task.result()  # 起不来时把异常交给监督任务
            await asyncio.sleep(0.05)
        self._state[node["name"]] = "listening"
        log.info(
            f"[OB11] HTTP API {node['name']} 监听 {node['host']}:{node['port']}{path}"
            f"（token={'有' if token else '无'}）"
        )
        await task

    # ---------- 反向 WS ----------

    async def _run_ws_client(self, node: dict):
        name = node["name"]
        url = node["url"]
        while True:
            try:
                headers = {}
                if node["access_token"]:
                    headers["Authorization"] = f"Bearer {node['access_token']}"
                async with websockets.connect(
                    url,
                    additional_headers=headers or None,
                    max_size=MAX_MESSAGE_SIZE,
                    open_timeout=node["timeout_ms"] / 1000,
                ) as ws:
                    self._clients[name] = ws
                    self._state[name] = "connected"
                    log.info(f"[OB11] 已连上反向 WS {name}: {url}")
                    _notify_status()
                    await self._announce_connect(node, ws)
                    await self._read_ws_client(node, ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._state[name] = "reconnecting"
                self._errors[name] = f"{type(e).__name__}: {e}"
                log.warning(
                    f"[OB11] 反向 WS {name} 断开，{node['reconnect_interval_ms']}ms 后重连：{e}"
                )
            finally:
                self._clients.pop(name, None)
            await asyncio.sleep(max(1.0, node["reconnect_interval_ms"] / 1000))

    async def _read_ws_client(self, node: dict, ws):
        async for raw in ws:
            if node["role"] == ROLE_EVENT:
                continue
            await _serve_action(ws, raw)

    # ---------- HTTP 上报 ----------

    async def _run_http_client(self, node: dict):
        self._state[node["name"]] = "ready"
        log.info(f"[OB11] HTTP 事件上报 {node['name']}: {node['url']}")
        await asyncio.Future()  # 只在 broadcast 时发请求

    # ---------- 推送 ----------

    def broadcast(self, payload: dict) -> int:
        """向所有入口推送一条事件，返回投递成功数。可从任意线程调用。"""
        loop = self._loop
        if loop is None or not self._nodes:
            return 0
        try:
            fut = asyncio.run_coroutine_threadsafe(self._broadcast(payload), loop)
            return fut.result(timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[OB11] 事件推送失败：{e}")
            return 0

    async def _broadcast(self, payload: dict) -> int:
        from . import config

        sent = 0
        for node in self._nodes:
            if not node["enabled"]:
                continue
            if node["role"] == ROLE_API:
                continue  # 只收动作的入口不推事件
            body = format_for_node(payload, node, config.OB_MESSAGE_FORMAT)
            if node["type"] == KIND_HTTP_CLIENT:
                sent += await self._post_event(node, body)
                continue
            if node["type"] == KIND_WS_CLIENT:
                ws = self._clients.get(node["name"])
                if ws is not None and await _safe_send(ws, body, node["name"]):
                    sent += 1
                continue
            for ws in list(self._connections.get(node["name"], ())):
                if await _safe_send(ws, body, node["name"]):
                    sent += 1
        return sent

    async def _post_event(self, node: dict, body: dict) -> int:
        headers = {"Content-Type": "application/json"}
        if node["access_token"]:
            headers["Authorization"] = f"Bearer {node['access_token']}"
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_post_json, node["url"], body, headers, node["timeout_ms"]),
                timeout=node["timeout_ms"] / 1000 + 1,
            )
            return 1
        except Exception as e:  # noqa: BLE001
            log.warning(f"[OB11] HTTP 上报 {node['name']} 失败：{e}")
            return 0

    # ---------- 状态 ----------

    def status(self) -> list[dict]:
        out = []
        with self._lock:
            for node in self._nodes:
                name = node["name"]
                conns = self._connections.get(name, set())
                count = len(conns) + (1 if self._clients.get(name) else 0)
                out.append(
                    {
                        "name": name,
                        "type": node["type"],
                        "enabled": node["enabled"],
                        "role": node["role"],
                        "path": node["path"],
                        "state": self._state.get(name, "stopped"),
                        "connections": count,
                        "target": _target(node),
                        "error": self._errors.get(name, ""),
                        "message_format": node["message_format"] or "（跟随全局）",
                    }
                )
        return out


async def _asgi_json(send, status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _target(node: dict) -> str:
    if node["type"] in (KIND_WS_SERVER, KIND_HTTP_SERVER):
        return f"{node['host']}:{node['port']}{node['path']}"
    return node["url"]


def _authorize(node: dict, request):
    """WS 握手鉴权：Bearer 头或 ?access_token=。不通过就回 401。"""
    token = node["access_token"]
    if not token:
        return None
    auth = request.headers.get("Authorization", "")
    if auth == f"Bearer {token}":
        return None
    if _query_param(request, "access_token") == token:
        return None
    log.warning(f"[OB11] 入口 {node['name']} 鉴权失败：Authorization={auth!r}")
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    return Response(401, "Unauthorized", Headers({"Connection": "close"}), b"Unauthorized")


def _http_authorized(node: dict, request) -> bool:
    token = node["access_token"]
    if not token:
        return True
    if request.headers.get("authorization", "") == f"Bearer {token}":
        return True
    return request.query_params.get("access_token") == token


def _query_param(request, key: str) -> str:
    """取握手请求的 query 参数（请求对象只给出 path，查询串从 path 里解析）。"""
    path = getattr(request, "path", "") or ""
    values = parse_qs(urlparse(path).query).get(key) or [""]
    return values[0]


async def _serve_action(ws, raw: str):
    """处理一条客户端动作请求，把响应发回同一连接。"""
    from .ob_protocol import _handle_ob_api

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("[OB11] 收到无效 JSON")
        return
    if not isinstance(data, dict):
        return
    try:
        resp = await _handle_ob_api(data)
    except Exception as e:  # noqa: BLE001
        log.error(f"[OB11] 处理 API 异常：{e}")
        resp = {
            "status": "failed",
            "retcode": RETCODE_INTERNAL,
            "data": {},
            "message": f"内部错误：{e}",
            "echo": data.get("echo", ""),
        }
    if resp is not None:
        await _safe_send(ws, resp, "?")


async def _safe_send(ws, payload: dict, name: str) -> bool:
    try:
        await ws.send(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning(f"[OB11] 入口 {name} 发送失败：{e}")
        return False


def _post_json(url: str, body: dict, headers: dict, timeout_ms: int) -> int:
    import requests

    resp = requests.post(url, json=body, headers=headers, timeout=timeout_ms / 1000)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return 1


def _notify_status():
    try:
        from .webui import monitor

        monitor.notify_status()
    except Exception:  # noqa: BLE001
        pass


# ============ 进程内单例 ============

_manager: NetworkManager | None = None
_manager_lock = threading.Lock()


def manager() -> NetworkManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = NetworkManager()
        return _manager


def broadcast(payload: dict) -> int:
    """推事件的统一入口（旧 ``ws_pool.broadcast`` 的位置）。"""
    return manager().broadcast(payload)
