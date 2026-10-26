# AGENTS.md — Flandora Agent 宪法

适用范围：本仓库全部目录；子目录另有 AGENTS.md 时，以更接近改动点的那份为准。
最后更新：2026-10-25

## 项目概览

一句话定位：把微信个人号接入 OneBot v11 的桥接服务（任意 OneBot v11 客户端通过 WebSocket 收发），
收消息读微信本地加密库，发消息走 Windows UI 自动化。目标平台 Windows。

技术栈：

- Python 3.13；命令里的 `python` 指它（自建虚拟环境时先激活，本仓库不自带也不要求 venv），
  运行期依赖见 `flandora/requirements.txt`
- FastAPI + uvicorn：控制面板 `http://127.0.0.1:4950`
- websockets 16–17：OneBot v11 服务端（默认 7999，以 `data/config.json` 为准）
- Vue 3.5 + Vite 6（`webui-frontend/`），图标 `@lucide/vue`
- pytest（后端）、Playwright（前端），PyInstaller + Inno Setup（打包）
- 专有依赖：`uiautomation`（窗口自动化）、`frida`（只打进取钥器）、`zstandard`、`Pillow`

文档索引：

- 用户向说明（概览、能力与快速开始）：[README.md](./README.md)
- 协议面（ID 约定、动作面、消息段、事件面、返回码）：[docs/protocol.md](./docs/protocol.md)
- 控制台八个页面与数据落点：[docs/console.md](./docs/console.md)
- 配置与更新：[docs/config.md](./docs/config.md)
- 客户端工具（Python SDK、MCP 服务）：[docs/sdk.md](./docs/sdk.md)
- 开发环境、命令与验收：[docs/development.md](./docs/development.md)
- 已知限制：[docs/limitations.md](./docs/limitations.md)
- 代码结构、依赖规则与扩展点：[docs/architecture.md](./docs/architecture.md)
- 真机验收清单：[docs/smoke-test.md](./docs/smoke-test.md)
- 界面设计规范：[DESIGN.md](./DESIGN.md)（配色、字体、间距与组件写法，不描述代码架构）、
  产品口径：[PRODUCT.md](./PRODUCT.md)
- 底层格式与桌面机制的实验记录：[docs/known-limits-dat-format.md](./docs/known-limits-dat-format.md)、[docs/known-limits-desktop.md](./docs/known-limits-desktop.md)

## 常用命令

全部在仓库根执行，PowerShell 语法：

| 目的 | 命令 |
|---|---|
| 装运行期与开发依赖 | `python -m pip install -r requirements-dev.txt`（它 `-r` 了 `flandora/` 与 `manager/` 两份运行期清单） |
| 跑全部后端测试 | `python -m pytest tests -q` |
| 跑单个测试文件 | `python -m pytest tests/test_ob_server.py -q` |
| 装 SDK / MCP（下游包） | `python -m pip install -e sdk\python` 与 `python -m pip install -e mcp\python`；自检 `python -m flandora_mcp.server --selftest` |
| 重新导出动作快照 | `python tools/gen_sdk_catalog.py`（改动作后必跑，CI 核对无差异） |
| 生成/校验配置示例 | `python tools/gen_config_example.py`（改字段或说明后必跑；`--check` 在 CI 里挡漂移） |
| 语法检查 | `python -m compileall -q flandora manager sdk mcp tools` |
| 核对每个测试文件都被收集 | `python tools/check_test_collection.py`（零收集即失败） |
| Lint | `python -m ruff check .` |
| 格式化 | `python -m ruff format .`（CI 用 `--check`，不通过即红） |
| 同步版本号 | `python tools/sync_version.py`（改 `flandora/__init__.py` 的 `__version__` 后必跑；`--check` 在 CI 里挡漏改） |
| 依赖漏洞扫描 | `$env:PYTHONUTF8=1; python -m pip_audit -r flandora/requirements.txt`（清单带中文注释，Windows 代码页下不设 UTF-8 会解码失败） |
| 起桥接（源码） | `python run_bridge.py` |
| 装前端依赖 | `cd webui-frontend; npm install` |
| 跑前端测试 | `cd webui-frontend; npm test`（Playwright 自动在 4175 起 vite，无需手工起服务） |
| 构建前端 | `cd webui-frontend; npm run build`（产物写入 `flandora/webui/dist`） |
| 打包前校验 | `python build.py --check`（版本号、载荷目录、`--add-data`、前端产物、安装脚本引用） |
| 打包发行版 | `python build.py`（前端产物比源码旧时自动重建；`--skip-web` 复用现有产物，缺产物直接报错） |

lint 与格式化统一用 ruff，配置在仓库根的 `pyproject.toml`（line-length 100、`E/F/W/I` 规则、
`E501` 交给 formatter）。仓库没有类型检查配置，不要凭经验编造 mypy/pyright 命令。

## 架构边界

```text
run_bridge.py -> flandora.main
   收件：wx_source -> wx_store -> wx_crypto/zstandard -> bridge_core -> ob_protocol -> ob_network -> OneBot 客户端
   更新：webui/api/update.py -> updater（下载/暂存/生成替换脚本）+ 退出后执行的 PowerShell 脚本
   发件：WebUI /api/send 或 OneBot 调用 -> senders.create_sender() -> uia_sender -> wx_desktop（隔离桌面）
   面板：flandora/webui/api/*.py 的 11 个路由器（各自导出 ROUTES 与 DEFER_REASONS），webui/app.py 是组合根
   进程级状态：flandora/runtime/（LIFECYCLE 管阶段/暂停/桥接线程，RUNTIME 管 sender、bridge、OneBot 入口占用与版本提示）
   身份与名字索引：flandora/ob_ids.py（SELF、remember_name/lookup_name，账号级，换账号一起复位）
   OneBot 入口线程：flandora/ob_server.py（OneBotServer 起停专用事件循环与网络管理器）
   取钥：wizard（状态机）-> wxkey（capture/locator/recover）+ wxkey.store（DPAPI）
```

- **依赖单向**：`webui/`（含 `webui/api/*`）可以 import 核心；核心（`bridge_core`、`ob_*`、`wx_*`、
  `uia_sender`、`storage`、`main`、`runtime`、`domain`）不许 import `flandora.webui`
  （`tests/test_webui_architecture.py::test_core_never_imports_webui`）。组合根例外只有 `main.py` 组装 `webui.app.run`。
- **HTTP 路由只写在 `flandora/webui/api/<组>.py`**，且必须在 `ROUTES` 登记 `call`/`defer`（`defer` 同时写
  `DEFER_REASONS`）；`webui/app.py` 不许出现路由装饰器（`test_app_has_no_route_decorators`、
  `test_every_deferred_route_declares_a_reason`）。
- **OneBot 编解码只有一处实现**：只有 `ob_protocol.py`/`ob_network.py`/`ob_server.py` 产出 OneBot JSON
  与网络入口；入站解码在 `ob_inbound.py`，出站渲染只经 `ob_protocol.py` 的 `to_segment_dict`/`to_cq`/`render_message_event`。
- **进程级状态只有三个**：`runtime.lifecycle.LIFECYCLE`（起停与暂停，唯一写者）、
  `runtime.runtime.RUNTIME`（sender/bridge/OneBot 入口认领/更新提示）、`ob_ids.SELF`（自身身份）；
  别的模块要读就用 `LIFECYCLE.snapshot()`/`RUNTIME.online_flags()`，不许自己存一份
  （`tests/test_runtime_lifecycle.py`、`tests/architecture_allowlist.py`）。
- **配置只经 `config.store.ConfigStore` 读写**，字段、默认值与校验只在 `config/fields.py`；
  不许 import 期改配置、不许模块级 `config.X = ...`（`config.__init__` 里有兼容代理，但新代码走 `store.update()`）。
- **出站唯一路径 `domain/send.py::execute`**：面板与 OneBot 两条入口都汇到它，不许新增第四条发送路径
  （`tests/test_domain_send_entries.py`）。
- **模块级可变全局**只允许上面第 4 条的三个对象与全大写常量/类型别名，存量登记在
  `tests/architecture_allowlist.py`，新增即红。
- **前端页面只在 `webui-frontend/src/pages/index.js` 的 `PAGES` 登记**，icon 必须是 `AppIcon.vue`
  图标集里的名字（该文件导出的 `ICON_NAMES` 是白名单，`webui-frontend/tests/console.spec.js` 的
  `every registered page icon exists in the AppIcon whitelist` 逐项核对）。

## 关键目录

| 我要… | 改这里 |
|---|---|
| 改进程级状态（启停阶段、暂停、桥接线程、发送器与 OneBot 入口占用） | `flandora/runtime/lifecycle.py`、`flandora/runtime/runtime.py` |
| 改机器人身份或「整数 ID → 显示名」索引 | `flandora/ob_ids.py` |
| 改 OneBot 入口线程的起停 | `flandora/ob_server.py`（`OneBotServer.start()/stop()`） |
| 加 API 端点 | `flandora/webui/api/<组>.py`：加处理函数并在该模块 `ROUTES` 登记 `call`/`defer`，`defer` 的路由还要在 `DEFER_REASONS` 写原因；`app.py` 只负责组合，不要往里加路由 |
| 加一个控制台页面 | `webui-frontend/src/pages/index.js` 的 `PAGES` 加一项（id/group/label/icon/description/component），侧栏、路由与 Playwright 用例都从这张表派生 |
| 改收件（读库、字段解析） | `flandora/bridge_core.py`，数据层在 `flandora/wx_store.py` |
| 改发送（投递、清空输入框） | `flandora/uia_sender.py` |
| 改桌面隔离与接管 | `flandora/wx_desktop.py` |
| 加后端测试 | `tests/test_<模块>.py`，Windows 专属用例直接调用桌面 API |
| 加前端测试 | `webui-frontend/tests/console.spec.js`，夹具在 `tests/fixtures.js` |
| 改「微信画面」页与隔离桌面控件 | `webui-frontend/src/pages/Desktop.vue`，端点见 `flandora/webui/api/desktop.py` 的 `/api/desktop/*` |
| 改打包或安装器 | `build.py`、`installer/Flandora.iss`（版本与载荷目录在 `installer/version.iss`，由同步脚本生成） |
| 改更新与替换 | `flandora/updater.py`、`flandora/webui/api/update.py` 的 `/api/update/*` |
| 改事件总线或窗口能力 | `flandora/runtime/events.py`、`flandora/win32/windows.py`（两者都是被依赖方，不许反向 import 上层） |

## 数据流

`wx_source` 轮询微信本地库（`wlog` 增量）→ `wx_store` 只读查询配合 `wx_crypto` 解密 → `bridge_core`
组装 OneBot 事件（群消息带 `is_mentioned`；撤回与拍一拍先摘成 `notice`，不进消息流）→ `ob_protocol` 整形 → `ob_network` 按入口推给已连接的客户端，其中每条连接建立时推 `lifecycle(connect)`、停止时推 `disable`，心跳按 `ob_heartbeat_seconds` 定时推；
发送方向由 `senders` 选出的 `uia_sender` 在隔离桌面上把文本投递进微信输入框并回车。

启停方向：`main.start_bridge()` 先 `OneBotServer.start()` 再起桥接线程，`stop_bridge()` 先
`LIFECYCLE.begin_stop()` 让收件循环退出（最多等 5 秒）再 `OneBotServer.stop()` 释放端口，最后
`LIFECYCLE.mark_stopped()`。暂停（`LIFECYCLE.pause()`）只把心跳与状态回报切成「已暂停/离线」，
收件与推送照常。

## 修改契约

- 发版先改 `flandora/__init__.py` 的 `__version__`，再跑 `python tools/sync_version.py`：它把版本写进
  `sdk/python` 与 `mcp/python` 的 `__init__.py`/`pyproject.toml`，并生成 `installer/version.iss`
  （`AppVersion` 与 `PayloadDir`）。`build.py` 不写字面量，运行期从包内读；别处再出现版本字面量会被
  `--check` 判为漂移。
- 增删配置项：依次改 `flandora/config/fields.py`（字段声明：键名、默认值、kind、校验与 `desc`）
  → 跑 `python tools/gen_config_example.py` 重新生成 `flandora/config.example.json`（示例与 `_comment_`
  说明都由字段表派生，不要手改）→ `docs/config.md` 里对应的一节 →
  `webui-frontend/src/pages/Config.vue` 的 `DEFAULTS`（仅少数需兜底的键）→
  `webui-frontend/tests/fixtures.js`。
  界面没有逐字段表单：多数配置项在「系统设置 → 高级」里直接编辑 JSON，右侧逐键说明与默认值来自
  `GET /api/config/schema`（即字段表的 `desc`），所以界面侧不需要逐项加控件；
  但 `DEFAULTS` 与 README 漏了就会出现「后端认、文档或界面不认」的静默不一致。
  取值校验统一写在 `flandora/config/fields.py` 的 `clean_field()`（文件加载与面板保存共用；
  `strict=True` 拒绝、否则按规则回退），面板侧只留请求编排（密码换哈希、PEM 落文件、绑全网卡前要有密码）。
- 改 `/api/status`、`/api/desktop/status` 等响应字段：同步 `webui-frontend/tests/fixtures.js` 的夹具，
  否则前端断言与后端字段脱节，测试仍会绿。
- 改 OneBot 事件字段或协议行为：改 `flandora/ob_protocol.py`，并跑 `tests/test_ob_server.py`；动通知事件或心跳时另跑 `tests/test_ob_notice.py`、`tests/test_ob_heartbeat.py`、`tests/test_ob_network.py`（后两者起真端口，meta_event 会混进会话，等事件要跳过它）。
  群历史里的发言人名字走 `bridge_core.resolve_names()` 这个公开方法，直接调用即可；改名由导入期报错暴露，
  不会再退化成静默传给 wxid。
- 改前端：构建工具已经会按 mtime 判断 `flandora/webui/dist` 是否需要重建，正常打包不必先手工 `npm run build`；
  只有明确要复用现有产物时才用 `python build.py --skip-web`（它会打印产物与源码的时间差）。
- 改窗口或桌面行为：只动 `flandora/wx_desktop.py`，跑 `tests/test_wx_desktop.py`，再重启桥接实测；
  真实窗口绑定只能由桥接进程自己做，其它进程会拿到「事件无法调用任何订阅者」。
- 改依赖：`pip install` 后同步 `flandora/requirements.txt` 里的版本说明；前端锁文件
  `webui-frontend/package-lock.json` 与 `package.json` 一起提交。开发工具（pytest/ruff/pip-audit）只进
  `requirements-dev.txt`。

## 禁止操作

- 禁止改 `data/` 下密钥与口令的文件名。原因：用户按名字找它们，改名等于把已取的口令丢掉。
- 改 `flandora/wxkey/store.py` 的 `ENTROPY`（当前 `b"Flandora/wxkey/v1"`，`FORMAT_VERSION` 为 2）
  或口令文件的字段布局时，必须同时抬 `FORMAT_VERSION`。原因：熵参与 DPAPI 派生，属于落盘格式，
  不抬版本只会让既有文件报 DPAPI 错误码，看不出要重新取钥。
- 禁止在桥接里决定「要不要回」（@ 过滤、拼上下文、表情学习、图片描述都别做）。原因：那属于 OneBot 客户端职责，越界会让客户端收不到群里的消息。
  群消息一律全量上报，用 `is_mentioned` 如实标记是否被 @。
- 禁止静默结束用户自己桌面上的微信。原因：那可能是用户正在用的账号。挡住隔离桌面时，
  只由用户在控制台点「结束桌面上的微信」（`POST /api/desktop/quit-default-wechat`）。
- 禁止前端直接接触微信或本地库。原因：浏览器侧只有 `/api/*` 一个入口，绕过它就拿不到密钥与桌面句柄。
- 禁止手改 `flandora/webui/dist/`。原因：那是 vite 生成物且不入库；改源码后重新构建。
- 禁止给日志与控制台输出加 emoji。原因：Windows 控制台默认 GBK，`build.py` 曾因此打包成功
  却以退出码 1 结束。

## 验收标准

改动完成 = 下列全部通过：

1. `python -m compileall -q flandora manager sdk mcp tools`
2. `python -m ruff check .` 与 `python -m ruff format --check .`
3. `python -m pytest tests -q`
4. `python tools/check_test_collection.py`（每个 `tests/test_*.py` 都要被 pytest 收到用例）
5. `python tools/gen_config_example.py --check`（配置示例与字段说明一致）
6. `python tools/sync_version.py --check`（版本号只在 `flandora/__init__.py` 写一次）
7. `python build.py --check`（改了打包、安装器或前端产物时必跑）
8. 改了 `webui-frontend/` 时追加：`cd webui-frontend; npm test` 与 `npm run build`
9. 涉及桌面、发送、取钥或更新链路的改动，按 [docs/smoke-test.md](./docs/smoke-test.md) 在真机上走一遍

`.github/workflows/ci.yml` 在 push 到 `main` 与 PR 上跑同一组命令，另加 `package` job（真跑 `build.py`
并断言载荷清单）与前端 job。本地先跑绿能省一轮往返。真实环境验证需要 Windows 桌面与已登录的微信，
测试通过不能替代这一步。

新增测试文件必须有 `def test_*`（pytest 收集到用例，收集闸会拦零收集的文件）；
需要单文件直跑看逐条结果时再补 `main()`，那是可选项，范式见 `tests/test_wxkey_store.py`。

## 提交与协作

- 提交信息用中文，前缀 `feat:` / `fix:` / `docs:` / `refactor:`；破坏性变更写 `feat!:` 或 `fix!:`
  并在 body 逐条列出破坏点（改了哪个配置键、哪个文件名、哪个行为）。
- 仓库不维护 CHANGELOG，变更说明写在 commit body。
- 直接提交到 `main`。

## 待办与未实现

- 语音与视频本体：真机实测（2026-10-24 私聊收到一条语音 + 一个视频）后确认，本体**不在本地磁盘**：
  语音是 `local_type=34`，XML 只给 `voiceurl`/`aeskey`/`length`（5699 字节，`voiceformat=4`）；
  视频是 `local_type=43`，XML 只给 `cdnvideourl`/`aeskey`/`length`（2.7 MB）/`md5`，本地仅落
  未加密缩略图 `msg/video/<月>/<hash>_thumb.jpg`。全盘 `*.silk`/`*.amr`/`*.mp4` 计数为 0。
  要做等于实现微信 CDN 下载协议 + AES 解密 + silk 转码，是独立工程。**决定（2026-10-24）：暂不实现，
  继续按 `[语音]`/`[视频]` 占位文本上报**。真要推进时先试最便宜的一步——让人在客户端里播放一次，
  再看内容有没有落到本地缓存；落盘了就不必碰 CDN 协议。证据与字段见 `docs/wip.md` 第二节。
- 撤回（`delete_msg`）：气泡能定位（聊天区里是 `ListItemControl`，Name 就是正文；会话列表里也有同名条目，靠 x 坐标区分），
  但聊天区是自绘渲染子窗 `MMUIRenderSubWindowHW`，向主窗与它分别投 `WM_RBUTTONDOWN/UP`、`WM_CONTEXTMENU` 都不出菜单。
  对照：同样往主窗投右键点会话列表能弹出会话菜单，说明投递本身有效，是这块子窗不认合成消息；非活动桌面上
  `SetCursorPos` 直接失败，真实鼠标也没有落点。要么把隔离桌面切到前台（会占用户屏幕），要么由人在客户端里点。
  实测记录见 `docs/needs-user.md` 第 2 节。
- 拍一拍（`send_poke` / `friend_poke`）：头像与消息气泡在同一块自绘区域，和撤回撞同一堵墙。收件侧不受影响，
  别人拍一拍会以 `notice/notify + sub_type=poke` 推出去（`tests/test_ob_notice.py` 覆盖）。
- 引用回复（发送方向的 `reply` 段）：微信 PC 引用的入口同样是右键消息菜单，因此与撤回同一限制。收件方向已通
  （`refermsg` 的 `svrid` 回查 `msg_ids` 得到被引 `message_id`），只有「主动引用某条历史消息」这一半挂着。
- 语音转文字（ASR）已移除，不属于桥接职责：转写与回复策略归 OneBot v11 客户端。

## 已知风险区

| 路径 | 风险 | 改动前后置动作 |
|---|---|---|
| `flandora/wx_desktop.py` | 曾在全系统扫 `Weixin.exe` 进程，把本程序留在沙箱里的实例误判成用户自己开的微信，导致重启后画面 404、发送绑不上窗口 | 跑 `tests/test_wx_desktop.py`；重启后确认日志出现「已接管隔离桌面上正在运行的微信」 |
| `flandora/uia_sender.py` | 输入框有清不掉的残留时会取消发送，避免把旧草稿当新消息发出去 | 跑 `tests/test_clear_input.py`；失败原因在 `sender._last_error` |
| `flandora/bridge_core.py` | 群消息语义（全量上报、`is_mentioned`、群内按发言人分 user_id）是设计约定 | 跑 `tests/test_group_passthrough.py` |
| `flandora/wxkey/store.py` | DPAPI 额外熵参与派生，改动即作废已存口令 | 改熵值或字段布局须同时抬 `FORMAT_VERSION`；见「禁止操作」 |
| `webui-frontend/src/pages/Wizard.vue` | 2 秒轮询曾把免责声明勾选冲回 false，第一步永远无法完成 | 跑 Playwright 用例 `wizard keeps the disclaimer tick across status polling` |
| `flandora/webui/dist` 与 `build.py` | 前端产物比源码旧时会把旧界面打进安装包 | 打包前跑 `python build.py --check`；确认它打印的产物时间不早于源码 |
| `flandora/updater.py` | 替换脚本在程序退出后才动手，写错路径或新版本起不来会让程序目录处于半更新状态 | 改完跑 `tests/test_updater.py`；发版前按 `docs/smoke-test.md` 第 9 节走一遍回滚路径 |

## 出错怎么办

| 症状（报错里的可检索片段） | 处理 |
|---|---|
| `IndentationError` / `unexpected indent` | 编辑时删掉了行尾换行，把 `def` 与 docstring 并成一行：先 `compileall` 定位再修 |
| `隔离桌面启动失败：默认桌面上已有微信在运行` | 退出自己桌面的微信，或在控制台点「结束桌面上的微信」 |
| `找不到微信可执行文件` | 在 `data/config.json` 把 `wechat_exe` 填成 `Weixin.exe` 完整路径；`/api/desktop/status` 的 `exe_found` 应为 `true` |
| `前端尚未构建` / 面板 503 | `cd webui-frontend; npm install; npm run build` |
| `输入框有清不掉的残留` | 在「微信画面」里手动清空输入框再重试；根因看日志里的残留文本 |
| `事件无法调用任何订阅者` | 窗口绑定只能由桥接进程做，别用独立脚本试绑沙箱窗口，改为重启桥接后验证 |
| `版本号漂移` / `PayloadDir 指向` | 先改 `flandora/__init__.py` 的 `__version__`，再跑 `python tools/sync_version.py` |

## 维护

本文件与代码同一个提交更新。改动以下内容时必须同步本文件：验收命令、配置项清单、
端口默认值、依赖版本下限、新增的禁止操作。发现本文件与代码不符时，先改本文件再继续改代码。
