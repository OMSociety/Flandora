"""进程完整性级别（是否提权）查询。

微信与桥接的权限级别不一致时，Windows 的 UIPI 会丢弃跨级投递的鼠标、键盘消息
与 UIA 动作：发送消息、点击转发都会静默失败，日志里只剩「附着输入队列失败」或
「点击没反应」。桌面管理器在状态里带上双方的令牌信息，面板据此提示用户修复。

只做查询：``OpenProcess`` 被拒（目标进程比调用者权限高）本身就说明级别更高，
这里返回 ``None``，由调用方按「读不到、多半更高」处理。
"""

import ctypes
import os
from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20


#: 与 `wintypes` 无关的简化结构：GetTokenInformation 的 TokenElevation 只回一个 DWORD。
class _TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


def process_elevated(pid: int) -> bool | None:
    """pid 的令牌是否提权；进程不存在或无权查询时返回 None。"""
    if not pid:
        return None
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            value = _TOKEN_ELEVATION()
            size = wintypes.DWORD(ctypes.sizeof(value))
            ok = advapi32.GetTokenInformation(
                token, TOKEN_ELEVATION, ctypes.byref(value), size, ctypes.byref(size)
            )
            if not ok:
                return None
            return bool(value.TokenIsElevated)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(handle)


def self_elevated() -> bool:
    """当前进程是否提权（查询失败按未提权处理）。"""
    return bool(process_elevated(os.getpid()))


def window_process_elevated(hwnd: int) -> bool | None:
    """窗口所属进程的令牌是否提权；窗口无效或无权查询时返回 None。"""
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    return process_elevated(pid.value)


def status(hwnd: int) -> dict:
    """微信与桥接的权限级别，以及消息是否会被 UIPI 拦下。

    字段：
    - ``self_elevated``：桥接进程是否提权；
    - ``wechat_elevated``：微信是否提权，``None`` 表示令牌读不到（多半是它更高）；
    - ``blocked``：桥接未提权，而微信已提权或读不到——此时发送与点击都会被丢弃。
    """
    self_is = self_elevated()
    wechat_is = window_process_elevated(hwnd)
    return {
        "self_elevated": self_is,
        "wechat_elevated": wechat_is,
        "blocked": not self_is and wechat_is is not False,
    }
