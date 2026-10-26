# 接入 AstrBot

AstrBot 的 OneBot v11 适配器（`aiocqhttp`）以「反向 WebSocket 服务端」的身份工作：它自己监听端口，协议端主动连过去。因此两边是这样配的。

## 1. 连起来

AstrBot 侧：「平台」里添加 `aiocqhttp(OneBot v11)` 适配器。

| 项 | 填什么 |
| --- | --- |
| 反向 WebSocket 主机地址 | `0.0.0.0`（只在本机对连时 `127.0.0.1` 也行） |
| 反向 WebSocket 端口 | 默认 `6199` |
| 反向 WebSocket Token | 要与 Flandora 入口的 `access_token` 一致；不校验就两边都留空 |

Flandora 侧：面板「节点配置」新增一条入口。

| 字段 | 值 |
| --- | --- |
| `type` | `ws_client` |
| `url` | `ws://127.0.0.1:6199/ws` |
| `role` | `universal` |
| `access_token` | 与 AstrBot 侧一致 |

`/ws` 是 AstrBot 反向 WebSocket 的固定路径（它文档里给的地址就是 `ws(s)://<host>:6199/ws`）。掉线后按 `reconnect_interval_ms`（默认 5 秒）重连。

> **提示**：AstrBot 日志里出现「aiocqhttp(OneBot v11) 适配器已连接。」就是握手成功；Flandora 侧的「运行日志」里能看到这条入口的连接记录。

## 2. 事件格式

Flandora 默认 `ob_message_format` 是 `array`，而 AstrBot 收到事件后是按消息段数组解析的（`convert_message` 里用 `groupby(event.message, key=...)` 按段分组）。**不要把它改成 `string`。**

事件里的 `self_id` / `user_id` / `group_id` / `message_id` 都是 Flandora 分配的 32 位正整数：

| 字段 | 内容 |
| --- | --- |
| `self_id` | 当前登录账号的整数 ID，与 `get_login_info` 同源 |
| `user_id` | 发送者的整数 ID |
| `group_id` | 群消息里是该群的整数 ID；只在群消息事件里出现 |
| `message_id` | 非零正整数，撤回、引用、取原文都用它 |

群消息事件另有 `group_name`（微信里的群名）与 `is_mentioned`（这条消息是否真的 @ 了机器人）。

## 3. 会话 ID 与 wxid

AstrBot 用 `unified_msg_origin` 记会话，写法是 `平台实例 ID:消息类型:会话 ID`（`MessageSession.__str__`，消息类型取 `GroupMessage` 或 `FriendMessage`）。会话 ID 那一段：群聊取事件的 `group_id`、私聊取发送者的 `user_id`（见 `aiocqhttp_message_event.py` 的 `send()`：`is_group = bool(self.get_group_id())`，`session_id = self.get_group_id() if is_group else self.get_sender_id()`）。

主动发送（定时任务、`send_by_session`、插件里没有 `event` 的发送）要求这段会话 ID 是纯数字：

```python
session_id_int = int(session_id) if session_id and session_id.isdigit() else None
```

拿不到整数时它退回用事件兜底；连事件都没有就抛 `ValueError: 无法发送消息：缺少有效的数字 session_id(...) 或 event(...)`。所以：

- **把事件里的 `group_id` / `user_id` 原样存下来当会话 ID**，wxid 不要塞进会话 ID；
- 需要人可读的原始标识时，用下面两条通道去取。

| 需要 | 从哪来 |
| --- | --- |
| 这条消息是谁发的（wxid） | 消息事件里的 `sender.wxid` |
| 是哪个群（`xxxxx@chatroom`） | 群消息事件里的 `group_wxid` |
| 标识换成整数 | 动作 `get_contact_id`（首次见到的标识会折进映射表并落盘） |
| 整数换回标识 | 动作 `get_contact_handle` |
| 整张映射表 | 动作 `get_contact_ids` |

反方向也通：凡是 `user_id` / `group_id` 一类参数，整数与微信标识两种写法都收。

```python
client.call("send_group_msg", group_id=1234567890, message=chain)  # 整数 ID
client.call("send_group_msg", group_id="50071592949@chatroom", message=chain)  # 群标识
client.call("send_private_msg", user_id="wxid_zhangsan", message=chain)  # wxid
```

写整数时必须是映射表里已经分配过的号：从没见过的数字回 `1406`，Flandora 不会拿这个数字去微信里搜人——那会把 ID 当成搜索词发给真实联系人。

## 4. 消息段

AstrBot 把消息链转成 OneBot 段名再发下来，它的语音段是 `record`（`Record.type = ComponentType.Record`，对应 OneBot v11 的标准段名）。Flandora 认这些段：

| 段 | Flandora | 说明 |
| --- | --- | --- |
| `text` | 支持 | 逐字符投递并回读校验 |
| `at` | 群内支持 | `qq` 填成员 ID；`@全体成员` 拒绝（桌面端没有这个入口） |
| `image` | 支持 | `base64://` 或 `client_attachments` 下的相对路径 |
| `face` | 支持 | 有图当图片发；只有 QQ 表情 id 时映射成微信文本表情 |
| `record` | 降级 | 以文件卡片发出：`base64://` 落成 `.mp3` 临时文件，本地路径按原样发，不改扩展名、不做转码 |
| `video` | 降级 | 以文件形式发出，呈现形态由微信按扩展名决定 |
| `file` | 支持 | AstrBot 的 `File` 给的是 `file:///D:/…`，直接投递成文件卡片；也收 `base64://`（`name` 当卡片上的文件名）、本机路径，以及光给文件名（到 `client_attachments` 里找） |
| `reply` | 跳过 | 桌面端无法按 `message_id` 造引用；只发 `reply` 时整条返回 `1404` |
| `node` / `nodes` | 不接受 | 合并转发在 AstrBot 侧走 `send_group_forward_msg`，Flandora 回 `1404` |

`file` 段只投递本机上能拿到的字节：`http(s)` URL 不下载（那是替客户端访问网络），会记进 `failed_segments` 并在 `message` 里写明。

### 引用机器人自己发的消息

被引的是机器人自己发出的那条时，Flandora 会在消息段最前面补一个 `at` 自己的段（`qq` 与事件的 `self_id` 相同），后面照旧跟一个 `reply` 段。

补这一段的原因在 AstrBot 的唤醒检查里（`astrbot/core/pipeline/waking_check/stage.py`）：它把「被 @ 了」「被 @全体成员」「引用了机器人的消息」并列成三条唤醒条件，前两条不依赖回查被引消息，第三条要求 `Reply.sender_id` 等于 `self_id`——而 `sender_id` 来自按 `reply` 段 `message_id` 调 `get_msg` 的结果。机器人自己发出的消息现在也会进事件存档（`message_sent`），被引消息的号取得到，这条唤醒条件本身就成立。补上的 `at` 段是第二道保险：客户端版本旧、或存档被清过时，仍能按「被 @」认出这条消息冲着机器人来。机器人启动时会按会话尾部补报一遍已经发出去的消息的服务端号，所以重启之前发出的消息同样绑得上（按发出时刻就近认领，隔得太远的不认）。

正文不受影响：AstrBot 组装 `message_str` 时跳过开头的 at 段（`aiocqhttp_platform_adapter.py`：第一个 at 的是自己时，不写进 `message_str`）。

> **提示**：这个 `at` 段是 Flandora 为唤醒补的，不是发消息的人真的 @ 了机器人。同一条消息本来就已经 @ 了机器人时不会再补第二个。

## 5. 做不到的部分

这些动作 AstrBot 会调，微信 PC 端没有入口，一律 `status=failed` + `retcode=1404`，`message` 是具体原因：

- 群管理：踢人、禁言、改群名、设置群名片
- 好友管理：删好友、改备注
- 消息操作：撤回、拍一拍（`send_poke` / `friend_poke`）
- 其它：精华消息、`set_msg_emoji_like`、`ocr_image`、`translate_en2zh`、`@全体成员`、合并转发、`send_like` / `set_online_status` / `get_online_clients`

完整清单与逐条原因见 [wip.md](wip.md)。AstrBot 的「戳一戳」类插件、群管理指令因此会在 AstrBot 日志里报动作失败，不是连接问题。

## 6. 排查

| 现象 | 先看哪里 |
| --- | --- |
| AstrBot 日志一直没有「适配器已连接」 | Flandora 入口的 `url`、`access_token`；AstrBot 端口是否在监听 |
| 收到消息正常，主动发送抛 `ValueError: 缺少有效的数字 session_id` | 会话 ID 不是 Flandora 发的整数（比如用了 wxid），改回事件里的 `group_id` / `user_id` |
| 某个动作回 `1404` | 微信 PC 端没这个能力，看 `message` 里的原因，清单见 [wip.md](wip.md) |
| 语音收到了文件卡片 | 预期行为：桌面端没有发语音的入口 |
| 想先手工验一条动作 | 面板「调试面板 → 动作调试」，走的是与 AstrBot 相同的动作链路 |
