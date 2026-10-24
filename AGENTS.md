# AGENTS.md — Flandora Agent 宪法

适用范围：本仓库全部目录；子目录另有 AGENTS.md 时，以更接近改动点的那份为准。
最后更新：2026-10-24

## 项目概览

一句话定位：把微信个人号接入 OneBot v11 的桥接服务（任意 OneBot v11 客户端通过 WebSocket 收发），
收消息读微信本地加密库，发消息走 Windows UI 自动化。目标平台 Windows。

技术栈：

- Python 3.13（仓库根 `.venv`，已 gitignore），运行期依赖见 `flandora/requirements.txt`
- FastAPI + uvicorn：控制面板 `http://127.0.0.1:8766`
- websockets 16–17：OneBot v11 服务端（默认 7999，以 `data/config.json` 为准）
- Vue 3.5 + Vite 6（`webui-frontend/`），图标 `@lucide/vue`
- pytest（后端）、Playwright（前端），PyInstaller + Inno Setup（打包）
- 专有依赖：`uiautomation`（窗口自动化）、`frida`（只打进取钥器）、`zstandard`、`Pillow`

文档索引：

- 用户向说明与配置详解：[README.md](./README.md)
- 设计基线：[DESIGN.md](./DESIGN.md)、产品口径：[PRODUCT.md](./PRODUCT.md)
- 已知限制：[docs/known-limits-desktop.md](./docs/known-limits-desktop.md)、[docs/known-limits-dat-format.md](./docs/known-limits-dat-format.md)

## 常用命令

全部在仓库根执行，PowerShell 语法：

| 目的 | 命令 |
|---|---|
| 装运行期与开发依赖 | `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`（它 `-r` 了 `flandora/` 与 `manager/` 两份运行期清单） |
| 跑全部后端测试 | `.venv\Scripts\python.exe -m pytest tests -q` |
| 跑单个测试文件 | `.venv\Scripts\python.exe -m pytest tests/test_ob_server.py -q` |
| 装 SDK / MCP（下游包） | `-m pip install -e sdk\python` 与 `-m pip install -e mcp\python`；自检 `-m flandora_mcp.server --selftest` |
| 重新导出动作快照 | `.venv\Scripts\python.exe tools/gen_sdk_catalog.py`（改动作后必跑，CI 核对无差异） |
| 语法检查 | `.venv\Scripts\python.exe -m compileall -q flandora manager sdk mcp` |
| Lint | `.venv\Scripts\python.exe -m ruff check .` |
| 格式化 | `.venv\Scripts\python.exe -m ruff format .`（CI 用 `--check`，不通过即红） |
| 起桥接（源码） | `.venv\Scripts\python.exe run_bridge.py` |
| 装前端依赖 | `cd webui-frontend; npm install` |
| 跑前端测试 | `cd webui-frontend; npm test`（Playwright 自动在 4175 起 vite，无需手工起服务） |
| 构建前端 | `cd webui-frontend; npm run build`（产物写入 `flandora/webui/dist`） |
| 打包发行版 | `.venv\Scripts\python.exe build.py`（产物在 `dist/`，Inno Setup 缺失时只出 portable zip） |

lint 与格式化统一用 ruff，配置在仓库根的 `pyproject.toml`（line-length 100、`E/F/W/I` 规则、
`E501` 交给 formatter）。仓库没有类型检查配置，不要凭经验编造 mypy/pyright 命令。

## 架构边界

```text
run_bridge.py -> flandora.main
   收件：wx_source -> wx_store -> wx_crypto/zstandard -> bridge_core -> ob_protocol -> ob_network -> OneBot 客户端
   更新：webui 端点 -> updater（下载/暂存）+ 退出后执行的 PowerShell 替换脚本
   发件：WebUI /api/send 或 OneBot 调用 -> senders.create_sender() -> uia_sender -> wx_desktop（隔离桌面）
   面板：flandora/webui/app.py（FastAPI）读写 state 与 bridge_core，并静态托管 flandora/webui/dist
   取钥：wizard（状态机）-> wxkey（capture/locator/recover）+ wxkey.store（DPAPI）
```

- 允许：`webui/app.py` 调用任意后端模块；`bridge_core` 调用收件源与发送器。
- 禁止：窗口与桌面操作散落到 `wx_desktop` / `uia_sender` 之外。原因：微信跑在独立桌面
  `WeBotSandbox` 上，只有持有该桌面句柄的进程能绑定窗口，旁路实现会静默失败。
- 禁止：前端直接接触微信或本地库。原因：浏览器侧只有 `/api/*` 一个入口，绕过它就拿不到密钥与桌面句柄。

## 关键目录

| 我要… | 改这里 |
|---|---|
| 加 API 端点 | `flandora/webui/app.py`（仿 `/api/desktop/status`，docstring 里同步端点清单） |
| 加一个控制台页面 | `webui-frontend/src/pages/`，再在 `src/App.vue` 注册路由与侧栏项 |
| 改收件（读库、字段解析） | `flandora/bridge_core.py`，数据层在 `flandora/wx_store.py` |
| 改发送（投递、清空输入框） | `flandora/uia_sender.py` |
| 改桌面隔离与接管 | `flandora/wx_desktop.py` |
| 加后端测试 | `tests/test_<模块>.py`，Windows 专属用例直接调用桌面 API |
| 加前端测试 | `webui-frontend/tests/console.spec.js`，夹具在 `tests/fixtures.js` |
| 改「微信画面」页与隔离桌面控件 | `webui-frontend/src/pages/Desktop.vue`，端点见 `flandora/webui/app.py` 的 `/api/desktop/*` |
| 改打包或安装器 | `build.py`、`installer/Flandora.iss` |
| 改更新与替换 | `flandora/updater.py`、`flandora/webui/app.py` 的 `/api/update/*` |

## 数据流

`wx_source` 轮询微信本地库（`wlog` 增量）→ `wx_store` 只读查询配合 `wx_crypto` 解密 → `bridge_core`
组装 OneBot 事件（群消息带 `is_mentioned`；撤回与拍一拍先摘成 `notice`，不进消息流）→ `ob_protocol` 整形 → `ob_network` 按入口推给已连接的客户端，其中每条连接建立时推 `lifecycle(connect)`、停止时推 `disable`，心跳按 `ob_heartbeat_seconds` 定时推；
发送方向由 `senders` 选出的 `uia_sender` 在隔离桌面上把文本投递进微信输入框并回车。

## 修改契约

- 增删配置项：依次改 `flandora/config.py` → `flandora/config.example.json` → `README.md`「配置详解」
  → `webui-frontend/src/pages/Config.vue` 的 `DEFAULTS` 与表单 → `webui-frontend/tests/fixtures.js`。
  少改一处就会出现「后端认、界面不认」的静默不一致。
- 改 `/api/status`、`/api/desktop/status` 等响应字段：同步 `webui-frontend/tests/fixtures.js` 的夹具，
  否则前端断言与后端字段脱节，测试仍会绿。
- 改 OneBot 事件字段或协议行为：改 `flandora/ob_protocol.py`，并跑 `tests/test_ob_server.py`；动通知事件或心跳时另跑 `tests/test_ob_notice.py`、`tests/test_ob_heartbeat.py`、`tests/test_ob_network.py`（后两者起真端口，meta_event 会混进会话，等事件要跳过它）。
  `ob_protocol.py` 用 `hasattr(bi, "_resolve_names")` 调 `bridge_core` 的方法，改名不会报错，
  只会让群历史里的发言人静默退化成 wxid。
- 改前端：必须 `cd webui-frontend; npm run build`，因为后端只托管 `flandora/webui/dist`，
  而 `build.py` 见该目录已有 `index.html` 就跳过构建，不重建就会把旧界面打进安装包。
- 改窗口或桌面行为：只动 `flandora/wx_desktop.py`，跑 `tests/test_wx_desktop.py`，再重启桥接实测；
  真实窗口绑定只能由桥接进程自己做，其它进程会拿到「事件无法调用任何订阅者」。
- 改依赖：`pip install` 后同步 `flandora/requirements.txt` 里的版本说明；前端锁文件
  `webui-frontend/package-lock.json` 与 `package.json` 一起提交。

## 禁止操作

- 禁止改 `flandora/wxkey/store.py` 的 `ENTROPY`，也禁止改 `data/` 下密钥与口令的文件名。
  原因：它们参与 DPAPI 派生，属于落盘格式，改动会让所有既有安装的数据库口令与图片密钥失效，
  用户必须重新取钥。确要改格式先提升 `FORMAT_VERSION` 并写迁移。
- 禁止在桥接里决定「要不要回」（@ 过滤、拼上下文、表情学习、图片描述都别做）。原因：那属于 OneBot 客户端职责，越界会让客户端收不到群里的消息。
  群消息一律全量上报，用 `is_mentioned` 如实标记是否被 @。
- 禁止静默结束用户自己桌面上的微信。原因：那可能是用户正在用的账号。挡住隔离桌面时，
  只由用户在控制台点「结束桌面上的微信」（`POST /api/desktop/quit-default-wechat`）。
- 禁止手改 `flandora/webui/dist/`。原因：那是 vite 生成物且不入库；改源码后重新构建。
- 禁止给日志与控制台输出加 emoji。原因：Windows 控制台默认 GBK，`build.py` 曾因此打包成功
  却以退出码 1 结束。

## 验收标准

改动完成 = 下列全部通过：

1. `.venv\Scripts\python.exe -m compileall -q flandora manager sdk mcp`
2. `.venv\Scripts\python.exe -m ruff check .` 与 `.venv\Scripts\python.exe -m ruff format --check .`
3. `.venv\Scripts\python.exe -m pytest tests -q`
4. 改了 `webui-frontend/` 时追加：`cd webui-frontend; npm test` 与 `npm run build`

`.github/workflows/ci.yml` 在 push 到 `main` 与 PR 上跑同一组命令（另加前端 job），
本地先跑绿能省一轮往返。真实环境验证需要 Windows 桌面与已登录的微信，
涉及桌面/发送/取钥的改动要另外重启桥接看日志，测试通过不能替代这一步。

## 提交与协作

- 提交信息用中文，前缀 `feat:` / `fix:` / `docs:` / `refactor:`；破坏性变更写 `feat!:` 或 `fix!:`
  并在 body 逐条列出破坏点（改了哪个配置键、哪个文件名、哪个行为）。
- 仓库不维护 CHANGELOG，变更说明写在 commit body。
- 直接提交到 `main`。

## 待办与未实现

- 语音与音频本体：现在只把语音当作 `[语音]` 占位文本上报，客户端拿不到音频文件。要走通至少三处
  改动：确认微信 4.x 把语音落在哪个目录（本机 `msg/attach/<会话>/<月>/` 下只有 `Img`，全盘
  `*.silk`/`*.amr`/`*.mp3` 计数为 0）、给 `flandora/wx_store.py` 加语音文件定位（现只有
  `extract_image()` 在写死的 `Img` 目录里 glob）、给 `flandora/wx_image.py` 的解码链路加音频魔数
  与容器处理（`_MAGICS` 只认 jpg/png/gif/webp，`_sniff` 认不出即返回空）。文件与视频同理。
- 语音转文字（ASR）已移除，不属于桥接职责：转写与回复策略归 OneBot v11 客户端。

## 已知风险区

| 路径 | 风险 | 改动前后置动作 |
|---|---|---|
| `flandora/wx_desktop.py` | 曾在全系统扫 `Weixin.exe` 进程，把本程序留在沙箱里的实例误判成用户自己开的微信，导致重启后画面 404、发送绑不上窗口 | 跑 `tests/test_wx_desktop.py`；重启后确认日志出现「已接管隔离桌面上正在运行的微信」 |
| `flandora/uia_sender.py` | 输入框有清不掉的残留时会取消发送，避免把旧草稿当新消息发出去 | 跑 `tests/test_clear_input.py`；失败原因在 `sender._last_error` |
| `flandora/bridge_core.py` | 群消息语义（全量上报、`is_mentioned`、群内按发言人分 user_id）是设计约定 | 跑 `tests/test_group_passthrough.py` |
| `flandora/wxkey/store.py` | DPAPI 额外熵参与派生，改动即作废已存口令 | 见「禁止操作」第一条 |
| `webui-frontend/src/pages/Wizard.vue` | 2 秒轮询曾把免责声明勾选冲回 false，第一步永远无法完成 | 跑 Playwright 用例 `wizard keeps the disclaimer tick across status polling` |
| `flandora/webui/dist` 与 `build.py` | `ensure_webui_dist()` 见 `index.html` 已存在就跳过构建，容易把旧界面打进安装包 | 打包前手动 `npm run build` |

## 出错怎么办

| 症状（报错里的可检索片段） | 处理 |
|---|---|
| `IndentationError` / `unexpected indent` | 编辑时删掉了行尾换行，把 `def` 与 docstring 并成一行：先 `compileall` 定位再修 |
| `隔离桌面启动失败：默认桌面上已有微信在运行` | 退出自己桌面的微信，或在控制台点「结束桌面上的微信」 |
| `找不到微信可执行文件` | 在 `data/config.json` 把 `wechat_exe` 填成 `Weixin.exe` 完整路径；`/api/desktop/status` 的 `exe_found` 应为 `true` |
| `前端尚未构建` / 面板 503 | `cd webui-frontend; npm install; npm run build` |
| `输入框有清不掉的残留` | 在「微信画面」里手动清空输入框再重试；根因看日志里的残留文本 |
| `事件无法调用任何订阅者` | 窗口绑定只能由桥接进程做，别用独立脚本试绑沙箱窗口，改为重启桥接后验证 |

## 维护

本文件与代码同一个提交更新。改动以下内容时必须同步本文件：验收命令、配置项清单、
端口默认值、依赖版本下限、新增的禁止操作。发现本文件与代码不符时，先改本文件再继续改代码。
