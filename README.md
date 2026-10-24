<p align="center">
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/assets/readme/hero.svg" width="100%" alt="Flandora 把微信个人号接成标准的 OneBot v11 服务端" />
</p>

<p align="center">
  <a href="https://github.com/OMSociety/Flandora/tags"><img src="https://img.shields.io/github/v/tag/OMSociety/Flandora?color=07C160" alt="最新版本"></a>
  <a href="https://github.com/OMSociety/Flandora"><img src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4" alt="Windows"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/OMSociety/Flandora?color=07C160" alt="License"></a>
  <a href="https://github.com/OMSociety/Flandora/stargazers"><img src="https://img.shields.io/github/stars/OMSociety/Flandora?color=07C160" alt="Stars"></a>
  <a href="https://github.com/OMSociety/Flandora/issues"><img src="https://img.shields.io/github/issues/OMSociety/Flandora?color=07C160" alt="Issues"></a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#运行链路">运行链路</a> ·
  <a href="#核心能力">核心能力</a> ·
  <a href="#控制台">控制台</a> ·
  <a href="#配置项">配置项</a> ·
  <a href="https://github.com/OMSociety/Flandora/issues">问题反馈</a>
</p>

Flandora 是面向微信 PC 客户端的 Windows 桥接程序：在微信登录的瞬间截获本机数据库口令，只读解密聊天库收取消息，并以账号本人身份在隔离桌面上投递回复，对外提供 [OneBot v11](https://github.com/botuniverse/onebot-11) 正向 WebSocket 服务端与浏览器控制台。

> [!CAUTION]
> Flandora 是独立的第三方项目，与腾讯 / 微信无隶属或授权关系。项目仅供学习与技术研究，请遵守《微信软件许可及服务协议》及适用法律；以自动化方式驱动自己的客户端存在账号被限制或封禁的风险，软件按「现状」提供，使用风险自负。详见[使用边界与许可](#使用边界与许可)。
>
> **Disclaimer:** Flandora is independent of Tencent / WeChat and is provided for study and research only, "as is" and without warranty.

## 核心能力

| 场景 | 能力 |
| --- | --- |
| 收件 | 登录瞬间取钥；解密副本只读轮询；私聊与群聊的文本、图片、表情、语音占位；群消息全量上报 |
| 发送 | 文本、图片、文件、群内 @ 提及、语音（mp3 卡片）与视频（文件形式）；默认在隔离桌面投递；发送前清空输入框并核对回显 |
| OneBot v11 | 四类网络入口（WS / HTTP 服务端、反向 WS、HTTP 上报）可同时开启；消息段与 CQ 码双向编解码；`is_mentioned` 如实标记是否被 @ |
| 群与身份 | 群内按发言人拆分 `user_id`；群名映射；多昵称识别 |
| 取钥 | 四步向导：免责声明、数据库口令、图片密钥、校验；口令与密钥由 Windows DPAPI 加密落盘 |
| 隔离桌面 | 微信跑在独立的 Windows 桌面上；重启后接管仍在运行的实例；需要时由你点button结束 |
| 控制台 | 仪表盘、消息记录、运行日志、桥接配置、微信画面、取钥向导、发送测试、调试台；明暗两套主题 |

## 运行链路

<p align="center">
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/assets/readme/flow.svg" width="100%" alt="微信客户端经 Flandora 的取钥、收件与发送链路，对外连接 OneBot v11 客户端" />
</p>

会话接入、口令还原、事件组装、动作执行与网络适配保持独立边界。收件按 `wx_poll_seconds` 轮询本机库，不向聊天进程注入钩子；发送只在你调用动作时发生。**桥接只做通道**：要不要回、怎么回、上下文带多少，全部留在客户端侧。

## 快速开始

1. 从 [Releases](https://github.com/OMSociety/Flandora/releases) 下载发行包并解压，安装包与便携包二选一：

   | 包 | 说明 |
   | --- | --- |
   | `Flandora-v1.0.0-setup.exe` | 安装到 `%LOCALAPPDATA%\Programs\Flandora`，可勾选开机自启与桌面快捷方式；卸载时单独询问是否删除配置与已存口令 |
   | `Flandora-v1.0.0.zip` | 便携包，解压到任意目录，双击 `启动.bat` |

2. 退出自己的微信，运行 `Flandora-Manager.exe`，浏览器打开 [`http://127.0.0.1:8766`](http://127.0.0.1:8766)。
3. 进入「取钥向导」，四步走完：勾选免责声明 → 开始取钥 → 在向导右侧的实况画面里完成登录 → 想让机器人看图就继续抓图片密钥 → 校验。

之后让客户端连上来即可：

```json
{
  "host": "127.0.0.1",
  "port": 7999,
  "token": ""
}
```

> [!NOTE]
> 程序没有购买代码签名证书，Windows SmartScreen 会拦一次：点「更多信息」→「仍要运行」。便携包的四个 exe 必须放在同一目录，建议放在不含中文和空格的路径。

> [!WARNING]
> `ob_server_token` 与客户端 `token` 必须一致。留空表示不校验鉴权，此时服务端只监听 `127.0.0.1`；要让别的机器连进来，改 `ob_server_host`、在防火墙放行 7999，并务必填一串随机 token。

## ID 约定

微信侧的标识是 wxid（`wxid_xxx`）与群会话 ID（`xxx@chatroom`），OneBot v11 要求整数。Flandora 在 `data/id_map.json` 里维护一张双向映射表：wxid 首次出现时分配一个正整数并落盘，之后每次启动、每次升级都是同一个数，客户端存下的 `user_id` 不会因为重启而失效。

| 字段 | 含义 |
| --- | --- |
| `user_id` | 发言人（私聊即对方）的 wxid 映射。群消息里按发言人拆分，同一个人在不同群里是同一个 `user_id` |
| `group_id` | 群会话 ID 的映射；私聊事件里为 `0` |
| `message_id` | 非零正整数。收件时按微信本地库的消息行号分配，同一条消息重复上报得到同一个号；发送成功后返回新号 |

映射表与消息号表都在配置目录下，换机时连同 `config.json` 一起带走即可保持 ID 不变。

## 动作面

动作清单的唯一真相源是 `flandora/action_catalog.py`：哪些动作支持、要什么参数、能不能真正执行都写在里面。支持的动作按能力分四类：

| 分类 | 动作 |
| --- | --- |
| 消息 | `send_msg`、`send_private_msg`、`send_group_msg`、`get_msg`、`get_group_msg_history`、`get_friend_msg_history` |
| 信息 | `get_login_info`、`get_status`、`get_version_info`、`can_send_image`、`can_send_record` |
| 好友 | `get_friend_list`、`get_stranger_info`、`get_friend_info` |
| 群 | `get_group_list`、`get_group_info`、`get_group_member_list`、`get_group_member_info` |

名册类查询读的是微信本地库：好友不含群、公众号与文件传输助手，群成员带 `owner` / `member` 角色。本地库没有的字段（性别、年龄、入群时间、群容量）如实回 `unknown` / `0`，不猜。

`get_msg` 取的是 Flandora 自己推送过的消息——每条上报的事件都在 `data/messages.db` 里留了副本，客户端可以用收到的 `message_id` 回查原文。存档不自动清理：`message_id` 是撤回、引用与取原文的长期凭据，静默回收会让老客户端的引用指向不存在的消息。

### 消息段

发送方向按段类型分派，每一段要么真的发出去，要么在响应里留下原因，没有静默跳过的分支：

| 段类型 | 发送 | 说明 |
| --- | --- | --- |
| `text` | 支持 | 逐字符投递并回读校验，回显不符会清空重打 |
| `at` | 群内支持 | 走成员选择弹层：输入 `@`、按昵称筛选、点中唯一匹配项；弹层认不出来就清空输入框并返回失败。`@全体成员` 直接拒绝（桌面端没有这个入口） |
| `image` | 支持 | `base64://` 或 `astrbot_attachments` 下的相对路径 |
| `face` | 支持 | 有 `file` 当图片发；只有 QQ 表情 id 时映射成微信文本表情（`[微笑]` 等） |
| `voice` | 降级 | 以 mp3 文件发送（对方收到文件卡片，双击可播放） |
| `video` | 降级 | 以文件形式发送，呈现形态由微信客户端按扩展名决定 |
| `reply` | 跳过 | 微信桌面端无法按 `message_id` 构造引用；只发 `reply` 时整条返回 `1404` |
| 其它 | 报错 | 未接线的段类型计入 `failed_segments`，一个段都没发出去时 `retcode=1500` |

收件方向会主动还原两种结构，客户端拿到的不是一段扁平文本：

| 段类型 | 收件 | 依据 |
| --- | --- | --- |
| `at` | 支持 | 微信把选中的提及渲染成 `@昵称` 加 U+2005 分隔符；手打的 `@昵称` 没有分隔符，照旧留在文本里 |
| `reply` | 支持 | 引用消息的 `refermsg/svrid` 就是被引消息的 `server_id`，按它回查 `message_id`；查不到时 `id` 记 `0`（引用关系仍在，取不到原文） |
| `text` | 支持 | 其余内容按原文成段，认出中间夹着的提及时在前后切开 |

做不到的能力与各自原因写在 [`docs/wip.md`](docs/wip.md)；需要在真机上确认的三项（@ 弹层、撤回、拍一拍）连同实验步骤写在 [`docs/needs-user.md`](docs/needs-user.md)。

微信 PC 客户端确实没有对应能力的动作（群管理、群文件、点赞等）返回 `failed` 并在 `message` 里写明原因，不会回一个空对象假装成功：

| `retcode` | 含义 |
| --- | --- |
| `0` | 成功 |
| `1400` | 参数不合契约，或缺少定位会话所需的信息（例如群名映射还没学到） |
| `1404` | 动作已知但暂未实现，`message` 是具体原因 |
| `1405` | 动作名不在动作目录里 |
| `1406` | 动作支持，但传入的 ID 在本地库里查不到 |
| `1500` | 执行失败（微信未登录、主窗口不可用、实现缺口） |

## 控制台

面板默认监听 `127.0.0.1:8766`，八页：仪表盘、消息记录、日志、桥接配置、微信与桌面、取钥向导、测试发送、调试台。调试台把动作目录整张列出来：选动作、按参数表填值、点执行，回来的响应原样打印；也能看最近推送的事件，或者拼好消息段直接生成 CQ 码。它调的是同一条动作链路，线上调不通的动作可以在这里先复现。面板本身可以设登录密码（配置页「打开面板时要密码」），设了之后所有接口与实时事件流都要先登录，密码只以 PBKDF2 哈希存在 `config.json` 里。
<table>
<tr>
<td width="50%" align="center"><img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/dashboard.png" alt="仪表盘：运行状态、收发计数与 24 小时消息量"></td>
<td width="50%" align="center"><img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/messages.png" alt="消息记录：按收发方向与类型筛选"></td>
</tr>
<tr>
<td align="center">仪表盘：收件源、OneBot 连接、发送通道与进程状态</td>
<td align="center">消息记录：谁说了什么、发出去没有，一眼可查</td>
</tr>
</table>

<table>
<tr>
<td width="70%" align="center"><img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/config.png" alt="桥接配置：收件与身份、OneBot 连接、消息与桥接、群名映射"></td>
<td width="30%" align="center"><img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/mobile.png" alt="窄屏下的仪表盘"></td>
</tr>
<tr>
<td align="center">桥接配置：五组表单，也能直接编辑原始 JSON</td>
<td align="center">窄屏：手机上同样能看状态</td>
</tr>
</table>

## Python SDK

`flandora-sdk` 把桥接当成 OneBot v11 协议端来用：同步 HTTP、同步 WebSocket、异步 WebSocket 三种客户端，
消息段构造器与 CQ 码编解码，错误按「桥接说动作失败」和「连不上」分成两类。源码在 `sdk/python/`：

```powershell
.venv\Scripts\python.exe -m pip install -e sdk\python
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

用法、错误分类与已知边界见 [sdk/python/README.md](sdk/python/README.md)。

## MCP 服务

`flandora-mcp` 把动作目录与调用能力包成 MCP 服务，给支持 MCP 的 AI 客户端用：默认 `docs` 模式只读动作目录
（不需要桥接在线），`read` 模式可调只读动作，`write` 模式才能发消息。源码在 `mcp/python/`：

```powershell
.venv\Scripts\python.exe -m pip install -e mcp\python
.venv\Scripts\python.exe -m flandora_mcp.server --selftest
```

客户端配置、三种模式与工具清单见 [mcp/python/README.md](mcp/python/README.md)。

## 配置项

配置文件是程序目录下的 `data/config.json`（源码模式即仓库根 `data/config.json`），首次启动自动生成。缺的键按默认值处理，改完重启 bridge 生效。

```json
{
  "bot_nicknames": ["你的微信昵称"],
  "bot_wxid": "",
  "self_name": "wechat-bot",
  "ob_server_host": "127.0.0.1",
  "ob_server_port": 7999,
  "ob_server_token": "",
  "isolated_desktop": true,
  "buffer_seconds": 5,
  "web_port": 8766
}
```

### 机器人身份

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `bot_nicknames` | 字符串数组 | 必填 | 机器人微信昵称。群里 @ 到其中任意一个，`is_mentioned` 即为 `true`；可填多个 |
| `bot_wxid` | 字符串 | 空 | 机器人自己的 wxid，留空从当前登录账号推断 |
| `self_name` | 字符串 | `wechat-bot` | 推送给客户端时显示的发送者名 |
| `group_name_map` | 对象 | `{}` | 群 ID 到群名的映射。微信搜索框搜不到 `@chatroom` 结尾的群 ID，发群消息要靠它换名 |

### 面板

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `web_port` | 整数 | `8766` | 面板端口，只监听回环地址 |
| `webui_auto_open` | 布尔 | `true` | 启动 bridge 时自动用默认浏览器打开面板 |
| `webui_password` | 字符串 | 空 | 面板登录密码的 PBKDF2 哈希，由面板写入。留空即不校验；手填明文不会生效 |

### 网络入口

一个入口是「一个方向的 OneBot 连接」。`ob_networks` 是入口数组，可以同时开多个；留空（或没有这一项）时按 `ob_server_host`、`ob_server_port`、`ob_server_token` 起一个正向 WebSocket 服务端，旧配置可以原样继续用。

| 类型 | 方向 | 谁发起连接 | 说明 |
| --- | --- | --- | --- |
| `ws_server` | 正向 WebSocket | 机器人框架连过来 | 最常用，AstrBot 等填 `ws://127.0.0.1:7999/` |
| `http_server` | 正向 HTTP | 机器人框架调过来 | 同时收 GET 查询串与 POST JSON，响应体是 OneBot 标准响应 |
| `ws_client` | 反向 WebSocket | 桥接连出去 | 适配只会起服务端的框架，断了按 `reconnect_interval_ms` 重连 |
| `http_client` | 反向 HTTP | 桥接连出去 | 事件用 POST 上报到框架，不做 API |

每个入口的字段：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `type` | 字符串 | 必填 | `ws_server`、`http_server`、`ws_client`、`http_client` 之一 |
| `name` | 字符串 | 必填 | 入口名字，同一份配置里不能重复 |
| `enabled` | 布尔 | `true` | 关掉后这个入口不启动 |
| `role` | 字符串 | `universal` | `universal` 收动作也推事件；`api` 只收动作；`event` 只推事件 |
| `host` / `port` | 字符串 / 整数 | `127.0.0.1` / 见下 | 仅服务端类入口 |
| `path` | 字符串 | `/` | 仅服务端类入口，握手路径 |
| `url` | 字符串 | 必填 | 仅客户端类入口，`ws://` 或 `http://` |
| `access_token` | 字符串 | 空 | Bearer 头或 `?access_token=` 都认，留空不校验 |
| `message_format` | 字符串 | 空 | 该入口的事件形态，留空跟随全局 `ob_message_format` |
| `timeout_ms` | 整数 | `5000` | 仅 `http_client`，上报超时 |
| `reconnect_interval_ms` | 整数 | `5000` | 仅 `ws_client`，重连间隔 |

```json
{
  "ob_networks": [
    {"type": "ws_server", "name": "default", "host": "127.0.0.1", "port": 7999, "path": "/", "role": "universal"},
    {"type": "ws_client", "name": "astrbot", "url": "ws://127.0.0.1:6199/ws", "role": "universal", "access_token": "换成一串随机值"}
  ],
  "ob_message_format": "array"
}
```

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `ob_server_host` | 字符串 | `127.0.0.1` | 没有 `ob_networks` 时的监听地址；都在本机就保持回环 |
| `ob_server_port` | 整数 | `7999` | 没有 `ob_networks` 时的正向 WebSocket 端口 |
| `ob_server_token` | 字符串 | 空 | 没有 `ob_networks` 时的握手 token，留空不校验 |
| `ob_message_format` | 字符串 | `array` | 事件里 `message` 的形态：`array` 为消息段数组，`string` 为 CQ 码字符串。`raw_message` 始终是 CQ 码字符串，发送方向两种都收 |
| `astrbot_attachments` | 字符串 | 空 | 客户端附件目录的绝对路径。填了之后图片与文件的本地路径按该目录（及其 `wechat_images` 子目录）拼出来 |

### 收件

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `wechat_files_root` | 字符串 | 空 | 微信数据根目录，留空自动取「文档」下的 `xwechat_files` |
| `wx_key_file` | 字符串 | 空 | 数据库口令文件，留空为配置目录下的 `wx_key.dat` |
| `wx_work_dir` | 字符串 | 空 | 解密副本与游标目录，留空为配置目录下的 `wx_cache` |
| `wx_poll_seconds` | 浮点 | `1.0` | 轮询本地库的间隔（秒） |
| `wx_backfill_seconds` | 整数 | `0` | 首次启动额外收进多少秒内的历史消息；`0` 表示只收启动之后的新消息 |
| `buffer_seconds` | 整数 | `5` | 同一联系人连续消息合并成一次上下文的窗口 |
| `wxkey_manual_rva` | 整数 | `0` | 手动指定取口令的函数地址（如 `0x6148a0`），仅当微信改版导致自动定位失手时用 |

### 发送与隔离桌面

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `isolated_desktop` | 布尔 | `true` | 让微信跑在独立桌面上，发送时的前台切换只发生在那个桌面；前提是微信由本程序启动 |
| `isolated_desktop_name` | 字符串 | `WeBotSandbox` | 隔离桌面名，同时跑多份实例时才需要改 |
| `wechat_exe` | 字符串 | 空 | 微信主程序路径，留空自动探测（含注册表登记的安装路径） |
| `wechat_hwnd` | 整数 | `0` | 指定要驱动的窗口句柄，`0` 取第一个；一台机器登录多个微信时才手填 |
| `allow_foreground_steal` | 布尔 | `false` | 仅在 `isolated_desktop` 为 `false` 时有意义。默认 `false` 时发送整体不可用；设为 `true` 后可用，代价是每条消息发出时前台短暂切到微信（实测切回耗时 3–11 毫秒） |

### 图片

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `wx_image_key_file` | 字符串 | 空 | 图片密钥文件，留空为配置目录下的 `wx_image_key.json` |

图片密钥与数据库口令不是一回事：AES 密钥只在微信解码图片的那几秒驻留内存，所以要趁微信正在打开一张没看过的图时去抓，取法见快速开始第 3 步。密钥是账号级的，换号后要重新取；缺它时图片消息退回 `[图片]` 占位，文字通道不受影响。

### 数据落点

| 路径 | 内容 |
| --- | --- |
| `data/config.json` | 全部配置（含 token 与 API key，别提交进仓库） |
| `data/wx_key.dat` | 数据库口令（DPAPI 加密） |
| `data/wx_image_key.json` | 图片密钥 |
| `data/id_map.json` | wxid / 群 ID 与整数 ID 的映射表，删了 ID 会重新分配 |
| `data/msg_ids.db` | 消息号表（`message_id` 与微信库行号的对应） |
| `data/messages.db` | 已推送事件的存档，供 `get_msg` 与历史检索 |
| `data/wx_cache/` | 解密副本与游标，可随时删 |
| `data/flandora.log` | 运行日志（UTF-8） |

## 已知限制

- **语音、文件、视频拿不到本体**：这几种在磁盘上同样是加密的 `.dat` 封装，目前只还原了图片那一种；收到语音按 `[语音]` 占位文本上报，客户端据此知道有人发过语音，拿不到音频本体。发送方向不受影响。
- **取钥需要一次重新登录**：口令只在登录瞬间存在，等不来，这是微信的设计。
- **图片密钥需要一张没被缓存过的图**：密钥只为正在解码的图片短暂驻留内存。
- **隔离桌面上登录仍需手机确认**：扫码或手机端确认绕不过去。
- **发送必然触发微信自身的窗口消息处理**：已逐项实测否证十几种「不惊动它」的投递方式（PostMessage、SendMessageTimeout、UIA SetValue、SetFocus、Invoke、SetActiveWindow、完全附着线程等），任何向微信投递的输入都会让它升到前台，这是微信自身行为；隔离桌面是绕开它的办法，不是修好了它。
- **一个微信号只能被一份实例驱动**：要跑多个号，用 `isolated_desktop_name` 给每份实例配不同桌面名，并用不同的 `ob_server_port`。
- **视频以文件形式发送**：视频段走文件通道发出，对方一定能收到；呈现成文件卡片还是视频气泡取决于微信客户端。
- **@ 提及尚未在真机上验证**：成员弹层的定位方式已在代码里实现并加了失败保护，但弹层是不是独立顶层窗口，需要在真机上看，实验步骤见 [`docs/needs-user.md`](docs/needs-user.md)。
- **撤回与拍一拍未接通**：两者都要求精确点中某条消息气泡或某个头像，UIA 目前拿不到稳定的定位方式，动作如实返回 `1404`。
- **未在全新安装的 Windows 上验证过**：开发与测试都在一台已经装了很多东西的机器上完成。

实验过程与数据见 [`docs/known-limits-dat-format.md`](docs/known-limits-dat-format.md) 与 [`docs/known-limits-desktop.md`](docs/known-limits-desktop.md)。

## 开发

需要 Python 3.13 与 Node.js（构建前端时）。以下命令在仓库根执行：

```powershell
# 装依赖（运行期 + 测试 + ruff，一份清单装全）
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 语法检查、lint、后端测试
.venv\Scripts\python.exe -m compileall -q flandora manager
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m pytest tests -q

# SDK（可选：在自己的项目里用它当客户端）
.venv\Scripts\python.exe -m pip install -e sdk\python

# MCP 服务（可选：给 AI 客户端用）
.venv\Scripts\python.exe -m pip install -e mcp\python

# 从源码起桥接
.venv\Scripts\python.exe run_bridge.py
```

这些命令与 CI（`.github/workflows/ci.yml`）同源，提交前本地跑绿即可。CI 另外会重新生成一次 `sdk/python/flandora_sdk/_catalog.json` 并核对无差异（动作目录改了没同步快照会红）。

前端在 `webui-frontend/`（Vue 3 + Vite）：

```powershell
cd webui-frontend
npm install
npm test        # Playwright，自动在 4175 起 vite
npm run build   # 产物写入 flandora\webui\dist
```

打包发行版：

```powershell
.venv\Scripts\python.exe build.py
```

产物在 `dist\`：`Flandora-v1.0.0.zip` 与 `Flandora-v1.0.0-setup.exe`。安装程序由 [Inno Setup](https://jrsoftware.org/isdl.php) 编译，没装就只出便携包（可用环境变量 `ISCC` 指向 `ISCC.exe` 的完整路径）。

> [!TIP]
> 打包前会检查 `flandora/webui/dist/index.html`：不存在才调用 npm 构建，已存在就原样打包。改过 `webui-frontend/` 就先自己 `npm run build`，否则旧界面会被打进安装包。

取钥器单独打包，因为 frida 有几十 MB，不塞进主程序。工程约定、目录边界与验收口径见 [`AGENTS.md`](AGENTS.md)。

## 使用边界与许可

> [!IMPORTANT]
> 本项目以 [MIT](LICENSE) 发布，可自由使用、修改与再分发，保留版权与许可声明即可。禁止用于任何违法违规用途，包括但不限于批量加好友、自动营销、骚扰与诈骗。

本工具通过自动化方式驱动你自己登录的微信客户端收发消息，这类用法可能与微信的用户协议冲突。请只用自己的号，并自行承担账号被限制或封禁的风险。项目为独立工具，与腾讯公司无任何关联，不承诺可用性与送达率；微信改版导致接口失效属官方行为，不在担保范围内。使用产生的一切后果由使用者自行承担。

发布包内 `docs/third-party-licenses/` 收录了随包分发组件的许可证原文。

## 鸣谢与致谢

- [WeChat-Onebot-Bridge](https://github.com/jessiongod/wechat-onebot-bridge)：本仓库的前身与上游，收件、发送与 OneBot 协议层的早期设计来自这个项目
- [WeFlow](https://github.com/hicccc77/WeFlow)：上游的收件链路基于它的本地微信数据接口；本仓库改为只读解密本地库，不再依赖它运行
- [OneBot v11](https://github.com/botuniverse/onebot-11)：对外提供的事件与动作遵循这份协议规范

<p align="center">
  <a href="https://github.com/OMSociety/Flandora/graphs/contributors"><img src="https://contrib.rocks/image?repo=OMSociety/Flandora" alt="Flandora 贡献者" /></a>
</p>
