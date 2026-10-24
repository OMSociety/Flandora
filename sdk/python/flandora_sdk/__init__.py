"""Flandora SDK：把桥接当成 OneBot v11 协议端来用。

```python
from flandora_sdk import FlandoraWS, MessageChain

client = FlandoraWS("ws://127.0.0.1:7999")
print(client.get_login_info())

@client.on("message")
def handle(event):
    if event["message_type"] == "group" and event.get("is_mentioned"):
        client.send_group_msg(event["group_id"], MessageChain().at(event["user_id"]).text(" 收到"))

client.run_forever()
```

三种客户端：`FlandoraHttp`（HTTP 服务端）、`FlandoraWS`（同步 WebSocket）、
`AsyncFlandoraWS`（异步 WebSocket）。错误分两类：桥接说动作失败是 `ApiError`，
连不上或超时是 `TransportError`。
"""

from .actions import categories, get_action, implemented_actions, list_actions
from .client import (
    AsyncFlandoraWS,
    FlandoraHttp,
    FlandoraWS,
    send_group_broadcast,
)
from .errors import ActionNotFound, ApiError, FlandoraError, TransportError
from .message import (
    MessageChain,
    at,
    at_all,
    escape,
    face,
    from_cq,
    image,
    normalize,
    record,
    reply,
    text,
    to_cq,
    unescape,
    video,
)

__version__ = "1.0.0"

__all__ = [
    "ActionNotFound",
    "ApiError",
    "AsyncFlandoraWS",
    "FlandoraError",
    "FlandoraHttp",
    "FlandoraWS",
    "MessageChain",
    "TransportError",
    "__version__",
    "at",
    "at_all",
    "categories",
    "escape",
    "face",
    "from_cq",
    "get_action",
    "image",
    "implemented_actions",
    "list_actions",
    "normalize",
    "record",
    "reply",
    "send_group_broadcast",
    "text",
    "to_cq",
    "unescape",
    "video",
]
