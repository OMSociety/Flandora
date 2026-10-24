"""把动作调用打到桥接的 HTTP 服务端上。

只用标准库：MCP 服务常常跑在别的机器或别的虚拟环境里，装 requests 反而是负担。
"""

import json
import urllib.error
import urllib.request
from typing import Any


class BridgeError(Exception):
    """桥接回了失败响应：动作没实现、参数不对、群名没映射……"""

    def __init__(self, message: str, retcode: int = 0, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.retcode = retcode
        self.payload = payload or {}


class TransportError(Exception):
    """连不上桥接：地址错、超时、对端挂了。"""


def call(
    endpoint: str,
    token: str,
    action: str,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    """POST 一个动作，返回 OneBot 响应里的 data。"""
    if not endpoint:
        raise TransportError("没有配置 FLANDORA_MCP_ENDPOINT，无法调用桥接")
    body = json.dumps(
        {"action": action, "params": params or {}, "echo": "mcp"}, ensure_ascii=False
    ).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 地址来自用户配置
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 401 / 400 这类也带着 OneBot 形状的 JSON，能读就读，读不出就报状态码。
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - 读错误体失败不该盖住原始状态码
            raise TransportError(f"桥接返回 HTTP {e.code}") from e
        if e.code == 401:
            raise TransportError("桥接拒绝了令牌（401），检查 FLANDORA_MCP_TOKEN") from e
        payload = _parse(raw)
        raise BridgeError(
            str(payload.get("message") or f"HTTP {e.code}"), _retcode(payload), payload
        )
    except urllib.error.URLError as e:
        raise TransportError(f"连不上 {endpoint}：{e.reason}") from e
    except TimeoutError as e:
        raise TransportError(f"{endpoint} 在 {timeout}s 内没有响应") from e

    payload = _parse(raw)
    if payload.get("status") != "ok":
        raise BridgeError(
            str(payload.get("message") or "动作失败"),
            _retcode(payload),
            payload,
        )
    return payload.get("data")


def _parse(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise TransportError(f"桥接返回的不是 JSON：{raw[:200]!r}") from e
    if not isinstance(parsed, dict):
        raise TransportError(f"桥接返回的 JSON 不是对象：{raw[:200]!r}")
    return parsed


def _retcode(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("retcode") or 0)
    except (TypeError, ValueError):
        return 0
