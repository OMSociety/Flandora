# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.3] - 2026-11-17

### 修复

- 修复「安装并重启」在托盘管理器运行时什么都不做的问题：替换脚本只等桥接退出，管理器仍占着 `Flandora.exe`，脚本挪不动程序目录、回滚也删不掉，最后静默结束。现在先停掉管理器与桥接并等进程退出，挪文件带重试，整段过程写进 `data/updates/apply-<版本>.log`。
- 修复「微信画面」在微信 4.2 上取不到图的问题：4.2 的主窗口不响应 `PrintWindow`，改为切到隔离桌面抓一帧再切回；顺带按窗口尺寸识别已登录的 Qt 主窗口，不再把已登录状态报成「状态未知」。
- 修复微信与桥接权限级别不一致时发送与点击静默失效的问题：微信以管理员权限运行时，普通权限的桥接投递的鼠标、键盘消息与 UIA 动作会被 Windows（UIPI）丢弃，日志与面板却显示正常。桥接现在比较双方进程的令牌级别，`/api/desktop/status` 带上 `privilege` 字段，面板显示提示条；点击投递失败如实返回错误，不再报告成功。
- 修复更新后新版本继承更新进程提权、导致微信与桥接权限级别不一致的问题：替换脚本拉起新版本改为经资源管理器发起。

### 新增

- 新增 `ignore_official_chats` 配置（默认开启）：微信团队、服务通知、订阅号/服务号这类官方会话的消息不再推给机器人。这些账号收到消息会自动回话，推给机器人只会一来一回停不下来。文件传输助手是用户自己的会话，不在过滤范围；需要接收官方会话时可以关掉这一项。

### 变更

- 「微信画面」不再自动刷新：取一帧要短暂切到微信所在桌面，轮询会反复抢屏；改为打开页面时取一帧、之后点「刷新画面」再取。

### Fixed

- Fixed "install and restart" doing nothing while the tray manager was running: the swap script waited only for the bridge, but the manager kept `Flandora.exe` locked, so the program directory could be neither moved nor rolled back, and the script ended silently. It now stops the manager and the bridge first, retries the move, and records the run in `data/updates/apply-<version>.log`.
- Fixed the "WeChat screen" page showing nothing on WeChat 4.2: the 4.2 main window does not answer `PrintWindow`, so the frame is now taken by switching to the isolated desktop and back; the window size is also used to recognise a logged-in Qt main window instead of reporting "unknown".
- Fixed sends and forwarded clicks failing silently when WeChat and the bridge run at different privilege levels: Windows (UIPI) drops mouse, keyboard and UIA messages posted by a lower-integrity process, while the log and panel still look healthy. The bridge now compares both process tokens, `/api/desktop/status` reports a `privilege` field, and the panel shows a banner; a rejected click returns an error instead of reporting success.
- Fixed the update relaunch inheriting the updater's elevation, which left WeChat and the bridge at different privilege levels: the swap script now starts the new version through Explorer.

### Added

- Added the `ignore_official_chats` setting (enabled by default): messages from official chats such as WeChat Team, service notifications and subscription/service accounts are no longer forwarded to the bot. Those accounts reply automatically, so forwarding them starts an endless back-and-forth. File Transfer Helper is the user's own chat and is not filtered; turn the setting off to receive official chats again.

### Changed

- The "WeChat screen" page no longer auto-refreshes: taking a frame briefly switches to the WeChat desktop, and polling would keep stealing the screen. A frame is now taken when the page opens and when "refresh" is pressed.
## [1.0.2] - 2026-11-12

### 修复

- 修复新加的群发不出回复的问题：群刚建好时群名册可能还没有群名，发送逻辑把群标识（`xxx@chatroom`）当成搜索词，微信搜索框永远选不中会话。现在发送前单独刷新通讯录、按实时群名发送，群名确实缺失时明确报错，不再拿群标识去搜。
- 修复改昵称后 @ 识别滞后的问题：桥接缓存的自身昵称只在 `store` 对象变化时重读，群成员名册按 600 秒超时，运行中改名后新名字的 @ 既不算提及、也解析不出 at 段。现在 `contact.db` 每重解密一次，通讯录版本号就前进一位，昵称、名册与 `get_login_info`/引用判定用的自身昵称一起随之失效。
- 修复「检查更新」查不到版本的问题：版本号改从订阅条目的 `<id>`（`…/Repository/<id>/<tag>`）读取，发布标题写成 `Flandora 1.0.2` 这类自由文本也能识别；条目只有标题且标题形如 `v1.2.3` 时仍按标题兜底。
- 修复更新完成后托盘管理器不随新版本启动的问题：替换脚本只拉起了 `Flandora-Bridge.exe`，管理器退出后不再回来，用户只剩面板、看不到托盘。现在优先拉起管理器 `Flandora.exe`（桥接由管理器启动），老安装里没有管理器时才退回桥接。

### 变更

- `contact.db` 的变更判据纳入 `-wal`：改名常常先写进 WAL，只看主文件会漏。
- 读不到通讯录时不再沿用旧昵称与旧名册，改为返回空并写 warning（同一原因每分钟最多一条）。

### Fixed

- Fixed replies failing in newly added groups: before the group had a name in the local roster, the send path used the raw `xxx@chatroom` handle as the search text, which never selects a chat in WeChat's search box. The contact table is now refreshed right before sending, the live group name is used, and a missing name is reported instead of searching by handle.
- Fixed stale `@` detection after a nickname change: the bridge cached its own nickname until the store object changed, and the member name map expired only after 600 seconds, so mentions of the new name were neither flagged nor turned into `at` segments. The contact table now carries a revision that advances whenever `contact.db` is re-decrypted, invalidating the nickname, the member map, and the self name used by `get_login_info` and quote checks.
- Fixed update checks reporting no versions: the tag is read from the feed entry `<id>` (`…/Repository/<id>/<tag>`), so releases titled like `Flandora 1.0.2` are still discovered; entries that only carry a `v1.2.3` title keep working through the title fallback.
- Fixed the tray manager not coming back after an update: the swap script relaunched only `Flandora-Bridge.exe`, so once the manager exited the tray icon was gone and only the panel remained. It now launches the manager `Flandora.exe` first (the manager starts the bridge) and falls back to the bridge only when the manager is absent.

### Changed

- The `contact.db` change check now includes its `-wal` file: renames usually land in the WAL first, so checking only the main file missed them.
- When the contact table cannot be read, stale nicknames and name maps are no longer reused; an empty result is returned with a warning (at most once per minute per reason).

## [1.0.1] - 2026-11-03

### 修复

- 修复图片偶发退回 `[图片]` 的问题：`hardlink.db`、`message_resource.db` 的新行先写进 `-wal`，旧逻辑只比较主文件的 size 与 mtime，刚到的图片行会被判成不存在。现在把这两个库的 `-wal` 一并纳入变更判据。
- 图片定位失败不再只写 debug 日志：改为 warning，并带上 md5、local_id 与会话标识，出问题时能在日志里看到原因。

### Fixed

- Fixed images occasionally degrading to the literal `[图片]`: new rows in `hardlink.db` and `message_resource.db` land in `-wal` first, while the old change check only looked at the main file, so brand-new image rows were treated as missing. Both `-wal` files now participate in the change check.
- Image resolution failures are no longer debug-only: they log a warning with the md5, local id and talker, so the cause is visible in the log.
