"""Windows 平台能力：识别窗口与查询进程权限的纯函数。

发送器（``uia_sender``）与隔离桌面（``wx_desktop``）都需要枚举微信窗口、
判定登录/聊天界面，以及核对双方的权限级别。两者互相 import 会成环，所以这些
能力独立成包：它们单向依赖本包，本包不 import 它们。
"""

from . import privilege, windows

__all__ = ["privilege", "windows"]
