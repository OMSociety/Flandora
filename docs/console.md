# 控制台与数据落点

## 控制台

面板默认监听 `127.0.0.1:4950`，八页：仪表盘、消息记录、节点配置、微信画面、取钥向导、运行日志、调试面板、系统设置。调试面板分「动作调试」与「发送测试」两个标签页：前者把动作目录整张列出来，选动作、按参数表填值、点执行，回来的响应原样打印，也能看最近推送的事件，或者拼好消息段直接生成 CQ 码；后者把一条文本投进真实微信窗口，验证发送通道通不通。两者走同一条动作链路，线上调不通的动作可以在这里先复现；只打开的标签页才会去拉它自己的数据。节点配置把 OneBot 入口按类型归拢（正向 WS 服务端、HTTP 服务端、反向 WS、HTTP 上报），逐条启用、逐条鉴权与编辑，通用设置只在没有启用中的入口时兜底，左列本机账号卡里放客户端附件目录。系统设置按职责分区（安全与服务、存储、高级、关于），一次只显示一个分区，每块单独保存，版本与更新在「关于」里。面板本身可以设登录密码（系统设置「安全与服务」→「面板访问」的「打开面板时要密码」），设了之后所有接口与实时事件流都要先登录，密码只以 PBKDF2 哈希存在 `config.json` 里。同一页还能把监听地址放开到局域网（`0.0.0.0` 只在设了密码后可选）、贴一对 PEM 证书让面板走 HTTPS，以及把 `config.json` 导出带走或从备份恢复。「存储」一页把数据目录的占用按类拆开，并给出图片缓存、解密缓存、日志与消息存档的清理入口。

<p align="center">
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/dashboard.png" width="49%" alt="仪表盘：运行状态、收发计数与 24 小时消息量" />
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/messages.png" width="49%" alt="消息记录：按收发方向与类型筛选" />
</p>

仪表盘：收件源、OneBot 连接、发送通道与进程状态。消息记录：谁说了什么、发出去没有，一眼可查。

<p align="center">
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/nodes.png" width="100%" alt="节点配置：入口按类型分组，逐条启用、鉴权与编辑" />
</p>

节点配置：正向与反向入口按类型归拢，逐条开关、逐条鉴权，改完统一保存。

<p align="center">
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/config.png" width="70%" alt="系统设置：分区导航，安全与服务、存储、高级、关于" />
  <img src="https://raw.githubusercontent.com/OMSociety/Flandora/main/docs/shots/mobile.png" width="28%" alt="窄屏下的仪表盘" />
</p>

系统设置：五处分区，一次只显示一块，每块单独保存；安全与服务里管密码、监听、HTTPS 与备份，存储里管占用与清理。窄屏：手机上同样能看状态。

仪表盘上的「今日消息」与 24 小时曲线读的是消息存档（重启与刷新都不清零）；发送方向的柱子来自本次运行，重启后从 0 重新计——发送不是推送事件，不进存档。

## 数据落点

数据目录就是 `config.json` 所在的目录：源码运行是仓库根的 `data/`，安装版在程序目录下。

| 路径 | 内容 |
| --- | --- |
| `data/config.json` | 全部配置（含 token 与 API key，别提交进仓库） |
| `data/wx_key.dat` | 数据库口令（DPAPI 加密，绑定当前 Windows 账户） |
| `data/wx_image_key.json` | 图片密钥 |
| `data/id_map.json` | wxid / 群 ID 与整数 ID 的映射表，删了 ID 会重新分配 |
| `data/msg_ids.db` | 消息号表（`message_id` 与微信库行号的对应） |
| `data/messages.db` | 已推送事件的存档，供 `get_msg`、历史检索与仪表盘统计 |
| `data/wx_cache/` | 解密副本与游标，可随时删 |
| `data/updates/` | 更新用的安装包、暂存目录、备份与替换脚本，可随时删 |
| `data/flandora.log` | 运行日志（UTF-8），写满按份轮转 |

换机器时把整个目录带走即可保住 ID 映射与推送存档；`wx_key.dat` 除外——它由 DPAPI 加密、只有生成它的那个 Windows 账户解得开，到新机器上要在取钥向导里重新取一次。
