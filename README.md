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