"""单实例判重：命名互斥量。

为什么不用 pid 文件：进程被强杀（任务管理器结束、蓝屏、断电）时 pid 文件会留下，
里面的 pid 还可能被系统分给别的进程，于是要么误判「已在运行」而拒绝启动，要么
判不出去而放进第二个实例。互斥量由内核在进程退出时自动释放，不存在残留。

用法：

    from flandora.single_instance import (
        acquire_mutex, SINGLE_INSTANCE_NAME)
    handle, already_running = acquire_mutex(SINGLE_INSTANCE_NAME)
    if already_running:
        sys.exit(1)

返回的 `handle` 必须一直持有：提前释放等于把互斥量交出去，判重随即失效。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger("flandora")

# 带 Global\ 前缀 → 作用域是整个机器而不是当前登录会话，多用户切换也拦得住。
# 名字与项目对外名一致，改名时要一起改。
SINGLE_INSTANCE_NAME = "Global\\Flandora_SingleInstance"

# CreateMutexW 在互斥量已存在时返回的 last-error
ERROR_ALREADY_EXISTS = 183


def acquire_mutex(name: str = SINGLE_INSTANCE_NAME):
    """创建（或打开）命名互斥量。

    返回 `(handle, already_running)`。

    - 非 Windows：返回 `(None, False)`。
    - 创建失败：返回 `(None, False)` 并记一条日志。宁可放过一个重复实例，也不要
      因为拿不到内核对象就让人完全起不来。
    """
    if sys.platform != "win32":
        return None, False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        log.warning(
            "创建互斥量 %s 失败（err=%s），本次跳过重复启动检查", name, ctypes.get_last_error()
        )
        return None, False

    return handle, ctypes.get_last_error() == ERROR_ALREADY_EXISTS
