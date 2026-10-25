"""Windows 平台能力：只放「识别窗口」的纯函数与常量。

发送器（``uia_sender``）与隔离桌面（``wx_desktop``）都需要枚举微信窗口、
判定登录/聊天界面。两者互相 import 会成环，所以这些能力独立成包：
它们单向依赖本包，本包不 import 它们。
"""

from . import windows

__all__ = ["windows"]
