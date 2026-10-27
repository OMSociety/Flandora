# 协议面：ID、动作与事件

对外的身份是一套 [OneBot v11](https://github.com/botuniverse/onebot-11) 服务端：事件按「事件面」上报，动作按「动作面」执行。这一页写清微信的字符串标识怎么变成 OneBot 的整数、哪些动作真的支持、消息段收发的实际形态。

## 运行链路

<p align="center">
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/assets/readme/flow.svg" width="100%" alt="微信客户端经 Flandora 的取钥、收件与发送链路，对外连接 OneBot v11 客户端" />
</p>

会话接入、口令还原、事件组装、动作执行与网络适配保持独立边界。收件按 `wx_poll_seconds` 轮询本机库，不向聊天进程注入钩子；发送只在你调用动作时发生。**桥接只做通道**：要不要回、怎么回、上下文带多少，全部留在客户端侧。

## ID 约定

微信侧的标识是 wxid（`wxid_xxx`）与群会话 ID（`xxx@chatroom`），OneBot v11 要求整数。Flandora 在 `data/id_map.json` 里维护一张双向映射表：wxid 首次出现时分配一个正整数并落盘，之后每次启动、每次升级都是同一个数，客户端存下的 `user_id` 不会因为重启而失效。

| 字段 | 含义 |
| --- | --- |
| `user_id` | 发言人（私聊即对方）的 wxid 映射。群消息里按发言人拆分，同一个人在不同群里是同一个 `user_id` |
| `group_id` | 群会话 ID 的映射；私聊事件不带这个字段 |
| `message_id` | 非零正整数。收件时按微信本地库的消息行号分配，同一条消息重复上报得到同一个号；发送成功后返回新号 |

发动作时 `user_id` / `group_id` 两种写法都收：整数 ID，或直接写微信标识（`wxid_xxx`、`xxx@chatroom`）。写标识时不必事先换 ID，Flandora 自己折进映射表；写整数时必须是映射表里已分配过的号，从没见过的数字一律回 `1406`，不会拿这个数字去微信里搜人。

事件与名册回的是整数 ID。要原始标识有两个入口：消息事件里带 `group_wxid`（群会话 ID）与 `sender.wxid`（发言人 wxid）；也可以用 `get_contact_id`（标识 → 整数）、`get_contact_handle`（整数 → 标识）、`get_contact_ids`（整表导出）三个动作互查。

映射表与消息号表都在配置目录下，换机时连同 `config.json` 一起带走即可保持 ID 不变。

## 动作面

动作清单的唯一真相源是 [`flandora/action_catalog.py`](../flandora/action_catalog.py)：哪些动作支持、要什么参数、能不能真正执行都写在里面。支持的动作按用途归并如下（只作阅读分组；`category` 字段的取值是 `信息`、`消息`、`好友`、`群信息`、`群管理`、`请求`、`扩展`，MCP 的 `list_actions(category=...)` 按它精确匹配）：

| 分类 | 动作 |
| --- | --- |
| 消息 | `send_msg`、`send_private_msg`、`send_group_msg`、`get_msg`、`get_group_msg_history`、`get_friend_msg_history` |
| 信息 | `get_login_info`、`get_status`、`get_version_info`、`can_send_image`、`can_send_record`、`get_contact_id`、`get_contact_handle`、`get_contact_ids` |
| 好友 | `get_friend_list`、`get_stranger_info`、`get_friend_info` |
| 群 | `get_group_list`、`get_group_info`、`get_group_member_list`、`get_group_member_info` |

`user_id` / `group_id` 一类参数在目录里的类型写作 `id`：整数或微信标识都收（见上面的「ID 约定」）。

名册类查询读的是微信本地库：好友列表只列好友（群聊、公众号、文件传输助手各有自己的动作），群成员带 `owner` / `member` 角色。本地库没有的字段（性别、年龄、入群时间、群容量）如实回 `unknown` / `0`，不猜。

`get_msg` 取的是 Flandora 存档过的消息——每条上报的事件、以及机器人自己发出去的消息，都在 `data/messages.db` 里留了副本，客户端可以用收到的 `message_id` 回查原文。存档不自动清理：`message_id` 是撤回、引用与取原文的长期凭据，静默回收会让老客户端的引用指向不存在的消息。

微信 PC 客户端确实没有对应能力的动作（群管理、群文件、点赞等）返回 `failed` 并在 `message` 里写明原因，不会回一个空对象假装成功：

| `retcode` | 含义 |
| --- | --- |
| `0` | 成功 |
| `1400` | 参数不合契约，或缺少定位会话所需的信息（例如群名映射还没学到） |
| `1401` | 入口配置了 access_token，请求未带上或带错 |
| `1404` | 动作已知但暂未实现，`message` 是具体原因 |
| `1405` | 动作名不在动作目录里 |
| `1406` | 动作支持，但传入的 ID 在本地库里查不到；HTTP 入口路径不匹配同样是 `1406` |
| `1500` | 执行失败（微信未登录、主窗口不可用、实现缺口） |

## 消息段

发送方向按段类型分派，每一段要么真的发出去，要么在响应里留下原因，没有静默跳过的分支：

| 段类型 | 发送 | 说明 |
| --- | --- | --- |
| `text` | 支持 | 逐字符投递并回读校验，回显不符会清空重打；正文里带 emoji 这类非 BMP 字符时改走剪贴板粘贴 |
| `at` | 群内支持 | 走成员选择弹层：输入 `@`、按昵称筛选、点中唯一匹配项；弹层认不出来就清空输入框并返回失败。`@全体成员` 直接拒绝（桌面端没有这个入口） |
| `image` | 支持 | `base64://`，或只给文件名由桥接到 `client_attachments`（含其 `wechat_images` 子目录）里找；带路径分隔符的值先收窄成文件名，绝对路径不接受 |
| `face` | 支持 | 有 `file` 当图片发；只有 QQ 表情 id 时映射成微信文本表情（`[微笑]` 等） |
| `record` | 降级 | 以 mp3 文件发送（对方收到文件卡片，双击可播放） |
| `video` | 降级 | 以文件形式发送，呈现形态由微信客户端按扩展名决定 |
| `file` | 支持 | 本机路径、`file://` URI 或 `base64://` 内联（`name` 决定卡片上的文件名）；光给文件名时到 `client_attachments` 里找。`http(s)` URL 不下载，如实回失败 |
| `reply` | 跳过 | 微信桌面端无法按 `message_id` 构造引用；只发 `reply` 时整条返回 `1404` |
| 其它 | 报错 | 未接线的段类型计入 `failed_segments`，一个段都没发出去时 `retcode=1500` |

收件方向会主动还原两种结构，客户端拿到的不是一段扁平文本：

| 段类型 | 收件 | 依据 |
| --- | --- | --- |
| `at` | 支持 | 微信把选中的提及渲染成 `@昵称` 加 U+2005 分隔符；手打的 `@昵称` 没有分隔符，照旧留在文本里 |
| `reply` | 支持 | 引用消息的 `refermsg/svrid` 就是被引消息的 `server_id`，按它回查 `message_id`；查不到时 `id` 记 `0`（引用关系仍在，取不到原文） |
| `text` | 支持 | 其余内容按原文成段，认出中间夹着的提及时在前后切开 |

被引的是机器人自己发出的消息时，事件带 `reply_to_self: true`，消息段照旧只有 `reply` 与正文：发出去的消息也会进存档（`post_type` 为 `message_sent`），被引消息的 `server_id` 与 `message_id` 绑得上，`reply` 段的 `id` 取得到原文。正文不改写——发消息的人没打的 @ 不会出现在消息里，要不要当成「在跟我说话」由客户端按这个字段决定。接 AstrBot 时的来龙去脉见 [astrbot.md](astrbot.md)。

做不到的能力与各自原因写在 [wip.md](wip.md)；需要在真机上确认的两项（撤回、拍一拍）连同实验步骤写在 [needs-user.md](needs-user.md)；接 AstrBot 的步骤与能力缺口清单见 [astrbot.md](astrbot.md)。

## 事件面

除消息事件外，桥接还推两类旁路事件。它们不占消息流，客户端按 `post_type` 分派即可：

| 事件 | 触发 | 字段 |
| --- | --- | --- |
| `meta_event` / `heartbeat` | 每隔 `ob_heartbeat_seconds` 秒一次，取 0 关闭 | `interval`、`status.online`（桥接在跑且微信已接入）、`status.good`（发送通道也可用） |
| `meta_event` / `lifecycle` | 每条连接建立时推 `connect`，桥接停止时推 `disable` | `sub_type`；`connect` 是逐连接发的，后连上的客户端同样收得到 |
| `notice` / `group_recall`、`friend_recall` | 微信消息库里出现撤回系统消息 | `user_id`（撤回者）、`operator_id`、`message_id`、`message_id_known` |
| `notice` / `notify` + `sub_type: poke` | 群里出现拍一拍 | `user_id`（发起者）、`target_id`（被拍的人）、`suffix` 与 `raw_info`（自定义文案，OneBot v11 的段列表）、有群时带 `group_id` |

撤回与拍一拍**只走 notice，不作为消息事件推送**：这两类内容在微信里是系统提示行，没有可回复的正文，混在消息流里会让客户端对着一行「XX 撤回了一条消息」生成回复。

图片与表情包都走 `image` 段：表情包多一个 `sub_type: 1`（go-cqhttp 的口径，`0` 是普通图片），认这个字段的客户端会把表情包与普通图片分开显示；不认的客户端当普通图片渲染，也能看到图。

`message_id_known` 是给客户端的自证字段：微信 PC 端的撤回系统消息里没有被撤回消息的 id（本地库样本里 `newmsgid` 为空），所以 `message_id` 只能填 `0`。客户端看到 `message_id_known: false` 就该只更新界面，不要拿 `0` 去 `get_msg` 或 `delete_msg`。
