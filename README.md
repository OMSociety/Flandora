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
| 发送 | 文本、图片、文件与语音（mp3 卡片）；默认在隔离桌面投递；发送前清空输入框并核对回显 |
| OneBot v11 | 正向 WebSocket 服务端；消息段构造与事件上报；`is_mentioned` 如实标记是否被 @ |
| 群与身份 | 群内按发言人拆分 `user_id`；群名映射；多昵称识别 |
| 取钥 | 四步向导：免责声明、数据库口令、图片密钥、校验；口令与密钥由 Windows DPAPI 加密落盘 |
| 隔离桌面 | 微信跑在独立的 Windows 桌面上；重启后接管仍在运行的实例；需要时由你点button结束 |
| 控制台 | 仪表盘、消息记录、运行日志、桥接配置、微信画面、取钥向导、发送测试；明暗两套主题 |

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

## 控制台

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

### OneBot 服务端

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `ob_server_host` | 字符串 | `127.0.0.1` | 监听地址，都在本机就保持回环 |
| `ob_server_port` | 整数 | `7999` | 正向 WebSocket 端口 |
| `ob_server_token` | 字符串 | 空 | 握手鉴权 token，留空不校验 |
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
| `data/wx_cache/` | 解密副本与游标，可随时删 |
| `data/flandora.log` | 运行日志（UTF-8） |

## 已知限制

- **语音、文件、视频拿不到本体**：这几种在磁盘上同样是加密的 `.dat` 封装，目前只还原了图片那一种；收到语音按 `[语音]` 占位文本上报，客户端据此知道有人发过语音，拿不到音频本体。发送方向不受影响。
- **取钥需要一次重新登录**：口令只在登录瞬间存在，等不来，这是微信的设计。
- **图片密钥需要一张没被缓存过的图**：密钥只为正在解码的图片短暂驻留内存。
- **隔离桌面上登录仍需手机确认**：扫码或手机端确认绕不过去。
- **发送必然触发微信自身的窗口消息处理**：已逐项实测否证十几种「不惊动它」的投递方式（PostMessage、SendMessageTimeout、UIA SetValue、SetFocus、Invoke、SetActiveWindow、完全附着线程等），任何向微信投递的输入都会让它升到前台，这是微信自身行为；隔离桌面是绕开它的办法，不是修好了它。
- **一个微信号只能被一份实例驱动**：要跑多个号，用 `isolated_desktop_name` 给每份实例配不同桌面名，并用不同的 `ob_server_port`。
- **未在全新安装的 Windows 上验证过**：开发与测试都在一台已经装了很多东西的机器上完成。

实验过程与数据见 [`docs/known-limits-dat-format.md`](docs/known-limits-dat-format.md) 与 [`docs/known-limits-desktop.md`](docs/known-limits-desktop.md)。

## 开发

需要 Python 3.13 与 Node.js（构建前端时）。以下命令在仓库根执行：

```powershell
# 装依赖
.venv\Scripts\python.exe -m pip install -r flandora\requirements.txt
.venv\Scripts\python.exe -m pip install -r manager\requirements.txt

# 语法检查与后端测试
.venv\Scripts\python.exe -m compileall -q flandora
.venv\Scripts\python.exe -m pytest tests -q

# 从源码起桥接
.venv\Scripts\python.exe run_bridge.py
```

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
