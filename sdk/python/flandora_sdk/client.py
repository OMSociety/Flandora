"""客户端：HTTP 直连、正向 / 反向 WebSocket 都能用。

三种用法共享一套语义：`call(action, **params)` 拿到 `data`，`status != "ok"` 抛
`ApiError`，连不上或超时抛 `TransportError`。WebSocket 客户端额外负责 echo 关联与
事件分发——收到的消息没有匹配的 echo 就交给注册的处理器。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable, Iterable

import requests
from websockets.asyncio.client import connect as async_connect
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as sync_connect

from . import message as msg
from .errors import ApiError, TransportError

DEFAULT_HTTP_URL = "http://127.0.0.1:5700/"
DEFAULT_WS_URL = "ws://127.0.0.1:7999"
Handler = Callable[[dict[str, Any]], None]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _check(action: str, payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise TransportError(f"{action}：响包不是 JSON 对象")
    retcode = int(payload.get("retcode") or 0)
    if payload.get("status") != "ok":
        text = payload.get("message") or payload.get("wording") or "桥接返回失败"
        raise ApiError(action, retcode, str(text), payload)
    return payload.get("data")


def _contact(value: int | str) -> int | str:
    """ID 参数：整数原样传，微信标识去掉首尾空白后传，怎么解析由 Flandora 决定。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return str(value).strip()


class _Actions:
    """放在客户端上的便捷方法；真正干活的是 `self.call`。"""

    def call(self, action: str, **params: Any) -> Any:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def send_private_msg(self, user_id: int | str, message: Any, **extra: Any) -> Any:
        return self.call(
            "send_private_msg",
            user_id=_contact(user_id),
            message=msg.normalize(message),
            **extra,
        )

    def send_group_msg(self, group_id: int | str, message: Any, **extra: Any) -> Any:
        return self.call(
            "send_group_msg", group_id=_contact(group_id), message=msg.normalize(message), **extra
        )

    def send_msg(self, message_type: str, message: Any, **target: Any) -> Any:
        return self.call(
            "send_msg", message_type=message_type, message=msg.normalize(message), **target
        )

    def get_login_info(self) -> dict[str, Any]:
        return self.call("get_login_info")

    def get_status(self) -> dict[str, Any]:
        return self.call("get_status")

    def get_friend_list(self) -> list[dict[str, Any]]:
        return self.call("get_friend_list")

    def get_group_list(self) -> list[dict[str, Any]]:
        return self.call("get_group_list")

    def get_msg(self, message_id: int) -> dict[str, Any]:
        return self.call("get_msg", message_id=int(message_id))


class FlandoraHttp(_Actions):
    """走 OneBot HTTP 服务端（`ob_networks` 里的 `http_server`）。"""

    def __init__(self, base_url: str = DEFAULT_HTTP_URL, token: str = "", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout
        self._session = requests.Session()

    def call(self, action: str, **params: Any) -> Any:
        payload = {"action": action, "params": params}
        try:
            response = self._session.post(
                self.base_url,
                json=payload,
                headers=_auth_headers(self.token),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise TransportError(f"{action}：连不上桥接（{e}）") from e
        if response.status_code == 401:
            raise TransportError(f"{action}：桥接拒绝了这个请求（HTTP 401，检查 access_token）")
        if response.status_code >= 400:
            raise TransportError(f"{action}：桥接返回 HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as e:
            raise TransportError(f"{action}：响应不是合法 JSON") from e
        return _check(action, body)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "FlandoraHttp":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class FlandoraWS(_Actions):
    """走 WebSocket：正向连桥接的 `ws_server`，或反向接框架自己的地址。"""

    def __init__(
        self,
        url: str = DEFAULT_WS_URL,
        token: str = "",
        timeout: float = 10.0,
        reconnect: bool = False,
        reconnect_interval: float = 5.0,
    ):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.reconnect = reconnect
        self.reconnect_interval = reconnect_interval
        self._handlers: dict[str, list[Handler]] = {}
        self._ws: Any = None
        self._cm: Any = None
        self._closed = False

    # ---------- 连接 ----------

    def connect(self) -> "FlandoraWS":
        # websockets 17 的 connect() 返回的是上下文管理器而不是连接本身；
        # 这里要一条长命连接（不在 with 块里活），所以显式进出它。
        try:
            self._cm = sync_connect(
                self.url,
                additional_headers=_auth_headers(self.token),
                open_timeout=self.timeout,
                max_size=16 * 1024 * 1024,
            )
            self._ws = self._cm.__enter__()
        except Exception as e:  # noqa: BLE001 - websockets 的异常族较杂，统一归一
            self._cm = None
            self._ws = None
            raise TransportError(f"连不上 {self.url}：{e}") from e
        return self

    def close(self) -> None:
        self._closed = True
        cm, self._cm = self._cm, None
        self._ws = None
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as e:  # noqa: BLE001 - 关连接失败不该盖住调用方的主流程
                raise TransportError(f"关闭 {self.url} 时出错：{e}") from e

    def __enter__(self) -> "FlandoraWS":
        return self.connect()

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _ensure(self) -> Any:
        if self._ws is None:
            self.connect()
        return self._ws

    # ---------- 事件 ----------

    def on(self, event: str, handler: Handler | None = None):
        """注册事件处理器；`event` 可以是 `message`、`message_sent`、`notice` 或 `*`。"""

        def register(fn: Handler) -> Handler:
            self._handlers.setdefault(event, []).append(fn)
            return fn

        return register if handler is None else register(handler)

    def _dispatch(self, event: dict[str, Any]) -> None:
        keys = ["*", str(event.get("post_type") or "")]
        for key in keys:
            for handler in self._handlers.get(key, []):
                handler(event)

    # ---------- 调用 ----------

    def call(self, action: str, **params: Any) -> Any:
        ws = self._ensure()
        echo = uuid.uuid4().hex
        try:
            ws.send(json.dumps({"action": action, "params": params, "echo": echo}))
        except ConnectionClosed as e:
            raise TransportError(f"{action}：连接已关闭（{e}）") from e

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError(f"{action}：{self.timeout} 秒内没有回包")
            try:
                raw = ws.recv(timeout=remaining)
            except TimeoutError as e:
                raise TransportError(f"{action}：{self.timeout} 秒内没有回包") from e
            except ConnectionClosed as e:
                raise TransportError(f"{action}：连接被关闭（{e}）") from e
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("echo") == echo:
                return _check(action, payload)
            if isinstance(payload, dict):
                self._dispatch(payload)

    def run_forever(self) -> None:
        """持续收事件，直到连接关闭。

        `reconnect=True` 时断线按 `reconnect_interval` 重连，否则直接把异常抛给调用方。
        """
        while True:
            ws = self._ensure()
            try:
                for raw in ws:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        self._dispatch(event)
            except ConnectionClosed:
                pass
            if self._closed or not self.reconnect:
                return
            time.sleep(self.reconnect_interval)
            self._ws = None


class AsyncFlandoraWS(_Actions):
    """异步版 WebSocket 客户端，适合 asyncio 框架。"""

    def __init__(self, url: str = DEFAULT_WS_URL, token: str = "", timeout: float = 10.0):
        self.url = url
        self.token = token
        self.timeout = timeout
        self._handlers: dict[str, list[Callable[[dict[str, Any]], Any]]] = {}
        self._ws: Any = None

    async def connect(self) -> "AsyncFlandoraWS":
        try:
            self._ws = await async_connect(
                self.url,
                additional_headers=_auth_headers(self.token),
                open_timeout=self.timeout,
                max_size=16 * 1024 * 1024,
            )
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"连不上 {self.url}：{e}") from e
        return self

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> "AsyncFlandoraWS":
        return await self.connect()

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    def on(self, event: str, handler: Any = None):
        def register(fn: Any) -> Any:
            self._handlers.setdefault(event, []).append(fn)
            return fn

        return register if handler is None else register(handler)

    async def _dispatch(self, event: dict[str, Any]) -> None:
        for key in ("*", str(event.get("post_type") or "")):
            for handler in self._handlers.get(key, []):
                result = handler(event)
                if hasattr(result, "__await__"):
                    await result

    async def call(self, action: str, **params: Any) -> Any:
        if self._ws is None:
            await self.connect()
        echo = uuid.uuid4().hex
        try:
            await self._ws.send(json.dumps({"action": action, "params": params, "echo": echo}))
        except ConnectionClosed as e:
            raise TransportError(f"{action}：连接已关闭（{e}）") from e

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TransportError(f"{action}：{self.timeout} 秒内没有回包")
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except TimeoutError as e:
                raise TransportError(f"{action}：{self.timeout} 秒内没有回包") from e
            except ConnectionClosed as e:
                raise TransportError(f"{action}：连接被关闭（{e}）") from e
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("echo") == echo:
                return _check(action, payload)
            if isinstance(payload, dict):
                await self._dispatch(payload)

    async def run_forever(self) -> None:
        if self._ws is None:
            await self.connect()
        async for raw in self._ws:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict):
                await self._dispatch(event)


def send_group_broadcast(
    client: _Actions, group_ids: Iterable[int], message: Any, delay: float = 0.0
) -> list[tuple[int, Any]]:
    """给多个群发同一条消息，返回每组的结果；单个群失败不影响其它群。"""
    results: list[tuple[int, Any]] = []
    for group_id in group_ids:
        try:
            results.append((int(group_id), client.send_group_msg(int(group_id), message)))
        except (ApiError, TransportError) as e:
            results.append((int(group_id), e))
        if delay:
            time.sleep(delay)
    return results
