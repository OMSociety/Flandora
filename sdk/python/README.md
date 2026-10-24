# flandora-sdk

Flandora 的 Python 客户端：把桥接当成 OneBot v11 协议端来用，省掉手写 WebSocket 与 echo 关联。

```powershell
# 在本仓库里
.venv\Scripts\python.exe -m pip install -e sdk\python
```

## 三条连接方式

| 客户端 | 走什么 | 适合 |
| --- | --- | --- |
| `FlandoraHttp` | `ob_networks` 里的 `http_server` | 一问一答的脚本、定时任务 |
| `FlandoraWS` | `ws_server`（正向）或机器人框架的地址（反向） | 要收事件的常驻程序 |
| `AsyncFlandoraWS` | 同上，asyncio 版 | asyncio 框架、并发调用 |

```python
from flandora_sdk import FlandoraWS, MessageChain

client = FlandoraWS("ws://127.0.0.1:7999", token="")
print(client.get_login_info())  # {'user_id': 123456, 'nickname': '...'}


@client.on("message")
def handle(event):
    if event["message_type"] == "group" and event.get("is_mentioned"):
        client.send_group_msg(event["group_id"], MessageChain().at(event["user_id"]).text(" 收到"))


client.run_forever()
```

HTTP 版本：

```python
from flandora_sdk import FlandoraHttp

with FlandoraHttp("http://127.0.0.1:5700/", token="") as client:
    print(client.get_group_list())
    client.send_private_msg(123456, "定时提醒：该写日报了")
```

异步版本：

```python
import asyncio
from flandora_sdk import AsyncFlandoraWS


async def main():
    async with AsyncFlandoraWS("ws://127.0.0.1:7999") as client:
        client.on("message", lambda event: print("收到", event["raw_message"]))
        print(await client.get_login_info())
        await client.run_forever()


asyncio.run(main())
```

## 错误怎么处理

```python
from flandora_sdk import ApiError, TransportError

try:
    client.send_group_msg(123456, "hi")
except ApiError as e:  # 桥接回了一段失败响应：动作没实现、参数不对、群名没映射……
    print(e.retcode, e.message)  # 1404 未实现 / 1405 未知动作 / 1406 查不到
except TransportError as e:  # 连不上、超时、连接被关
    print("稍后重试", e)
```

分界线只有一条：**服务端连得上但动作失败是 `ApiError`，连不上或断了是 `TransportError`**。

## 消息段

`text` / `at` / `at_all` / `face` / `image` / `record` / `video` / `reply` 都是返回 OneBot 段的普通函数；
`MessageChain` 把它们串起来，`str(chain)` 就是 CQ 码。反过来 `from_cq("[CQ:at,qq=42] 在吗")` 得到段数组。
转义规则与服务端一致（`&` `[` `]` `,` 四个字符），往返不丢内容。

`client.send_*` 接受三种形态：段数组、`MessageChain`、CQ 字符串。字符串里没有 `[CQ:` 就整条当文本。

## 批量发群

```python
from flandora_sdk import FlandoraHttp, send_group_broadcast

with FlandoraHttp("http://127.0.0.1:5700/") as client:
    results = send_group_broadcast(client, [111, 222, 333], "维护通知：今晚 23:00 重启", delay=1.0)
    for group_id, outcome in results:
        print(group_id, "失败" if isinstance(outcome, Exception) else "已送达")
```

单个群失败（比如群名还没映射）只会落在那一条结果里，不影响后面的群。

## 动作目录

`flandora_sdk.list_actions()` 返回服务端支持的 51 个动作（含未实现的，带 `wip` 说明原因），
`implemented_actions()` 只留已接通的。这份快照由 `tools/gen_sdk_catalog.py` 从
`flandora/action_catalog.py` 生成，`tests/test_sdk_catalog.py` 会核对它没过期。

## 已知边界

- 微信 PC 端做不到的动作（群管理、撤回、拍一拍等）服务端回 `retcode=1404`，SDK 原样抛 `ApiError`，不会假装成功。
- 收件侧只上报 Flandora 推送过的消息；`get_msg` 查不到别人的历史消息（返回 1406）。
