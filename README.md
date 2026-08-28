# WeChatOneBotBridge

> **微信 ↔ OneBot v11 桥接**，让 MaiBot 直接接管微信收发消息。
> 本项目是 [Akasha-WeChat](https://github.com/) 的衍生分支，改为对接 MaiBot 的 napcat-adapter（OneBot v11 WS 服务端），并附桌面管理器。

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

从 [Releases](https://github.com/jessiongod/wechat-onebot-bridge/releases) 下载最新版，解压到任意目录即可，**无需 Python 环境**。

```
WeChatOneBotBridge/
├── MaiBotBridgeManager.exe   ← 双击启动桌面管理器
├── bridge.exe                ← 桥接服务（管理器自动调用）
├── log_tail.exe              ← 日志跟随器（管理器自动调用）
├── config.json               ← 配置文件（首次需手动填入）
├── 启动.bat
├── README.md
└── LICENSE
```

### 方式二：从源码运行（开发者）

需要 Python 3.10+：

```bash
git clone https://github.com/jessiongod/wechat-onebot-bridge.git
cd wechat-onebot-bridge
pip install -r bridge/requirements.txt
python manager/bridge_manager.py
```

---

## 🚀 快速开始

### 1. 准备 WeFlow

[WeFlow](https://weflow.top) 是一个开源的微信桌面 hook 工具，通过 SSE 协议把微信消息推送出去。

- 启动后访问 `http://127.0.0.1:5031`
- 在 WeFlow 设置里拿到 `access_token`

### 2. 启动 MaiBot（napcat-adapter 模式）

MaiBot 社区已有 napcat-adapter 插件，让它作为 WS 客户端连到本 bridge。

- 默认连入地址：`ws://127.0.0.1:7999`
- token：在 `config.json` 的 `ob_server_token` 字段（自己生成随机字符串）

### 3. 配置

把 `config.example.json` 复制为 `config.json`，替换占位符：

```jsonc
{
    "access_token": "你的 WeFlow access_token",
    "ob_server_token": "随机字符串（napcat-adapter 也填这个）",
    "bot_nicknames": ["机器人微信昵称"],
    "astrbot_attachments": "C:\\path\\to\\maibot\\attachments",
    "image_caption_api_key": "OpenAI 兼容 API key（可选）",
    "fun_asr_api_key": "阿里云百炼 API key（可选，语音转写）"
}
```

### 4. 启动

- **桌面管理器（推荐）**：双击 `启动.bat` 或 `MaiBotBridgeManager.exe`
- **纯命令行**：在 `bridge/` 目录跑 `python main.py`（或打包的 `bridge.exe`）

管理器启动后会自动接管任何已在运行的 bridge。状态显示「运行中 PID=xxx（外部，已接管）」即成功。

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

## 📂 目录结构

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
├── LICENSE
└── README.md
```

## 🛠️ 开发

### 打包成 exe

```bash
python build.py        # 需要 PyInstaller，会自动安装
# 产物：dist/WeChatOneBotBridge-v1.0.0.zip
```

### 依赖

```bash
pip install -r bridge/requirements.txt
pip install pystray Pillow       # 桌面管理器托盘用
```

### 调试

```bash
# 桥接实时日志
tail -f bridge/bridge.log
```

---

## ❓ 常见问题

**Q: 启动管理器后日志窗口是空的？**
A: 检查 `bridge.exe` 与管理器是否在同一目录。打包后三者必须在同一目录。

**Q: MaiBot 收不到微信消息？**
A: 检查 `ob_server_token` 两端是否一致；浏览器打开 `http://127.0.0.1:7999` 看能否连上。

**Q: 发送消息失败？**
A: 需要在 WebUI 里绑定微信窗口句柄（`wechat_hwnd`），微信重启后会变，需重新绑定。

**Q: 为什么桌面管理器会闪终端窗口？**
A: 那是早期版本的 bug。新版所有 PowerShell 子进程已加 `CREATE_NO_WINDOW`，不会再弹窗。

---

## 📜 协议

MIT License（见 [LICENSE](LICENSE)）

---

## 🙏 致谢

- 原作者：Akasha-WeChat（[原仓库](https://github.com/)）
- [MaiBot](https://github.com/) —— 主项目
- [WeFlow](https://weflow.top) —— 微信 hook 框架
- [uiautomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) —— 跨进程窗口自动化
- [OneBot v11](https://github.com/botuniverse/onebot-11) —— 标准化聊天机器人协议

> 若在使用过程中有任何问题，欢迎提 [Issue](https://github.com/jessiongod/wechat-onebot-bridge/issues)。
