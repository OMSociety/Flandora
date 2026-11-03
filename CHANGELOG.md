# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-11-03

### 修复

- 修复图片偶发退回 `[图片]` 的问题：`hardlink.db`、`message_resource.db` 的新行先写进 `-wal`，旧逻辑只比较主文件的 size 与 mtime，刚到的图片行会被判成不存在。现在把这两个库的 `-wal` 一并纳入变更判据。
- 图片定位失败不再只写 debug 日志：改为 warning，并带上 md5、local_id 与会话标识，出问题时能在日志里看到原因。

### Fixed

- Fixed images occasionally degrading to the literal `[图片]`: new rows in `hardlink.db` and `message_resource.db` land in `-wal` first, while the old change check only looked at the main file, so brand-new image rows were treated as missing. Both `-wal` files now participate in the change check.
- Image resolution failures are no longer debug-only: they log a warning with the md5, local id and talker, so the cause is visible in the log.
