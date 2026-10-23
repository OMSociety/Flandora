# Flandora

把微信个人号接进任意 OneBot v11 客户端：收微信消息、以本人身份回复，对外表现为一个标准的 OneBot v11 实现。

本仓库是 Windows 侧的桥接服务，负责与微信打交道。它不绑定特定机器人框架——MaiBot、AstrBot
或自己写的脚本都可以连上来；以 [MaiBot](https://github.com/MaiM-with-u/MaiBot)（麦麦）为例，
其微信适配器在另一个仓库 [MaiBot WeChat Adapter](https://github.com/OMSociety/mai-bot-wechat-adapter)。

> **提示：** 本程序通过自动化方式驱动你自己的微信客户端收发消息。这类用法可能与微信的用户协议冲突，账号存在被限制的风险。请只用自己的号，并自行评估。完整的合规与风险分析写在适配器仓库的 README 里。

## 目录

- [这是什么](#这是什么)
- [环境要求](#环境要求)
- [安装](#安装)
- [快速开始](#快速开始)
- [配置详解](#配置详解)
- [界面说明](#界面说明)
- [使用注意事项](#使用注意事项)
- [常见问题](#常见问题)
- [已知限制](#已知限制)
- [架构](#架构)
- [开发者](#开发者)
- [来源与致谢](#来源与致谢)
- [许可证](#许可证)

## 这是什么

微信从 4.x 起把聊天记录存在本机的 SQLCipher 加密数据库里。Flandora 的做法是：

- **收件**：自带取钥能力，在微信登录的那一刻截获数据库口令，把本机数据库解密成标准 SQLite
  副本后直接读取。不依赖任何第三方插件，也不走屏幕识别。
- **发送**：用 UI 自动化操作真实的微信窗口投递文本、图片与文件，对微信而言与用户自己操作
  没有区别。
- **对外**：起一个 OneBot v11 正向 WebSocket 服务端（默认 `ws://127.0.0.1:7999`），麦麦的
  微信适配器连上来，就是一个普通的 OneBot 连接。
- **界面**：桌面托盘管理器管启停与自启，浏览器控制面板管配置、实况画面与取钥向导。

它与 QQ 通道互不干扰，可以同时挂在同一个麦麦上。

## 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（64 位）。隔离桌面模式依赖 Windows 桌面 API |
| 微信 | 微信 4.x PC 版，需已在本机登录过一次 |
| Python | 仅源码运行需要，3.13（3.10 及以上可用） |
| 麦麦 | MaiBot 1.3.x，以及配套的微信适配器插件 |
| Node.js | 仅从源码打包时需要（构建 WebUI 前端） |
| 网络 | 全部组件都在本机，不需要外网。只有「检查新版本」与图片描述、语音转写会出网 |

本程序**跟着微信版本走**：取钥与解密不写死任何偏移，而是在运行期于内存中结构化定位。
微信升级后一般无需更新本程序。当前实测的版本号见[已知限制](#已知限制)。

## 安装

两种成品形态，任选一种；都不需要管理员权限，也都不需要装 Python。

### 方式一：安装程序（推荐）

到 [Releases](https://github.com/OMSociety/Flandora/releases) 下载
`Flandora-v1.0.0-setup.exe`，双击安装。安装目录默认在
`%LOCALAPPDATA%\Programs\Flandora`，安装过程中可以勾选开机自启与桌面快捷方式。

本程序没有购买代码签名证书，Windows SmartScreen 会拦一下：点「更多信息」→
「仍要运行」即可。卸载时程序会单独问一次要不要删掉配置与已保存的微信口令，默认删除。

### 方式二：便携包（portable zip）

下载 `Flandora-v1.0.0.zip`，解压到任意目录。

```
Flandora-v1.0.0/
├── Flandora.exe            ← 桥接服务（管理器自动调用）
├── Flandora-Manager.exe    ← 双击启动桌面管理器
├── Flandora-LogTail.exe    ← 日志跟随器（管理器自动调用）
├── Flandora-KeyTool.exe    ← 取钥器（向导自动调用，平时用不到）
├── 启动.bat                   ← 等价于双击 Flandora-Manager.exe
├── data/
│   └── config.json            ← 配置文件（首次需手动填入）
├── docs/
├── README.md
└── LICENSE
```

1. 编辑 `data/config.json`，见[配置详解](#配置详解)。最少要填 `bot_nicknames`。
2. 双击 `启动.bat`，或直接运行 `Flandora-Manager.exe`。

> **提示：** 建议放在不含中文和空格的目录（如 `D:\Flandora`），能少踩一些编码与权限的坑。
> 四个 exe 必须放在同一目录，管理器靠同目录下的主程序与日志跟随器工作。
> `data/` 是可写区：配置、日志、进程号、微信口令与图片密钥都在里面。想搬到别的目录，整个
> 文件夹移走即可。

### 方式三：从源码运行（开发者）

```bat
git clone https://github.com/OMSociety/Flandora.git
cd Flandora
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r flandora\requirements.txt
.venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r manager\requirements.txt
mkdir data
copy flandora\config.example.json data\config.json
.venv\Scripts\python run_bridge.py
```

源码模式的可写数据和打包后一样落在仓库根的 `data\`（该目录不入版本控制）。

## 快速开始

### 第 1 步：准备微信

用你打算交给机器人的那个微信号，在本机正常登录一次微信，确认能收发消息。

如果你打算用默认的**隔离桌面**模式（推荐），**先把微信彻底退出**：右键任务栏图标退出，
并确认任务管理器里没有 `Weixin.exe`。隔离桌面模式下微信由本程序启动；Windows 上微信是
单实例的，你自己先开了微信的话，隔离实例只会停在登录页。

### 第 2 步：配置 data/config.json

```jsonc
{
    "bot_nicknames": ["你的微信昵称"],
    "ob_server_port": 7999,
    "ob_server_token": ""
}
```

完整字段说明见[配置详解](#配置详解)，每一项在 `config.json` 里都带 `_comment` 注释。

### 第 3 步：首次取钥

微信的数据库口令只在**登录那一瞬间**存在，过后就查不到了。所以取钥必须配合一次重新登录。
WebUI 左边栏有「取钥向导」一页，把这件必须掐着时间做的事拆成了四步：

1. **免责声明**：确认这是你自己的账号并勾选同意。
2. **数据库口令**：点「开始取钥」，向导拉起 `Flandora-KeyTool.exe`。它挂好钩子后会提示你
   现在登录微信；在微信窗口里完成登录即可。隔离桌面模式下微信不在你的屏幕上，登录界面要在
   页面下方的实况画面里看，用手机扫码或确认。取到后口令用 Windows DPAPI 加密存到
   `data/wx_key.dat`。
3. **图片密钥**：想让机器人能看懂图片就继续这一步，只需要文本可以跳过。点「开始扫描」，然后
   在实况画面里**点开一张之前没看过的图片**，密钥会在解码的那几秒被截获。
4. **校验**：拿口令把账号下的每个库都过一遍，确认真的能解开。

**取钥只做这一次**，之后每次开机直接启动即可。换微信号、或口令失效时重复本步。

> **提示：** 口令用 Windows DPAPI 加密，只能在同一台机器的同一个 Windows 用户下解开。
> 把 `data/` 拷到别的机器或别的用户下会解不开，需要在那边重新取钥。

### 第 4 步：在麦麦里装微信适配器

按 [MaiBot WeChat Adapter](https://github.com/OMSociety/mai-bot-wechat-adapter) 的 README
安装插件，然后让它连到本程序：

```toml
[server]
host = "127.0.0.1"
port = 7999
token = ""            # 与 bridge 的 ob_server_token 填同一个值
```

### 第 5 步：启动

管理器里点「启动」。日志里出现 OneBot 服务端开始监听、适配器连上来（1 客户端），就通了。
给机器人发条消息试试。

只有文本通道需要数据库口令；**图片**还需要额外一步，见[图片](#图片)。

## 配置详解

配置文件是程序目录下的 `data/config.json`（源码模式即仓库根的 `data/config.json`）。改完需要重启
bridge 生效。下面按用途分组，只列需要你关心的项，其余字段见文件内注释。

### 核心必填

| 字段 | 说明 |
|---|---|
| `bot_nicknames` | 机器人微信昵称的列表。群里只有 @ 到这些昵称之一才会触发回复 |
| `bot_wxid` | 机器人自己的 wxid，可留空（程序从当前登录账号推断） |
| `ob_server_port` | OneBot v11 服务端端口，默认 7999 |
| `ob_server_token` | OneBot 握手鉴权 token，留空则不校验。默认只监听回环地址 |
| `self_name` | 机器人在麦麦侧显示的名字 |
| `astrbot_attachments` | 麦麦的 `attachments` 目录绝对路径 |

### 收件（读微信本地库）

| 字段 | 说明 |
|---|---|
| `wechat_files_root` | 微信数据根目录。留空 = 自动取「文档」目录下的 `xwechat_files`；文档库被重定向到别的盘也能正确解析 |
| `wx_key_file` | 口令文件路径。留空 = `data/wx_key.dat` |
| `wx_work_dir` | 解密副本与游标的存放目录。留空 = `data/wx_cache`。原始库始终只读 |
| `wx_poll_seconds` | 轮询间隔，默认 1.0 秒 |
| `wx_backfill_seconds` | 首次启动时额外收进多少秒内的历史消息。`0` = 只收启动之后的新消息，不会把旧聊天记录灌给机器人 |
| `buffer_seconds` | 同一联系人多久内的连续消息合并成一次上下文 |
| `context_messages` | 每次带上多少条历史消息作为上下文 |
| `group_reply_mode` | `mention`（默认，被 @ 才回）/ `all`（群里每条都回） |
| `group_name_map` | 群 ID 到群名的映射 |

### 发送

| 字段 | 说明 |
|---|---|
| `isolated_desktop` | **默认 `true`**。让微信跑在一张独立的 Windows 桌面上，向它投递输入时切换的前台是那张桌面的，你自己的桌面完全不受打扰，发送通道因此可以长期开着 |
| `isolated_desktop_name` | 隔离桌面的名字，默认 `WeBotSandbox`。只有要同时跑多份互不干扰的实例时才需要改 |
| `wechat_exe` | 微信主程序路径。留空 = 自动探测常见安装位置（含注册表登记的安装路径） |
| `wechat_hwnd` | 指定要驱动的微信窗口句柄，`0` = 自动取第一个。**一台机器上同时登录多个微信**时才需要手填，且微信重启后句柄会变，要重新绑定 |
| `allow_foreground_steal` | 仅在 `isolated_desktop` 为 `false` 时有意义。默认 `false`，此时发送功能整体不可用；设为 `true` 后发送可用，代价是每条消息发出时前台会短暂切到微信，发完立刻切回（实测切回耗时 3–11 毫秒） |

关于前台切换的成因，见[已知限制](#已知限制)。

### 图片

| 字段 | 说明 |
|---|---|
| `wx_image_key_file` | 图片密钥文件路径。留空 = `data/wx_image_key.json` |
| `image_caption_provider` | 收到图片时交给谁描述：`openai`（任何 OpenAI 兼容接口）/ `ollama`（本地）/ 留空 = 不描述，直接把图片交给麦麦 |
| `image_caption_model` | 视觉模型名 |
| `image_caption_api_base` | 兼容接口地址 |
| `image_caption_api_key` | 兼容接口密钥 |
| `image_caption_prompt` | 描述用的提示词 |
| `ollama_base_url` / `ollama_timeout` | 本地 Ollama 的地址与超时 |

图片密钥与数据库口令不是一回事：**AES 密钥只在微信解码图片的那几秒驻留内存**，所以要在
微信正在打开一张没看过的图时去抓。做法：

1. 管理器里点「启动」，让微信跑起来。
2. 在「取钥向导」里走完第 1、2 步（数据库口令已经到手）。
3. 第三步点「开始扫描」，然后按提示在实况画面里**点开一张你之前没看过的图片**
   （没被缓存过，才会触发解码）。
4. 扫到密钥后自动写入 `data/wx_image_key.json`。

密钥是账号级的：换号后要重新取。缺它时图片消息退回 `[图片]` 占位，文字通道不受影响。

### 语音转写（可选）

收到的语音消息本体是加密的 `.dat`，目前尚未还原，所以本程序**不会**尝试转写收到的语音，
只在日志里记一条占位。相关字段 `fun_asr_*` 保留着，等语音通道打通后可直接用。

发送方向不受影响：麦麦侧发出的 OneBot v11 `voice` 段会以「mp3 文件卡片」发到微信。

### 表情学习（可选）

| 字段 | 说明 |
|---|---|
| `emoji_learn_enabled` | 是否开启。开启后采集微信自带表情（`[捂脸]` 这类代码）的使用场景 |
| `emoji_learn_threshold` | 样本积累到多少条后，用 LLM 蒸馏成「表达方式」条目并写进麦麦的表达方式库 |
| `maibot_emoji_dir` | 麦麦的表情库目录 |
| `maibot_webui_url` | 麦麦 WebUI 地址，用于写入表达方式库 |

## 界面说明

### 桌面管理器（`Flandora-Manager.exe`）

托盘常驻，负责启动与停止 bridge、查看状态、开机自启、快速打开 WebUI 与日志。双击
`启动.bat` 就是打开它。管理器自身做了单实例锁，重复双击只会提示。

### WebUI 控制面板（`http://127.0.0.1:8766`）

- **仪表盘**：运行状态、收发计数、收件源是否就绪、当前微信窗口。
- **消息**：实时消息流。
- **日志**：与日志文件同源。
- **配置**：表单化编辑 `config.json`，保存后按需重启 bridge。
- **微信画面**：隔离桌面模式下微信的实况截图，可以在这里点鼠标完成登录或操作微信。
- **发送测试**：手动发一条消息验证发送通道。

## 使用注意事项

### 1. 端口

默认占用 7999（OneBot）与 8766（WebUI）。被占用时改 `ob_server_port` 与 `web_port`，
适配器那边同步改。

```bat
netstat -ano | findstr :7999
```

### 2. 隔离桌面模式下微信不在你的屏幕上

这是设计目的，不是故障。要看微信，用 WebUI 的「微信画面」。想让微信回到自己屏幕上，把
`isolated_desktop` 设为 `false`，然后自己启动微信（但要接受发送时的前台切换）。

### 3. 隔离桌面模式下首次登录要在 WebUI 里完成

隔离桌面的窗口你看不见，第一次登录得在 WebUI 的实况画面里操作，用手机扫码或确认。

### 4. `ob_server_token` 两端必须一致

bridge 的 `ob_server_token` 与适配器的 `server.token` 填同一个值。留空表示不校验鉴权，
此时服务端只监听 `127.0.0.1`。一端改了另一端没改，适配器会一直连不上。

### 5. `group_name_map` 为什么必要

微信的搜索框**搜不到以 `@chatroom` 结尾的群 ID**，所以发送前必须把群 ID 映射成群里的真实
群名。群 ID 可以在 WebUI 的消息流里看到。

### 6. 发送依赖 UI 自动化

本机显示缩放、系统主题或输入法状态异常时，UI 自动化可能定位不到控件；桌面弹窗（微信更新
提示、安全提示）也会打断发送。失败原因会写进日志。

### 7. 防火墙与局域网

麦麦和 bridge 不在同一台机器时，把 `ob_server_host` 改成可对外监听的地址、在防火墙放行
7999，并务必给 `ob_server_token` 填一串随机字符。都在本机就保持 `127.0.0.1`。

### 8. 日志编码

日志文件是 UTF-8。用 PowerShell 的 `Get-Content -Encoding UTF8` 或编辑器查看，不要用旧版
cmd 的 `type`（cp936 会乱码）。管理器已做编码修复，可以正常显示。

### 9. 一个微信号只能被一份实例驱动

Windows 上微信是单实例的。要跑多个微信号，用 `isolated_desktop_name` 给每份实例配一个不同
的桌面名，并用不同的 `ob_server_port`。

### 10. 别把配置提交进仓库

`config.json` 里有你的 token 与 API key，仓库里不含它。不要把真实的 `ob_server_token`、
`image_caption_api_key`、`fun_asr_api_key` 提交到 GitHub 或发到群里。

## 常见问题

**Q：启动时报「找不到 config.json」。**
把 `config.example.json` 复制为 `config.json` 放进程序目录下的 `data/`（源码模式即仓库根的 `data/`）。

**Q：启动管理器后日志窗口是空的？**
确认四个 exe 在同一目录、`config.json` 存在且没有报错，然后点一次「重启」。

**Q：启动成功但收不到消息。**
先看日志里收件源是否就绪。若提示口令缺失或账号不符，说明还没取钥，或者**换了微信号**——
口令与登录的微信号一一对应，换号后必须重新取钥。

**Q：能收不能发。**
默认的隔离桌面模式要求微信由本程序启动。若你把 `isolated_desktop` 设成了 `false`，发送还
需要 `allow_foreground_steal` 为 `true`。

**Q：图片显示成 `[图片]` 占位。**
缺少图片密钥，见[图片](#图片)。

**Q：换了台机器，口令解不开。**
DPAPI 密文绑机器与 Windows 用户，属于预期行为，在新机器上重新取钥。

**Q：微信升级后还能用吗。**
一般能。取钥与解密都是运行期定位，不写死偏移。

## 已知限制

照实列在这里，细节与验证过程见 `docs/`：

- **语音、文件、视频消息拿不到本体。** 这几种在磁盘上同样是加密的 `.dat` 封装，目前只还原
  了图片那一种。收到语音只记一条占位日志。
- **取钥需要一次重新登录。** 口令只在登录瞬间存在，等不来，这是微信的设计。
- **图片密钥需要一张没被缓存过的图。** 密钥只为正在解码的图片短暂驻留内存。
- **隔离桌面上登录仍需手机确认。** 扫码或手机端确认这一步绕不过去。
- **发送必然触发微信自身的窗口消息处理。** 我们已逐项实测否证了十几种「不惊动它」的投递
  方式（PostMessage、SendMessageTimeout、UIA SetValue、SetFocus、Invoke、SetActiveWindow、
  完全附着线程等），任何向微信投递的输入都会让它升到前台，这是微信自身的行为。隔离桌面是
  绕开它的办法，不是修好了它。
- **未在全新安装的 Windows 上验证过。** 开发与测试都在一台已经装了很多东西的机器上完成。

实测过的微信版本、否证清单与实验数据记录在 `docs/known-limits-dat-format.md` 与
`docs/known-limits-desktop.md`。

## 架构

```
微信 PC 客户端
   |  收件：解密本机 SQLCipher 库（口令由取钥器在登录时截获）
   |  发送：UI 自动化投递输入（默认在隔离桌面上）
   v
Flandora
   |- 取钥器 Flandora-KeyTool.exe（独立进程，只在取钥时运行）
   |- OneBot v11 正向 WebSocket 服务端  ws://127.0.0.1:7999
   \- WebUI  http://127.0.0.1:8766
   ^
   |  OneBot v11
   v
MaiBot WeChat Adapter（麦麦插件）
   v
MaiBot（麦麦）
```

## 开发者

### 目录结构

| 路径 | 内容 |
|---|---|
| `run_bridge.py` | 入口脚本。放在仓库根，`flandora` 包才会被完整收集 |
| `flandora/` | 主包：配置、收件源、发送器、协议、图片解码、WebUI 后端 |
| `flandora/wxkey/` | 取钥链路：定位、截获、还原 |
| `flandora/webui/` | WebUI 后端与前端静态产物 |
| `webui-frontend/` | WebUI 前端的 vite 工程 |
| `manager/` | 桌面托盘管理器与日志查看器 |
| `tools/` | 独立小工具：取钥器、取图片密钥 |
| `tests/` | 单测，不依赖真实微信 |
| `docs/` | 已知限制与验证记录、第三方许可 |
| `installer/` | Inno Setup 安装脚本 |
| `build.py` | 一键打包 |

### 打包

```bat
.venv\Scripts\python build.py
```

产物在 `dist\` 下：`Flandora-v1.0.0.zip`（便携包）与
`Flandora-v1.0.0-setup.exe`（安装程序）。打包前会自动构建 WebUI 前端，所以需要 Node.js 与
npm；安装程序由 [Inno Setup](https://jrsoftware.org/isdl.php) 编译，没装就只出便携包
（也可以用 `INNO_SETUP_DIR` 或 `ISCC` 环境变量指定 `ISCC.exe` 的位置）。
取钥器单独打包，因为 frida 有几十 MB，不塞进主程序。

### 跑测试

```bat
.venv\Scripts\python tests\test_wx_image.py
```

`tests/` 下的脚本都可直接执行，测试数据现造，不读真实微信数据。

## 来源与致谢

- [jessiongod/wechat-onebot-bridge](https://github.com/jessiongod/wechat-onebot-bridge)：
  本仓库的前身与上游，收件、发送与 OneBot 协议层的早期设计来自这里。
- [jessiongod/mai-bot-wechat-adapter](https://github.com/jessiongod/mai-bot-wechat-adapter)：
  麦麦侧微信适配器的前身。
- 顾海蔚蓝：早期结构与文档。

本仓库在上述工作基础上重做了取钥、收件与发送链路，并按 MIT 许可证继续分发。

## 许可证

MIT，见 [LICENSE](LICENSE)。发布包内 `docs/third-party-licenses/` 收录了随包分发的第三方
组件的许可证原文。
