# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.2] - 2026-11-12

### 修复

- 修复新加的群发不出回复的问题：群刚建好时群名册可能还没有群名，发送逻辑把群标识（`xxx@chatroom`）当成搜索词，微信搜索框永远选不中会话。现在发送前单独刷新通讯录、按实时群名发送，群名确实缺失时明确报错，不再拿群标识去搜。
- 修复改昵称后 @ 识别滞后的问题：桥接缓存的自身昵称只在 `store` 对象变化时重读，群成员名册按 600 秒超时，运行中改名后新名字的 @ 既不算提及、也解析不出 at 段。现在 `contact.db` 每重解密一次，通讯录版本号就前进一位，昵称、名册与 `get_login_info`/引用判定用的自身昵称一起随之失效。

### 变更

- `contact.db` 的变更判据纳入 `-wal`：改名常常先写进 WAL，只看主文件会漏。
- 读不到通讯录时不再沿用旧昵称与旧名册，改为返回空并写 warning（同一原因每分钟最多一条）。

### Fixed

- Fixed replies failing in newly added groups: before the group had a name in the local roster, the send path used the raw `xxx@chatroom` handle as the search text, which never selects a chat in WeChat's search box. The contact table is now refreshed right before sending, the live group name is used, and a missing name is reported instead of searching by handle.
- Fixed stale `@` detection after a nickname change: the bridge cached its own nickname until the store object changed, and the member name map expired only after 600 seconds, so mentions of the new name were neither flagged nor turned into `at` segments. The contact table now carries a revision that advances whenever `contact.db` is re-decrypted, invalidating the nickname, the member map, and the self name used by `get_login_info` and quote checks.

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
