# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - Unreleased

### 修复

- 修复网易云分享链接里的 `vipToken` 等账号凭据参数被原样发给机器人的问题，现在只保留歌曲 id。
- 修复笔记等卡片的标题带换行的问题。
- 修复带 `[旺柴]` 这类表情码的文字发送失败：微信把表情码就地换成内嵌对象，输入框回读到的是对象占位符，被误判为文字没进到输入框。
- 修复图片「发糊」：存在高清原图 `_h.dat` 时优先使用它，不再只取微信给的中图主文件。
- 修复微信重新登录后桥接把离线期间同步进本地库的旧消息逐条推给机器人的问题：这些消息的本地编号是新的、发送时间却是当初的，只看游标会把它们当成新消息。现在发送时间早于闸线的消息不再推送，日志按会话汇总一行。
- 修复微信从登录界面进主界面后发送通道整体不可用的问题：换窗口后旧控件连不上 UI Automation 提供方，读取窗口属性抛 `E_NOCONNECTION`，异常打断发送器创建，此后每次发送都报「桥接未运行」。现在读取窗口信息失败只记调试日志，发送器照常建起来；绑定用的句柄也随窗口一起换成新的。
- 修复还原出来的动图表情慢放的问题：原来把所有帧按每帧 100 毫秒拼成 GIF，而表情码流是 30 帧/秒，播放时间被拉长到三倍多。现在整段交给 FFmpeg 合成 GIF，帧间隔按码流原样保留；同一批表情的合成耗时也从数秒降到半秒以内。`data\emoticon\` 下按旧方式生成的解码结果会在首次用到时整批清掉重新生成。

### 新增

- 新增语音、文件、视频的本体推送：语音由 SILK 转成 WAV 后以 `record` 段上报，文件与视频取微信已落盘的本体，分别附 `file`、`video` 段；本体还没到本机时等待至多 30 秒，仍没有就只按文字推送。
- 新增卡片消息的解读：公众号文章、B 站与网易云分享、小程序、合并转发的聊天记录（逐行展开）、视频号、笔记、接龙、群公告、转账、红包、位置、名片都转成多行文字，链接去掉追踪参数，事件另带结构化的 `card` 字段；表情型卡片走表情通道。
- 出站的 `share`、`json` 段降级为标题加链接的文本；没有链接的段计入失败。
- 入站 `file` 段新增 `url` 与 `file_id`，面板新增 `/files/<令牌>/<文件名>` 下载地址；新增 `get_group_file_url`、`get_private_file_url` 动作。AstrBot 只认 `http` 地址或能换出地址的 `file_id`，此前读不到桥接机器上的路径。
- 入站图片、语音、视频段与文件一样带 `url` 与 `file_id`，客户端与桥接不在一台机器时按地址取件；图片不再内联 `base64`。新增 `web_public_host` 配置：媒体下载地址用它拼主机名，留空按 `web_bind_host` 推断，跨机部署时把 `web_bind_host` 改成 `0.0.0.0`。
- 新增 `wx_stale_seconds` 配置（默认 `600`，`0` 表示不过滤）：发送时间早于这么多秒的消息不推给机器人；开启 `wx_backfill_seconds` 时取两者中较大的一个，回填窗口内的消息不会被挡掉。

### Fixed

- Fixed account-credential parameters such as `vipToken` in NetEase Music share links being passed to the bot; only the song id is kept.
- Fixed card titles such as notes containing line breaks.
- Fixed text containing emoji codes such as `[旺柴]` failing to send: WeChat turns the code into an embedded object, so reading the input box back returned an object placeholder that was mistaken for text that never made it in.
- Fixed blurry images: when the full-resolution `_h.dat` exists it is used first instead of the medium-size main file.
- Fixed the bridge pushing old messages to the bot one by one after WeChat logs in again: messages synced into the local database while offline carry fresh local ids but their original send times, so the cursor alone treated them as new. Messages sent before the cutoff are no longer pushed, and the log reports one summary line per chat.
- Fixed the whole send channel being unusable once WeChat moved from the login screen into the main window: the replaced window's old control can no longer reach its UI Automation provider, reading its properties raised `E_NOCONNECTION`, the exception aborted sender creation, and every later send reported "the bridge is not running". Reading window information now only logs at debug level, the sender is still created, and the bound handle follows the new window.
- Fixed restored animated stickers playing in slow motion: every frame was assembled into a GIF at a fixed 100 ms, while the sticker bitstream runs at 30 fps, stretching the playback time by more than three times. The stream now goes to FFmpeg in one piece, which keeps the original frame timing; assembling the same stickers also dropped from seconds to under half a second. Results already decoded under `data\emoticon\` the old way are cleared in one batch the first time they are needed and regenerated.

### Added

- Added delivery of voice, file and video bodies: voice is converted from SILK to WAV and reported as a `record` segment, files and videos are taken from what WeChat already saved locally and attached as `file` and `video` segments; when the body has not reached the machine yet the bridge waits up to 30 seconds and then pushes the text alone.
- Added interpretation of card messages: official-account articles, Bilibili and NetEase Music shares, mini programs, forwarded chat records (expanded line by line), Channels posts, notes, group solitaires, group announcements, transfers, red packets, locations and contact cards become multi-line text with tracking parameters removed from links, and the event also carries a structured `card` field; sticker-type cards use the sticker channel.
- Outgoing `share` and `json` segments are downgraded to text made of the title and link; segments without a link count as failed.
- Inbound `file` segments now carry `url` and `file_id`, the panel serves them at `/files/<token>/<name>`, and the `get_group_file_url` and `get_private_file_url` actions were added. AstrBot only accepts an `http` address or a `file_id` it can exchange for one, so it could not read paths on the bridge machine before.
- Inbound image, voice and video segments now carry `url` and `file_id` like files do, so a client on another machine can fetch them; images are no longer inlined as `base64`. Added the `web_public_host` setting: media download addresses take their host from it, fall back to `web_bind_host`, and a cross-machine deployment sets `web_bind_host` to `0.0.0.0`.
- Added the `wx_stale_seconds` setting (default `600`; `0` disables the filter): messages sent more than this many seconds ago are not pushed to the bot. When `wx_backfill_seconds` is enabled the larger of the two applies, so messages inside the backfill window are never dropped.

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
- 「微信画面」页每 3 秒重新读取一次登录状态（只查状态、不抓画面，不切屏），状态角标会跟着变化；画面仍只在打开页面与点「刷新画面」时更新。

### Fixed

- Fixed "install and restart" doing nothing while the tray manager was running: the swap script waited only for the bridge, but the manager kept `Flandora.exe` locked, so the program directory could be neither moved nor rolled back, and the script ended silently. It now stops the manager and the bridge first, retries the move, and records the run in `data/updates/apply-<version>.log`.
- Fixed the "WeChat screen" page showing nothing on WeChat 4.2: the 4.2 main window does not answer `PrintWindow`, so the frame is now taken by switching to the isolated desktop and back; the window size is also used to recognise a logged-in Qt main window instead of reporting "unknown".
- Fixed sends and forwarded clicks failing silently when WeChat and the bridge run at different privilege levels: Windows (UIPI) drops mouse, keyboard and UIA messages posted by a lower-integrity process, while the log and panel still look healthy. The bridge now compares both process tokens, `/api/desktop/status` reports a `privilege` field, and the panel shows a banner; a rejected click returns an error instead of reporting success.
- Fixed the update relaunch inheriting the updater's elevation, which left WeChat and the bridge at different privilege levels: the swap script now starts the new version through Explorer.

### Added

- Added the `ignore_official_chats` setting (enabled by default): messages from official chats such as WeChat Team, service notifications and subscription/service accounts are no longer forwarded to the bot. Those accounts reply automatically, so forwarding them starts an endless back-and-forth. File Transfer Helper is the user's own chat and is not filtered; turn the setting off to receive official chats again.

### Changed

- The "WeChat screen" page no longer auto-refreshes: taking a frame briefly switches to the WeChat desktop, and polling would keep stealing the screen. A frame is now taken when the page opens and when "refresh" is pressed.
- The "WeChat screen" page now re-reads the login state every 3 seconds (status only, without capturing a frame or switching desktops), so the badge follows along; the picture is still updated only when the page opens and when "refresh" is pressed.
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
