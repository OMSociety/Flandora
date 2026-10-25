# 客户端工具：Python SDK 与 MCP 服务

两套东西都只是「把 Flandora 当 OneBot v11 协议端来用」的封装，桥接本身不依赖它们；用不用、用什么语言写客户端都随你。

## Python SDK

`flandora-sdk` 把桥接当成 OneBot v11 协议端来用：同步 HTTP、同步 WebSocket、异步 WebSocket 三种客户端，消息段构造器与 CQ 码编解码，错误按「桥接说动作失败」和「连不上」分成两类。源码在 [`sdk/python/`](../sdk/python)：

```powershell
python -m pip install -e sdk\python
```

```python
from flandora_sdk import FlandoraWS, MessageChain

client = FlandoraWS("ws://127.0.0.1:7999", token="")


@client.on("message")
def handle(event):
    if event["message_type"] == "group" and event.get("is_mentioned"):
        client.send_group_msg(event["group_id"], MessageChain().at(event["user_id"]).text(" 收到"))


client.run_forever()
```

用法、错误分类与已知边界见 [sdk/python/README.md](../sdk/python/README.md)。

## MCP 服务

`flandora-mcp` 把动作目录与调用能力包成 MCP 服务，给支持 MCP 的 AI 客户端用：默认 `docs` 模式只读动作目录（不需要桥接在线），`read` 模式可调只读动作，`write` 模式才能发消息。源码在 [`mcp/python/`](../mcp/python)：

```powershell
python -m pip install -e mcp\python
python -m flandora_mcp.server --selftest
```

客户端配置、三种模式与工具清单见 [mcp/python/README.md](../mcp/python/README.md)。
