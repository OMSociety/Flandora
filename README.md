<p align="center">
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/assets/readme/hero.svg" width="100%" alt="Flandora 把微信个人号接成 OneBot v11 服务端" />
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
  <a href="#核心能力">核心能力</a> ·
  <a href="#文档">文档</a> ·
  <a href="docs/config.md">配置</a> ·
  <a href="#使用边界与许可">使用边界</a> ·
  <a href="https://github.com/OMSociety/Flandora/issues">问题反馈</a>
</p>

Flandora 是面向微信 PC 客户端的 Windows 桥接程序：在微信登录的瞬间截获本机数据库口令，只读解密聊天库收取消息，并以账号本人身份在隔离桌面上投递回复，对外提供 [OneBot v11](https://github.com/botuniverse/onebot-11) 正向 WebSocket 服务端与浏览器控制台。

> [!CAUTION]
> Flandora 是独立的第三方项目，与腾讯 / 微信无隶属或授权关系。项目仅供学习与技术研究，请遵守《微信软件许可及服务协议》及适用法律；以自动化方式驱动自己的客户端存在账号被限制或封禁的风险，软件按「现状」提供，使用风险自负。详见[使用边界与许可](#使用边界与许可)。
>
> **Disclaimer:** Flandora is an independent third-party project, not affiliated with or authorized by Tencent / WeChat. It is provided for study and research only, "as is" and without warranty, and you are responsible for complying with the applicable laws and the WeChat terms of service.

<p align="center">
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/dashboard.png" width="100%" alt="Flandora 控制台的仪表盘：收件源、OneBot 连接、发送通道与进程状态" />
</p>

## 核心能力

| 场景 | 能力 |
| --- | --- |
| 收件 | 登录瞬间取钥；解密副本只读轮询；私聊与群聊的文本、图片、表情、语音占位；群消息全量上报 |
| 发送 | 文本、图片、文件、群内 @ 提及、语音（mp3 卡片）与视频（文件形式）；默认在隔离桌面投递；发送前清空输入框并核对回显 |
| OneBot v11 | 四类网络入口（WS / HTTP 服务端、反向 WS、HTTP 上报）可同时开启；消息段与 CQ 码双向编解码；`is_mentioned` 如实标记是否被 @；入口改完保存即生效 |
| 群与身份 | 群内按发言人拆分 `user_id`；群名映射；多昵称识别；动作参数既收整数 ID 也收 wxid / 群标识 |
| 取钥 | 四步向导：免责声明、数据库口令、图片密钥、校验；口令与密钥由 Windows DPAPI 加密落盘；重启后自动认出盘上已就位的口令与密钥，不会退回重取 |
| 隔离桌面 | 微信跑在独立的 Windows 桌面上；重启后接管仍在运行的实例；需要时由你点按钮结束 |
| 控制台 | 仪表盘、消息记录、节点配置、微信画面、取钥向导、运行日志、调试面板、系统设置；明暗两套主题 |

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

2. 退出自己的微信，运行 `Flandora-Manager.exe`，浏览器打开 [`http://127.0.0.1:4950`](http://127.0.0.1:4950)。
3. 进入「取钥向导」，四步走完：勾选免责声明 → 开始取钥 → 在向导右侧的实况画面里完成登录 → 想让机器人看图就继续抓图片密钥 → 校验。

之后让客户端连上来即可：

```json
{
  "host": "127.0.0.1",
  "port": 7999,
  "token": ""
}
```

> **提示**：`ob_server_token` 与客户端 `token` 必须一致。留空表示不校验鉴权，此时服务端只监听 `127.0.0.1`；要让别的机器连进来，改 `ob_server_host`、在防火墙放行 7999，并务必填一串随机 token。

## 文档

| 想看什么 | 去哪 |
| --- | --- |
| 微信标识与 OneBot 整数怎么对应、支持哪些动作、消息段与事件面的实际形态、返回码含义 | [docs/protocol.md](docs/protocol.md) |
| 控制台八个页面各做什么，`data/` 下每个文件是干什么的 | [docs/console.md](docs/console.md) |
| 每个配置项的类型、默认值与说明，以及版本更新的工作机制 | [docs/config.md](docs/config.md) |
| 用 Python 自己写客户端，或把动作面挂给支持 MCP 的 AI 客户端 | [docs/sdk.md](docs/sdk.md) |
| 开发环境、命令、CI 与该跑哪些闸门 | [docs/development.md](docs/development.md) |
| 已知限制：拿不到的本体、要付出的代价、还没接通的能力 | [docs/limitations.md](docs/limitations.md) |
| 接 AstrBot（aiocqhttp 适配器）的步骤与能力缺口清单 | [docs/astrbot.md](docs/astrbot.md) |
| 代码结构、依赖规则与扩展点 | [docs/architecture.md](docs/architecture.md) |
| 发布前要手工走一遍的冒烟清单 | [docs/smoke-test.md](docs/smoke-test.md) |
| 刻意不做的事与原因、需要在真机上确认的项 | [docs/wip.md](docs/wip.md)、[docs/needs-user.md](docs/needs-user.md) |
| 工程约定与验收口径（给改代码的人） | [AGENTS.md](AGENTS.md) |

## 使用边界与许可

> **提示**：本项目以 [MIT](LICENSE) 发布，可自由使用、修改与再分发，保留版权与许可声明即可。禁止用于任何违法违规用途，包括但不限于批量加好友、自动营销、骚扰与诈骗。

本工具通过自动化方式驱动你自己登录的微信客户端收发消息，这类用法可能与微信的用户协议冲突。请只用自己的号，并自行承担账号被限制或封禁的风险。项目为独立工具，与腾讯公司无任何关联，不承诺可用性与送达率；微信改版导致接口失效属官方行为，不在担保范围内。使用产生的一切后果由使用者自行承担。

发布包内 `docs/third-party-licenses/` 收录了随包分发组件的许可证原文。

## 鸣谢与致谢

- [WeChat-Onebot-Bridge](https://github.com/jessiongod/wechat-onebot-bridge)：本仓库的前身，部分早期设计来自这个项目
- [OneBot v11](https://github.com/botuniverse/onebot-11)：事件与动作遵循的协议规范
- [SnowLuma](https://github.com/SnowLuma/SnowLuma)：优秀的 OneBot v11 服务端实现，本项目多有参考
