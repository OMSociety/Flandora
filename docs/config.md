# 配置项

配置文件是程序目录下的 `data/config.json`（源码模式即仓库根 `data/config.json`），首次启动自动生成。缺的键按默认值处理，改完重启 bridge 生效。

字段清单与逐项说明的机器可读版本在 `flandora/config.example.json`，由 `flandora/config/fields.py` 生成，与面板表单同源；`python tools/gen_config_example.py --check` 挡住它和字段表之间的漂移。

```json
{
  "ob_server_host": "127.0.0.1",
  "ob_server_port": 7999,
  "ob_server_token": "",
  "isolated_desktop": true,
  "web_port": 4950,
  "web_bind_host": "127.0.0.1"
}
```

## 机器人身份

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `group_name_map` | 对象 | `{}` | 群 ID 到群名的覆盖映射。群名默认从微信本地库与收到的群消息解析，一般不用填；面板不提供这一项，需要时在「系统设置 → 高级」直接编辑 JSON 添加 |

机器人自己的 wxid、显示名与 @ 识别用的昵称都**不需要配置**：启动时按当前登录的微信账号自动识别——账号 wxid 决定 OneBot 的 `user_id`，昵称取自本地通讯录里自己那一行（`get_login_info` 的昵称同源），群里「@你的微信昵称」据此标记 `is_mentioned`；切换登录账号后自动跟随，昵称改了也不用改配置。

> **提示**：@ 识别按你的微信昵称匹配。别人在群里 @ 你时，微信写进正文的是你在那个群的群名片；设了群名片而它又不是你的微信昵称时，这一条 @ 认不出来。此时把该群的群名片清掉即可（@ 会退回你的微信昵称）。

## 面板

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `web_port` | 整数 | `4950` | 面板端口 |
| `web_bind_host` | 字符串 | `127.0.0.1` | 面板绑定地址，只接受 `127.0.0.1`（仅本机）或 `0.0.0.0`（局域网可达）。填别的值会按 `127.0.0.1` 处理并在日志里说明 |
| `webui_tls` | 布尔 | `false` | 用 HTTPS 提供面板，需先装好证书与私钥 |
| `webui_tls_cert_file` | 字符串 | 空 | 证书 PEM 路径，留空用数据目录下的 `webui_cert.pem` |
| `webui_tls_key_file` | 字符串 | 空 | 私钥 PEM 路径，留空用数据目录下的 `webui_key.pem`（写入时权限收到 `0600`） |
| `webui_auto_open` | 布尔 | `true` | 启动 bridge 时自动用默认浏览器打开面板 |
| `webui_password` | 字符串 | 空 | 面板登录密码的 PBKDF2 哈希，由面板写入。留空即不校验；手填明文不会生效 |

> **提示**：放开到 `0.0.0.0` 之前必须先设面板密码——没有密码时面板保存会拒绝，启动时也会退回 `127.0.0.1`。证书与私钥在「系统设置 → 安全与服务 → HTTPS 证书」里粘贴，服务端会先用 `ssl` 试着加载一遍，加载不了就拒绝保存；文件是用户自己换的（续期、换域名），所以每次启动都重新验，验不过就退回 HTTP 并在日志里说明，不会带着坏证书起不来。

面板的「配置备份与恢复」按 `/api/config/backup`、`/api/config/restore` 两个接口走：导出默认剔掉 `webui_password` 与 `ob_server_token`，恢复只覆盖备份里有的字段，动手前会把当前的 `config.json` 另存成 `config.json.bak-<时间戳>`。

## 存储与日志

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `log_max_mb` | 整数 | `8` | 单个日志文件的上限（MB），写满自动轮转 |
| `log_backup_count` | 整数 | `5` | 历史日志保留份数，超出后最旧的一份被删 |

`data/` 里的占用可以在「系统设置 → 存储」一页看完：数据目录总量、消息存档（`messages.db` 与 `msg_ids.db`）、图片缓存（`wx_cache/<账号>/images/`）、解密的微信数据库、日志（含历史份）与更新暂存各自占多少、多少文件。点「刷新」重新数一遍。

> **提示**：清理动作里有三样要留意。「清理解密缓存」删的是从微信本地库解出来的副本，桥接会立刻重新解一份——聊天内容不会少，但收件会卡一下；「按天数清理」与「清空存档」删的是消息存档，`message_id` 一旦发给客户端就是长期约定，删掉的记录再被 `get_msg` 查就查不到了；「日志轮转」只是把当前文件换成新的空文件，历史份按 `log_backup_count` 留存。图片缓存删了没有代价，下一张同样的图会重新解出来。

## 网络入口

一个入口是「一个方向的 OneBot 连接」。`ob_networks` 是入口数组，可以同时开多个，改它最省事的地方是面板的「节点配置」一页：同一类的入口收在一个标签下，点行上的按钮开关、编辑或删除，右上角「保存」统一写盘；只配了一条通用设置时，那一类下会显示一条「通用设置生成的入口」，点「转为独立入口」就把它变成一条可单独编辑的入口。留空（或没有这一项）时按 `ob_server_host`、`ob_server_port`、`ob_server_token` 起一个正向 WebSocket 服务端，旧配置可以原样继续用。

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
| `ob_heartbeat_seconds` | 整数 | `30` | 心跳间隔秒数，取 `0` 关闭心跳；非 0 时限制在 5–3600，越界按 30 处理。`lifecycle` 不受它影响 |
| `client_attachments` | 字符串 | 空 | 客户端附件目录的绝对路径。填了之后图片与文件的本地路径按该目录（及其 `wechat_images` 子目录）拼出来。面板入口在「节点配置」页的本机账号卡 |

## 收件

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `wechat_files_root` | 字符串 | 空 | 微信数据根目录，留空自动定位：先看「文档」，再看微信自己记的存储位置 |
| `wx_key_file` | 字符串 | 空 | 数据库口令文件，留空为配置目录下的 `wx_key.dat` |
| `wx_work_dir` | 字符串 | 空 | 解密副本与游标目录，留空为配置目录下的 `wx_cache` |
| `wx_poll_seconds` | 浮点 | `1.0` | 轮询本地库的间隔（秒） |
| `wx_backfill_seconds` | 整数 | `0` | 首次启动额外收进多少秒内的历史消息；`0` 表示只收启动之后的新消息 |
| `wxkey_manual_rva` | 整数 | `0` | 手动指定取口令的函数地址（如 `0x6148a0`），仅当微信改版导致自动定位失手时用 |

## 发送与隔离桌面

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `isolated_desktop` | 布尔 | `true` | 让微信跑在独立桌面上，发送时的前台切换只发生在那个桌面；前提是微信由本程序启动 |
| `isolated_desktop_name` | 字符串 | `FlandoraSandbox` | 隔离桌面名，同时跑多份实例时才需要改 |
| `wechat_exe` | 字符串 | 空 | 微信主程序路径，留空自动探测（含注册表登记的安装路径） |
| `wechat_hwnd` | 整数 | `0` | 指定要驱动的窗口句柄，`0` 取第一个；一台机器登录多个微信时才手填 |
| `allow_foreground_steal` | 布尔 | `false` | 仅在 `isolated_desktop` 为 `false` 时有意义。默认 `false` 时发送整体不可用；设为 `true` 后可用，代价是每条消息发出时前台短暂切到微信（实测切回耗时 3–11 毫秒） |
| `send_key` | 字符串 | `auto` | 发送消息用哪种方式。`auto` 先按回车，没发出去就清掉输入框、重新输入后点「发送」按钮，成功即记住；`enter` 固定按回车；`button` 固定点按钮。微信「设置 → 快捷键」里把发送键换成 `Ctrl+回车` 之后回车只会插换行，点按钮与那条设置无关，这时选 `button` 省掉一次失败 |

## 图片

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `wx_image_key_file` | 字符串 | 空 | 图片密钥文件，留空为配置目录下的 `wx_image_key.json` |

图片密钥与数据库口令不是一回事：AES 密钥只在微信解码图片的那几秒驻留内存，所以要趁微信正在打开一张没看过的图时去抓，取法见快速开始第 3 步。密钥是账号级的，换号后要重新取；缺它时图片消息退回 `[图片]` 占位，文字通道不受影响。
