"""桥接的停止信号：一个手动复位的命名事件，管理器置位，桥接收尾退出。

管理器与桥接是两个进程，托盘里的「停止」「退出」不能走 HTTP——面板开着密码时
``/api/*`` 全要会话 Cookie，管理器拿不到。内核事件没有这个问题：同一用户的两个
进程都能按名字打开它，置位不需要任何凭据，桥接进程消失后事件对象也随之释放。

用法只有三步：桥接启动时 :func:`clear`，管理器要它停时 :func:`request`，桥接在
后台线程里 :func:`wait` 等这一次置位。事件是手动复位的，置位后一直有效，所以每次
启动桥接前都要清掉上一次的请求，否则新进程一起来就自己退出。
"""

import ctypes
import os
from ctypes import wintypes

EVENT_NAME = "Global\\Flandora_Stop"

IS_WINDOWS = os.name == "nt"

_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0
_ERROR_ALREADY_EXISTS = 183

_handle_cache = None
_kernel32 = None


def _api():
    """惰性取 kernel32，声明参数与返回类型（不声明的话 64 位句柄会被截断）。"""
    global _kernel32
    if _kernel32 is None:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateEventW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        api.CreateEventW.restype = wintypes.HANDLE
        api.SetEvent.argtypes = [wintypes.HANDLE]
        api.SetEvent.restype = wintypes.BOOL
        api.ResetEvent.argtypes = [wintypes.HANDLE]
        api.ResetEvent.restype = wintypes.BOOL
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        _kernel32 = api
    return _kernel32


def _handle():
    """按名字打开（不存在则创建）事件；失败返回 None。"""
    global _handle_cache
    if not IS_WINDOWS:
        return None
    if _handle_cache is None:
        api = _api()
        ctypes.set_last_error(0)
        handle = api.CreateEventW(None, True, False, EVENT_NAME)
        if not handle:
            error = ctypes.get_last_error()
            # 已经有人建过同名事件时 CreateEventW 照样给可用的句柄
            if error != _ERROR_ALREADY_EXISTS:
                _handle_cache = 0
                return None
        _handle_cache = handle
    return _handle_cache or None


def request() -> bool:
    """请桥接停止（管理器侧调用）。返回是否成功置位。"""
    handle = _handle()
    if handle is None:
        return False
    return bool(_api().SetEvent(handle))


def clear() -> None:
    """清掉上一次的停止请求（桥接启动、管理器起桥接之前调用）。"""
    handle = _handle()
    if handle is None:
        return
    _api().ResetEvent(handle)


def wait(timeout_ms=None) -> bool:
    """等停止请求（桥接侧调用）；``timeout_ms`` 为 None 时一直等。"""
    handle = _handle()
    if handle is None:
        return False
    timeout = _INFINITE if timeout_ms is None else int(timeout_ms)
    return _api().WaitForSingleObject(handle, timeout) == _WAIT_OBJECT_0
