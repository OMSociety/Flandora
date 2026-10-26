# 架构

这份文档说的是「东西在哪里、谁能调谁、一条消息怎么走」。规则部分与代码同源：
每条边界都有对应的守卫测试（见文末「守卫一览」），改动违反规则时测试先红。
改这里的行为之前先跑一遍那几条守卫，比读代码快。

## 分层

| 层 | 包 | 负责 | 不负责 |
|---|---|---|---|
| 入口 | `run_bridge.py`、`flandora/main.py` | 装日志、取机器人身份、抢单实例互斥量、起面板与桥接线程、账号切换兜底 | 业务判断 |
| 面板 | `flandora/webui/`（`app.py` 组合根、`deps.py` 取用层、`auth.py` 会话、`api/*` 12 组路由器、`monitor.py` 的 `/ws`） | HTTP、会话、静态前端、给界面推状态 | 读本地库、动微信 |
| 管理器 | `manager/`（`main.py` 入口、`paths.py` 路径与端口、`bridge.py` 桥接起停、`tray.py` 托盘图标与菜单） | 单实例锁、托盘菜单、按状态起停桥接、开机自启的读写入口 | 收发消息、业务判断 |
| 运行时 | `flandora/runtime/`（`lifecycle.py`、`runtime.py`、`events.py`） | 起停阶段与暂停、发送器/桥接/本地库/入口占用的持有、事件总线与日志中继 | 任何协议或平台细节 |
| 领域 | `flandora/domain/`（`message.py`、`content.py`、`send.py`、`store.py`、`window.py`） | 「一条消息是什么」的类型与约束、出站动作、收件库与发送器的访问协议 | 配置、日志、OneBot 字段名 |
| 收件 | `wx_source.py`、`wx_store.py`、`wx_crypto.py`、`wx_image.py`、`wx_image_key.py`、`wechat_account.py`、`ob_inbound.py` | 轮询微信本地库、解密、把正文解成消息段 | 决定要不要回、拼上下文 |
| 协议 | `ob_protocol.py`、`ob_network.py`、`ob_server.py`、`ob_segments.py`、`action_catalog.py`、`ob_ids.py`、`ob_store.py` | OneBot v11 编解码、四类网络入口、动作目录、ID 与名字映射 | 直接碰微信窗口 |
| 发送 | `senders.py`、`uia_sender.py`、`send_plan.py`、`wx_desktop.py`、`win32/windows.py` | 把动作投递进微信输入框、隔离桌面、窗口枚举与状态 | 解析 OneBot 请求 |
| 取钥 | `wizard.py`、`wxkey/`（`capture.py`、`locator.py`、`recover.py`、`store.py`） | 四步向导、内存取钥、DPAPI 落盘 | 收发消息 |
| 配置 | `flandora/config/`（`fields.py`、`store.py`、`paths.py`、`logging_setup.py`） | 字段声明与校验、读写 `data/config.json`、日志装配 | 任何运行期业务状态 |

`bridge_core.py` 是收件的组装点（把收件源的 dict 变成领域消息，再交给协议层渲染），
`ob_ids.py` 是「微信字符串 ↔ OneBot 整数 ID」的唯一映射层，两者都不属于上面任何一层的私有物。

## 依赖规则

1. **单向依赖**：`webui/` 可以 import 核心；核心（`bridge_core`、`ob_*`、`wx_*`、`uia_sender`、
   `storage`、`main`、`runtime`、`domain`）不许 import `flandora.webui`。组合根例外只有
   `main.py` 组装 `webui.app.run` 这一处。
2. **OneBot JSON 只有一处实现**：入站解码在 `ob_inbound.py`，出站渲染在 `ob_protocol.py`
   （`to_segment_dict` / `to_cq` / `render_message_event`），网络入口在 `ob_network.py` 与
   `ob_server.py`。
3. **进程级状态只有三个对象**：`runtime.lifecycle.LIFECYCLE`（阶段与暂停，唯一写者是起停线程）、
   `runtime.runtime.RUNTIME`（发送器、桥接、本地库、OneBot 入口占用、版本提示）、
   `ob_ids.SELF`（机器人身份）。其它模块读它们，不自己存一份。
4. **配置只经 `config.store.ConfigStore`**：字段、默认值、校验只在 `config/fields.py`；
   导入期不读盘、不建目录、不改配置。
5. **出站只有一条路径**：`domain/send.py::execute`。面板 `/api/send` 与 OneBot 的发送动作都汇到它。
6. **HTTP 路由只在 `flandora/webui/api/<组>.py`**：处理函数与 `ROUTES` 档位表在同一个文件，
   `webui/app.py` 只做组合。
7. **窗口能力只在 `win32/windows.py`**：`wx_desktop.py`（隔离桌面接管）与 `uia_sender.py`（投递）
   都向它要枚举与状态，两者互不 import。
8. **模块级可变全局**只允许第 3 条那三个对象与全大写常量/类型别名；存量登记在
   `tests/architecture_allowlist.py`，新增即红，删码要销账。

## 一次收消息

```text
wx_source.build_source()            按 poll_seconds 轮询 wlog 增量
  -> source.poll()                  产出统一形态的 dict（一条一条交）
  -> bridge_core._ingest()          去重（rawid）、过滤（自己发的、系统号）、日志
  -> Flandora.emit_message()
       -> _entry_for()              组装 InboundMessage：ChatRef/SenderRef/QuoteRef/正文/媒体
       -> events.record_inbound()   面板「消息」页留一份
       -> classify() 判类别        图片/表情包各起一个线程去解本地文件
       -> _notice_for()             撤回/拍一拍 → notice 事件，不进消息流
  -> _push_entry()
       -> ob_ids.contact_id()       微信字符串换成稳定整数 ID，名字进索引
       -> _inbound_segments()       Text/Mention/Reply + Image
       -> render_message_event()    Message -> OneBot 事件 dict（唯一出 dict 处）
  -> push_event()                   ob_network 按四类入口推给已连接的客户端
```

可观察点：面板「消息」与「日志」页、OneBot 客户端的 `message` 事件、`is_mentioned` 字段。
群消息全量上报，桥接不替客户端决定要不要回。

## 一次发消息

```text
入口 A：POST /api/send              webui/api/contacts.py
入口 B：OneBot action=send_msg      ob_protocol._handle_ob_api -> _prepare_message()
  -> _prepare_message()             请求 dict -> _OutboundPrep（文本/媒体/提及目标）
  -> domain/send.py::execute()      唯一执行入口：SendText / SendMention / SendMedia
       -> send_plan.plan_message()  段序列 -> 发送动作
       -> sender.send_text() 等      uia_sender 投递进微信输入框（隔离桌面里）
  -> SendOutcome(SENT/FAILED/REFUSED) 逐条结果，回给调用方按原样报告
```

发送失败会带上真实原因（窗口找不到、输入框有残留、多开冲突等），桥接不吞掉、也不伪造成功。
`/api/send` 与 OneBot 两条入口走同一个 `execute`，行为差异只在请求形状。

## 起停与暂停

`main.start_bridge()`：

1. `LIFECYCLE.begin_start()` 抢到 STARTING（已在跑则直接返回）。
2. `ONEBOT_SERVER.start()`：在专用事件循环里起网络管理器，等到所有服务端入口都在监听才返回；
   绑不上端口就返回 False 并清理干净。
3. 起 `sender-init` 线程（隔离桌面模式下先拉起微信）与 `bridge` 线程；后者
   `LIFECYCLE.mark_running()` 后进收件循环。

`main.stop_bridge()`：`begin_stop()` → 等桥接线程退出（最多 5 秒）→ `ONEBOT_SERVER.stop()`
（关入口、停管理器、关事件循环、释放占用）→ `mark_stopped()`。顺序是刻意的：先让收件停，
避免它往已经关掉的网络推事件。

`LIFECYCLE.pause()` 只把心跳与状态回报切成「已暂停/离线」，收件与推送照常；`resume()` 复原。
在线判定只有一处：`RUNTIME.online_flags()`，心跳与 `get_status` 都读它。

## 管理器

`manager/main.py` 拿 `Global\Flandora_Manager` 单实例锁后起托盘；桥接留在安装根目录，由
`manager/bridge.py` 按端口与进程状态判断在不在跑，菜单项随状态禁用。`manager/paths.py` 的
`install_dir()` 在冻结形态取 exe 所在目录、源码形态取仓库根，路径与端口只在这里定义。

托盘是一个通知区域图标，没有窗口：左键单击与菜单里的「打开面板」都打开网页面板，右键菜单
是「状态行（不可点）＋ 打开面板 / 启动桥接 / 停止桥接 / 重启桥接 / 打开日志 / 退出程序」。
图标绿色表示运行中、灰色表示已停止，悬停提示写同一句状态；状态每 2 秒刷新一次，菜单项的
可用状态跟着变。管理器已经在托盘里时再双击一次不会起第二个图标：桥接在跑就把面板打开，
没在跑就安静退出。

停桥接分两步：管理器把 `Global\Flandora_Stop` 事件置位；桥接侧 `flandora/stop_request.py`
的守候线程接住后依次 `stop_bridge()`、关隔离桌面上的微信、`webui.app.request_shutdown()`，
让 uvicorn 自己收尾退出，然后置 `shutdown` 结束 `main()`。管理器最多等 10 秒，超时才强杀进程。

强杀那一步按端口找占用者：7999 与 4950 上还在监听的进程都会被 `taskkill /F` 结束（管理器
自己的进程除外）。这两个端口是桥接的专用端口，配置里改过端口就以配置为准。

开机自启由 `flandora/autostart.py` 读写当前用户 `Run` 项下的 `Flandora` 值，面板的
「开机启动」与安装器的 `[Registry]` 写的是同一条值。管理器本身不判断自启状态。

## 面板

`webui/app.py` 的 `create_app()` 按固定顺序装：鉴权中间件 → 12 组业务路由器 → `/ws` →
静态前端（`flandora/webui/dist`，缺失时返回 503 并给构建命令）。

每个 `webui/api/<组>.py` 导出：

- `router`：FastAPI 路由器；
- `ROUTES`：`{路径: {方法: "call" | "defer"}}`。`call` 表示无不可逆副作用、冒烟里真打一次；
  `defer` 表示要真机条件（真微信、真桌面、外网或会删真实数据），原因写在同文件的
  `DEFER_REASONS`，键是 `"METHOD 路径"`。

`webui/deps.py` 是取用层：`store()` / `sender()` / `config_store()` / `status_payload()` 都从这里拿。
`status_payload()` 是 `/api/status` 与 `/api/control` 回包共用的同一份形状；`/ws` 首帧用的是
`runtime/events.py` 的 `panel_snapshot()`（日志、消息与计数器），两者不混用。

## 配置

`config.bootstrap()` 在 `main()` 第一行调用：加载 `data/config.json`、按示例生成缺失文件、装配日志。
之后 `config.X` 读到的都是校验过的值（回退过的字段在 `Settings.warnings` 里留痕）。

保存走 `store.update(payload)`：只对本轮提交的键严格校验（`strict=True` 拒绝非法值），
其余键的盘上旧值按同一套 `clean_field()` 规则回退生效。改动是否需要重启由字段的 kind 派生
（`HOT` 热生效、`RESTART` 需重启、`SECRET` 不回显），面板的 `restart_required` 就是这些键的并集。

热生效的字段里，网络入口是最需要「保存即生效」的一块：`ob_network.NetworkManager` 在 `start()`
时订阅配置，`ob_networks` 或三项兜底设置一变就调 `apply()` 把运行态对齐——新增的入口起、
删掉的断开、改过的按新配置重建、没动的连接不受影响。`apply()` 任意线程可调，任务操作由它送回
入口线程执行；面板上那条「待保存」写的就是「文件里有、运行态里没有」，入口改完不必重启桥接。

字段声明、默认值、校验与逐键说明的唯一来源是 `config/fields.py` 的 `Field.desc`；`flandora/config.example.json`
由 `python tools/gen_config_example.py` 生成（`--check` 挡漂移），`data/config.json` 只存「键: 值」，
`_comment*` 与其它未知键在读写时丢弃，落盘顺序按字段声明。面板「高级」页右侧的说明与默认值来自
`GET /api/config/schema`。

## 前端

`webui-frontend/src/pages/index.js` 的 `PAGES` 是页面登记表：id、分组、标题、图标名、说明、组件。
侧栏、连接与哈希路由、图标白名单、Playwright 用例都从这张表派生，加页面只改这一处。
`icon` 必须是 `AppIcon.vue` 导出的 `ICON_NAMES` 之一。

状态轮询只有 `store.js` 的 `startStatusPolling()` 一处（页面不可见时跳过）。
离开有未保存改动的页面经 `useDirtyGuard.js` 拦一道确认框。

## 守卫一览

| 守卫 | 断言 |
|---|---|
| `tests/test_webui_architecture.py::test_core_never_imports_webui` | 核心包不 import `flandora.webui` |
| `...::test_app_has_no_route_decorators` | `webui/app.py` 里没有路由装饰器 |
| `...::test_route_total_is_pinned` | 路由总数等于登记值（加端点必须同步档位表） |
| `...::test_api_modules_are_aggregated_and_own_their_routes` | 每个路由器都被聚合，且 `ROUTES` 与真实注册一致 |
| `...::test_every_deferred_route_declares_a_reason` | 每条 `defer` 都有原因 |
| `...::test_window_senders_do_not_import_each_other` | `wx_desktop` 与 `uia_sender` 互不 import |
| `...::test_win32_windows_is_the_single_window_capability_home` | 窗口枚举只在 `win32/windows.py` |
| `tests/test_webui_endpoints.py` | 全部 `call` 档端点冒烟不 5xx；控制端点真的调到桥接模块 |
| `tests/test_runtime_lifecycle.py` | 起停阶段唯一写者、暂停语义、`online_flags()` 是唯一在线判定 |
| `tests/architecture_allowlist.py` | 模块级可变全局棘轮（新增即红） |
| `tests/test_domain_send_entries.py` | 面板与 OneBot 两条入口都汇到 `execute` |
| `tests/test_ob_server_lifecycle.py` | 入口起停真的监听与真的释放端口 |
| `tests/test_manager_ports.py` | 端口字面量只出现在 `manager/paths.py` |
| `tests/test_manager_bridge.py` | 桥接探测按进程与端口判定；停止先请求、超时才强杀 |
| `tests/test_manager_tray.py` | 菜单按状态禁用、左键默认项是打开面板、退出先停桥接 |
| `tests/test_autostart.py` | Run 项只写一条带引号的命令行；管理器入口缺失时拒绝写 |
| `webui-frontend/tests/console.spec.js` | 页面登记表、图标白名单、脏值确认框、轮询行为与开机启动勾选 |

## 扩展点

| 要做什么 | 改哪里 | 别忘 |
|---|---|---|
| 加一个动作 | `action_catalog.py` 加动作与其参数，`ob_protocol._handle_ob_api` 实现 | `python tools/gen_sdk_catalog.py` 重导快照 |
| 加一个 HTTP 端点 | `webui/api/<组>.py` 写处理函数 | 在同文件 `ROUTES` 登档位，`defer` 写原因 |
| 加一个收件消息类型 | `wx_store` 解出字段 → `domain.message` 的段类型 → `ob_inbound` 解码 | `ob_protocol.to_segment_dict` 补渲染 |
| 加一个发送能力 | `domain/send.py` 的动作 → `send_plan` → `uia_sender` 的投递 | 面板与 OneBot 都自动复用 |
| 加一个配置项 | `config/fields.py` 加字段与 `desc` → 跑 `python tools/gen_config_example.py` → `docs/config.md` | 前端 `Config.vue` 的 `DEFAULTS`、`tests/fixtures.js` |
| 加一个面板页面 | `webui-frontend/src/pages/index.js` 的 `PAGES` | 图标名要在 `ICON_NAMES` 里 |
| 加一个托盘菜单项 | `manager/tray.py` 的 `_menu()` 与对应处理函数 | 状态相关的项按 `_running` 传 `enabled`，菜单随状态重建 |
| 改开机自启 | `flandora/autostart.py` | 安装器 `[Registry]` 写同一条值；`tests/test_autostart.py` 对表 |
