"""SDK 的错误类型。

调用方只需要记住一条分界线：**桥接说「这个动作失败了」是 `ApiError`（服务端连得上、
业务拒绝），连不上或连接断了是 `TransportError`**。前者通常重试无用、要改参数或换动作，
后者重试可能有用。
"""

from __future__ import annotations


class FlandoraError(Exception):
    """SDK 所有错误的基类。"""


class TransportError(FlandoraError):
    """连不上桥接、请求超时、连接被关闭。"""


class ApiError(FlandoraError):
    """桥接回了一个失败的响应（status=failed 或 HTTP 非 2xx）。"""

    def __init__(self, action: str, retcode: int, message: str, response: dict | None = None):
        self.action = action
        self.retcode = int(retcode or 0)
        self.message = message
        self.response = response or {}
        super().__init__(f"{action} 失败（retcode={self.retcode}）：{message}")


class ActionNotFound(LookupError):
    """动作目录里没有这个名字（写错了，或 SDK 快照太旧）。"""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"未知动作：{name}")
