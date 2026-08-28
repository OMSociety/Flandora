# WeChatOneBotBridge

> **微信 ↔ OneBot v11 桥接**，让 MaiBot 直接接管微信收发消息。
> 本项目是 [Akasha-WeChat](https://github.com/) 的衍生分支，改为对接 MaiBot 的 napcat-adapter（OneBot v11 WS 服务端），并附桌面管理器。

---

## 📑 目录

- [✨ 功能特性](#-功能特性)
- [📦 安装](#-安装)
- [🚀 快速开始](#-快速开始)
- [⚙️ 配置详解](#️-配置详解)
- [🖼️ 界面说明](#️-界面说明)
- [⚠️ 使用注意事项（重要）](#️-使用注意事项重要)
- [❓ 常见问题 FAQ](#-常见问题-faq)
- [🏗️ 架构](#️-架构)
- [🛠️ 开发者](#️-开发者)
- [📜 协议与致谢](#-协议与致谢)

---

## ✨ 功能特性

- 📡 **双向桥接**：微信消息 → MaiBot、MaiBot 回复 → 微信
- 🎙️ **UIA 自动化**：基于 uiautomation 的原生键盘模拟发消息，零 hook、零注入
- 🔌 **OneBot v11 标准**：MaiBot 通过 napcat-adapter 直连（WS Server on `0.0.0.0:7999`）
- 🌐 **WebUI 控制面板**：`http://127.0.0.1:8766` 看实时消息 / 改窗口绑定 / 发测试消息
- 🎨 **桌面管理器**（QQ 风格）：实时日志、端口状态、一键启停、最小化到托盘
- 🔁 **自动领养**：管理器启动时若发现已运行的 bridge，自动接管（不重复启动）
- ⚡ **零卡顿拖拽**：主线程零 PowerShell 阻塞，3s 一次的轻量后台采集
- 🚀 **打包即用**：双击 `启动.bat` 启动管理器，无需 Python 环境（见 Releases 的 zip 包）

---

## 📦 安装

### 方式一：下载预编译 zip（推荐）

从 [Releases](https://github.com/jessiongod/wechat-onebot-bridge/releases) 下载最新版，解压到**任意目录**即可，**无需 Python 环境**。

> ⚠️ **解压路径**：建议放在不含中文和空格的目录（如 `D:\WeChatBridge`）。虽然代码已做 UTF-8 处理，但放在纯英文路径能避免一些潜在的编码/权限问题。

```
WeChatOneBotBridge/
├── MaiBotBridgeManager.exe   ← 双击启动桌面管理器
├── bridge.exe                ← 桥接服务（管理器自动调用）
├── log_tail.exe              ← 日志跟随器（管理器自动调用）
├── config.json               ← 配置文件（首次需手动填入）
├── 启动.bat                  ← 等价于双击 MaiBotBridgeManager.exe
├── README.md
└── LICENSE
```

> ⚠️ **三个 exe 必须放在同一目录**。管理器靠同目录下的 `bridge.exe` / `log_tail.exe` 工作，别拆开。

### 方式二：从源码运行（开发者）

需要 Python 3.10+：

```bash
git clone https://github.com/jessiongod/wechat-onebot-bridge.git
cd wechat-onebot-bridge
pip install -r bridge/requirements.txt
pip install pystray Pillow        # 桌面管理器托盘用
python manager/bridge_manager.py  # 启动桌面管理器
```

---

## 🚀 快速开始

### 第 1 步：准备 WeFlow

[WeFlow](https://weflow.top) 是一个开源的微信桌面 hook 工具，通过 SSE 协议把微信消息推送出去。

- 启动后访问 `http://127.0.0.1:5031`
- 在 WeFlow 设置里拿到 `access_token`
- 记得在 WeFlow 里开启 API 服务（默认端口 5031）

> 如果 WeFlow 端口不是 5031，改 `config.json` 里的 `weflow_base_url`。

### 第 2 步：启动 MaiBot（napcat-adapter 模式）

MaiBot 社区已有 WeChat相关 插件，让它作为 **WS 客户端**连到本 bridge（不是 server）。

- 默认连入地址：`ws://127.0.0.1:7999`
- 鉴权 token：`config.json` 里的 `ob_server_token`（两端必须一致）

### 第 3 步：配置 config.json

把 zip 里的 `config.json`（或源码的 `bridge/config.example.json` 复制为 `config.json`），填入你的真实值：

```jsonc
{
    "access_token": "你的 WeFlow access_token",
    "ob_server_token": "自己生成一个随机字符串（MaiBot napcat-adapter 也填这个）",
    "bot_nicknames": ["机器人微信昵称"],
    "bot_wxid": "机器人自己的 wxid",
    "astrbot_attachments": "C:\\path\\to\\maibot\\attachments",
    "image_caption_api_key": "OpenAI 兼容 API key（可选，图片描述）",
    "fun_asr_api_key": "阿里云百炼 API key（可选，语音转写）"
}
```

保存后重启管理器（或点管理器上的「重启」）。

### 第 4 步：启动

- **桌面管理器（推荐）**：双击 `启动.bat` 或 `MaiBotBridgeManager.exe`
- **纯命令行**：在 `bridge/` 目录跑 `python main.py`（或打包的 `bridge.exe`）

管理器启动后会自动接管任何已在运行的 bridge。状态显示「运行中 PID=xxx（外部，已接管）」即成功。

---

## ⚙️ 配置详解

所有配置都在 `bridge/config.json`（打包版直接在根目录）。改完**重启 bridge** 生效。

### 核心必填

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | :---: | --- |
| `weflow_base_url` | 字符串 | ✅ | WeFlow API 地址，默认 `http://127.0.0.1:5031` |
| `access_token` | 字符串 | ✅ | WeFlow 的鉴权 token |
| `bot_nicknames` | 数组 | ✅ | 机器人在微信里的所有昵称（可多个），用于 @ 触发识别 |
| `ob_server_token` | 字符串 | ✅ | MaiBot 连入的鉴权 token，**两端必须一致** |
| `astrbot_attachments` | 字符串 | ✅ | MaiBot 的 attachments 目录绝对路径 |

### 常用进阶

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `ob_server_host` | `127.0.0.1` | OB11 WS 服务监听地址。`0.0.0.0` 允许局域网其他设备连入 |
| `ob_server_port` | `7999` | OB11 WS 服务端口。**改端口时 MaiBot 端也要改** |
| `bot_wxid` | `""` | 机器人自己的 wxid（防自回复，可留空） |
| `self_name` | `wechat-bot` | 机器人显示名 |
| `wechat_hwnd` | `0` | 多开微信时指定机器人所在窗口句柄，`0` = 自动取第一个微信窗口 |
| `group_reply_mode` | `mention` | `mention`（仅 @ 触发）/ `all`（群里所有消息都回复） |
| `context_messages` | `5` | @ 触发时附带的最近群消息数量（0 = 关闭） |
| `buffer_seconds` | `9` | 消息缓冲窗口时间，多条合并后再推，避免麦麦响应太碎 |
| `web_port` | `8766` | WebUI 面板端口（网页入口） |
| `webui_auto_open` | `true` | 启动 bridge 时自动打开浏览器到 WebUI |
| `group_name_map` | `{}` | 群 ID → 群名 映射（见下方重点说明） |
| `maibot_emoji_dir` | `""` | MaiBot 表情库目录（可选） |

### 图片描述（视觉模型识别图片内容）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `image_caption_provider` | `openai` | `openai`（OpenAI 兼容）/ `ollama`（本地视觉模型） |
| `image_caption_model` | `gpt-4o-mini` | 视觉模型名，例如 `gpt-4o-mini`、`llava:7b`、`deepseek-v4-flash-vision-exp` |
| `image_caption_api_key` | `""` | OpenAI 兼容 API key（provider=openai 时必填） |
| `image_caption_api_base` | `https://api.deepseek.com/v1` | OpenAI 兼容 API 的 base 地址 |
| `image_caption_prompt` | `请用中文简短描述这张图片的内容` | 描述提示词 |

> provider 选 `ollama` 时用 `ollama_base_url`（默认 `http://127.0.0.1:61000`）。

### 语音转写（DashScope Fun-ASR，可选）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `fun_asr_workspace_id` | `""` | 阿里云百炼业务空间 ID，留空则关闭语音转写 |
| `fun_asr_region` | `cn` | `cn`（北京）/ `intl`（新加坡） |
| `fun_asr_api_key` | `""` | 阿里云百炼 API key |
| `fun_asr_model` | `fun-asr-flash-2026-06-15` | 转写模型 |

### 表情学习（可选）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `emoji_learn_enabled` | `true` | 观察群友表情 → 蒸馏成表达方式喂给麦麦 |
| `emoji_learn_threshold` | `15` | 积累多少条样本触发一次蒸馏 |
| `maibot_webui_url` | `http://127.0.0.1:8001` | 麦麦 WebUI 地址（投喂表达方式用） |

---

## 🖼️ 界面说明

### 桌面管理器

- **状态栏**：显示「运行中 PID=xxx（自管/外部）」——`外部，已接管` 表示 bridge 是别的进程启动的，管理器直接接管控制。
- **运行信息**：PID、端口 7999 客户端数、WeFlow 健康、微信进程数。
- **bridge 日志（实时）**：跟随 `bridge.log` 滚动，管理器窗口最小化到托盘也会继续采集。
- **按钮**：`▶ 启动` / `■ 停止` / `↻ 重启`。
- **最小化到托盘**：关掉主窗口 = 最小化到托盘，bridge 仍在后台。右键托盘图标可「彻底退出」。
- **开机自启动**：勾选即写入 Windows 启动项。

### WebUI 控制面板（http://127.0.0.1:8766）

- **仪表盘**：24 小时收发消息量、连接状态。
- **消息**：实时消息流。
- **日志**：运行日志。
- **配置**：在线改 config.json（保存后重启生效）。
- **发送测试**：手动给某个联系人/群发一条测试消息。
- **窗口绑定**：多开微信时，在这里指定机器人所在窗口句柄（`wechat_hwnd`）。

---

## ⚠️ 使用注意事项（重要）

### 1. 端口冲突

bridge 占用 3 个端口：`5031`（连 WeFlow）、`7999`（OB11 WS 服务端）、`8766`（WebUI）。它们必须**空闲**，否则启动失败。

- 占用检查：
  ```bash
  netstat -ano | findstr :7999
  ```
- 若 7999 被其他程序占用，改 `ob_server_port`，同时改 MaiBot napcat-adapter 连入端口。

### 2. wechat_hwnd（微信窗口句柄）——最容易踩的坑

当电脑上**同时登录多个微信**时，机器人必须绑定到正确那个微信窗口。

- 微信**重启后窗口句柄会变**，需要重新绑定。
- `wechat_hwnd = 0` 时自动取第一个微信窗口。如果取错，发送消息会发到别的微信。
- **多开建议**：给每个机器人一份独立配置，用不同的 `ob_server_port` 和 `wechat_hwnd`。

### 3. 群 ID → 群名映射 group_name_map

微信搜索框**无法搜到以 `@chatroom` 结尾的群 ID**，所以你需要把"群 ID"映射成"群里显示的真实名字"，bridge 才能用群名去搜索并发送。

```jsonc
"group_name_map": {
    "45850044528@chatroom": "BOT TEST",
    "47795211464@chatroom": "工程群"
}
```

> 群 ID 可以在微信-群聊设置里查看，或用 WebUI 的消息流看到。

### 4. ob_server_token 两端必须一致

- 这个 token 是 MaiBot 连入本 bridge 的**握手鉴权**。
- 一端改了另一端没改，MaiBot 会**一直连不上**（表现：MaiBot 侧报 token 无效 / 连接失败，bridge.log 里没有 MaiBot 连入记录）。

### 5. 发送方式依赖 UIA 自动化

- bridge 用 uiautomation 模拟键盘来发微信消息，**微信窗口必须保持在前台/可被激活**（别最小化到托盘让微信隐身）。
- 需要保证你运行该程序的账号能访问到微信窗口（通常以登录微信的同一个 Windows 账号运行）。
- 这条路径对**输入法、弹窗**有一定要求——如果有桌面弹窗（如微信更新提示、安全弹窗）会打断发送。

### 6. 防火墙 / 局域网

- 如果 MaiBot 和 bridge 在**不同机器**，需要：
  1. 把 `ob_server_host` 改成 `0.0.0.0`
  2. 在防火墙放行 `7999` 端口
- 如果都在本机，保持 `127.0.0.1` 即可。

### 7. 编码问题

- `bridge.log` 是 **UTF-8** 编码。用记事本/编辑器打开没问题。
- **不要用旧版 cmd（cp936）的 `type` 直接看**，会乱码。用 `Get-Content -Encoding UTF8`（PowerShell）或 vscode 打开。
- 管理器能正确显示 UTF-8 日志（已做编码修复）。

### 8. 多实例

- 桌面管理器已做**单实例锁**：再双击一次只会弹提示，不会开第二个。
- bridge 本身可多开（不同 `ob_server_port`），但一个端口只能一个 bridge。

### 9. 先启动顺序

- 建议顺序：**WeFlow → bridge（/管理器） → MaiBot adapters**。
- bridge 启动时会自动拉起 WebUI；MaiBot 侧 adapter 连接是异步的，稍等几秒连接就会建立。
- 若 MaiBot 先于 bridge 启动并连不上，bridge 启动后会接受它的重连，无需重启 MaiBot。

### 10. 安全提醒（公开仓库）

- **仓库里不含 `config.json`**（含密钥）。你 clone 后自己从 `config.example.json` 复制并填真实值。
- 不要把真实的 `access_token` / `fun_asr_api_key` / `image_caption_api_key` 提交到 GitHub（会泄露），也不要发到群里。

---

## ❓ 常见问题 FAQ

**Q: 启动管理器后日志窗口是空的？**
A: 检查三点：① `bridge.exe` 是否和管理器在同一目录；② `config.json` 是否存在且没有报错；③ 先让 bridge 真正跑起来（点「重启」）。

**Q: MaiBot 收不到微信消息？**
A: 依次排查：
1. WeFlow 是否正常推送（WebUI 消息流有没有新消息）
2. `ob_server_token` 两端是否一致
3. 用浏览器打开 `http://127.0.0.1:7999`，能收到 OB11 协议说明说明服务正常
4. MaiBot napcat-adapter 是否能连上 `ws://127.0.0.1:7999`

**Q: 发送消息失败 / 发到错误的微信？**
A: 检查 `wechat_hwnd` 是否正确绑定机器人窗口；微信窗口是否在前台可激活；是否有弹窗打断。

**Q: 一开管理就跑很多窗口？**
A: 那是早期版本的 bug，新版已修复（所有 PowerShell 子进程加了 `CREATE_NO_WINDOW`）。若仍有，检查是否运行了旧版 exe。

**Q: 拖拽窗口卡顿？**
A: 主线程已无 PowerShell 阻塞。若仍卡，通常是机器整体负载高，或显卡驱动导致 Tk 渲染慢（更新显卡驱动）。

**Q: 开机自启动没生效？**
A: 用管理员身份运行一次，勾选"开机自启动"，确认没有被杀毒软件拦截。

**Q: 微信重启后 bot 不说话了？**
A: 微信重启 → `wechat_hwnd` 变 → 去 WebUI「窗口绑定」重新绑定，然后重启 bridge。

**Q: 图片识别没反应？**
A: 确认 `image_caption_provider` 对应的 API key 有效（`openai` 填 `image_caption_api_key`，`ollama` 填 `ollama_base_url`），且网络可达。

**Q: 语音消息无法转写？**
A: 语音转写依赖 DashScope Fun-ASR，需填 `fun_asr_workspace_id` + `fun_asr_api_key` 且模型可用。

---

## 🏗️ 架构

```
┌────────────┐    SSE (5031)     ┌──────────────┐    WS (7999)    ┌────────────┐
│   WeFlow   │ ────────────────→ │    bridge    │ ←─────────────── │   MaiBot   │
│  (微信hook) │  ←─────────────── │  (本项目)    │ ───────────────→│napcat-     │
└────────────┘    UIA 发送       └──────┬───────┘    OneBot v11    │ adapter    │
                                        │                           └────────────┘
                                        │
                                    WebUI (8766)
                                        │
                                        ▼
                                 FastAPI 控制面板
```

---

## 🛠️ 开发者

### 目录结构

```
wechat-onebot-bridge/
├── bridge/          ← bridge 主程序源码（WeFlow SSE → OneBot v11）
│   ├── main.py
│   ├── config.py
│   ├── config.example.json
│   ├── bridge_core.py
│   ├── ob_server.py
│   ├── uia_sender.py
│   └── ...
├── manager/         ← 桌面管理器源码（tkinter GUI）
│   ├── bridge_manager.py
│   ├── log_tail.py
│   └── README.md
├── webui/           ← WebUI 控制面板（FastAPI + Vue 前端）
│   ├── app.py
│   ├── dist/        ← 前端构建产物
│   └── frontend/    ← Vue 源码
├── build.py         ← 一键打包脚本（PyInstaller）
├── LICENSE
├── README.md
└── .gitignore
```

### 打包成 exe

```bash
pip install pyinstaller
python build.py
# 产物：dist/WeChatOneBotBridge-v1.0.0.zip
```

### 从源码跑 bridge

```bash
cd bridge
pip install -r requirements.txt
python main.py
```

### 前端开发

```bash
cd webui/frontend
npm install
npm run build   # 产物写入 webui/dist
```

---

## 📜 协议与致谢

### 协议

MIT License（见 [LICENSE](LICENSE)）。

### 致谢

- **原作者**：Akasha-WeChat（[原仓库](https://github.com/)）——本项目是基于它的衍生分支，改动为对接 MaiBot napcat-adapter 并附赠桌面管理器。
- [MaiBot](https://github.com/) —— 主项目。
- [WeFlow](https://weflow.top) —— 微信 hook 框架。
- [uiautomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) —— 跨进程窗口自动化。
- [OneBot v11](https://github.com/botuniverse/onebot-11) —— 标准化聊天机器人协议。

> 若在使用过程中有任何问题，欢迎到 [Issues](https://github.com/jessiongod/wechat-onebot-bridge/issues) 提问。有用的话点个 ⭐ 支持一下！
