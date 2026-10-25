"""微信窗口识别：枚举、任务栏闪烁、登录/聊天状态判定。

三处都要用这些能力：发送器（``uia_sender``）绑定窗口前要枚举与判状态，隔离
桌面（``wx_desktop``）判断「默认桌面上有没有微信」也要枚举，面板的
``/api/windows`` 组直接列窗口。``uia_sender`` 与 ``wx_desktop`` 互相 import
会成环（发送器要在隔离桌面上找窗口，桌面管理器又要知道窗口是谁），因此这些
纯函数落在这里，两边单向依赖本模块。

- 枚举与闪烁只用 ctypes，不依赖 uiautomation；
- 状态判定要遍历 UIA 控件树，``uiautomation`` 在函数内按需导入，缺包时返回
  ``unknown``，不阻止其余能力可用。
"""

import ctypes
import os
from ctypes import wintypes

# 微信登录界面的窗口类名。未登录时微信只存在这个窗口，而它的标题同样是
# 「微信」，仅按标题查找会误绑到登录界面：后续 Ctrl+F / 粘贴 / 回车都会
# 打进登录界面，并在发送后假报成功（消息其实从未发出）。
LOGIN_WINDOW_CLASS = "mmui::LoginWindow"

# 登录态判定按内容而非类名：掉线时登录布局承载在 mmui::MainWindow 里
# （类名与已登录主窗口相同），只比类名会把登录界面当成主界面。
LOGIN_TEXT_MARKERS = (
    "login_layout",
    "login_step_layout",
    "请重新登录",
    "为了你的账号安全",
)
CHAT_UI_MARKERS = ("通讯录", "聊天", "搜索")

# 内容扫描的边界：够覆盖登录布局与聊天界面，且不会在异常窗口上失控。
SCAN_MAX_DEPTH = 8
SCAN_MAX_NODES = 500


def _process_name(pid: int) -> str:
    """按 PID 取进程可执行文件名（如 WeChat.exe）。"""
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(h)
    return ""


def _process_start_time(pid: int) -> float:
    """进程启动时间（epoch 秒），失败返回 0。"""
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return 0
    try:
        ct, et, kt, ut = (
            wintypes.FILETIME(),
            wintypes.FILETIME(),
            wintypes.FILETIME(),
            wintypes.FILETIME(),
        )
        if kernel32.GetProcessTimes(
            h, ctypes.byref(ct), ctypes.byref(et), ctypes.byref(kt), ctypes.byref(ut)
        ):
            # FILETIME: 100ns 间隔，自 1601-01-01
            return ((ct.dwHighDateTime << 32) | ct.dwLowDateTime) / 1e7 - 11644473600
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(h)
    return 0


def enum_wechat_windows(desktop_name: str = "") -> list[dict]:
    """枚举所有疑似微信主窗口。

    判定：顶层可见窗口，标题含「微信/WeChat」，且类名像 Qt 窗口或进程是
    WeChat.exe/Weixin.exe（过滤浏览器标签页等同名窗口）。

    `desktop_name` 非空时改在指定 Desktop 上枚举（隔离桌面模式）——那些窗口在
    当前桌面的 `EnumWindows` 里根本不可见，必须走 `EnumDesktopWindows`。
    """
    user32 = ctypes.windll.user32
    results = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        try:
            # 隔离桌面上的窗口不可见，不能按可见性过滤
            if not desktop_name and not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            tbuf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, tbuf, length + 1)
            title = tbuf.value
            if not any(kw in title for kw in ("微信", "WeChat")):
                return True
            cbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cbuf, 256)
            class_name = cbuf.value
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc = _process_name(pid.value)
            # 过滤：Qt 窗口类名或微信进程名，避免误收浏览器标签页
            if not (("QWindowIcon" in class_name) or proc.lower() in ("wechat.exe", "weixin.exe")):
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            results.append(
                {
                    "hwnd": hwnd,
                    "pid": pid.value,
                    "title": title,
                    "class_name": class_name,
                    "process": proc,
                    "start_time": _process_start_time(pid.value),
                    "iconic": bool(user32.IsIconic(hwnd)),
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                }
            )
        except Exception:
            pass
        return True

    if desktop_name:
        hdesk = user32.OpenDesktopW(desktop_name, 0, False, 0x000F01FF)
        if not hdesk:
            return results
        try:
            user32.EnumDesktopWindows(hdesk, EnumWindowsProc(_cb), 0)
        finally:
            user32.CloseDesktop(hdesk)
    else:
        user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return results


def flash_window(hwnd: int, count: int = 5) -> bool:
    """让指定窗口的任务栏按钮闪烁（不抢焦点），用于视觉辨认。"""
    try:
        user32 = ctypes.windll.user32

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        info = FLASHWINFO(
            ctypes.sizeof(FLASHWINFO), hwnd, 0x0003 | 0x000C, count, 0
        )  # FLASHW_ALL | FLASHW_TIMERNOFG
        return bool(user32.FlashWindowEx(ctypes.byref(info)))
    except Exception:
        return False


def state_from_handle(hwnd: int) -> str:
    """按窗口句柄判断微信状态，返回 'login' / 'chat' / 'unknown'。

    供 WebUI 与桌面管理器复用：`ControlFromHandle` 能取到**别的桌面**上的
    控件，所以隔离桌面上的窗口也能这样判状态。
    """
    try:
        import uiautomation as auto
    except ImportError:
        return "unknown"
    try:
        ctrl = auto.ControlFromHandle(hwnd)
        if not ctrl or not ctrl.Exists(0.3):
            return "unknown"
    except Exception:
        return "unknown"
    return state_of(ctrl)


def state_of(w) -> str:
    """按窗口内容判断状态，返回 'login' / 'chat' / 'unknown'。

    微信掉线后登录布局承载在 mmui::MainWindow 里（与已登录主窗口同一个
    类名），只比类名会把登录界面当主界面，所以有界遍历子孙节点看内容。
    """
    try:
        if w.ClassName == LOGIN_WINDOW_CLASS:
            return "login"
    except Exception:
        pass

    budget = [SCAN_MAX_NODES]
    found = {"login": False, "chat": False}

    def rec(ctrl, depth):
        if found["login"] or depth > SCAN_MAX_DEPTH or budget[0] <= 0:
            return
        try:
            children = list(ctrl.GetChildren())
        except Exception:
            return
        for child in children:
            if found["login"] or budget[0] <= 0:
                return
            budget[0] -= 1
            try:
                blob = f"{child.AutomationId or ''} {child.Name or ''}"
            except Exception:
                blob = ""
            if any(mk in blob for mk in LOGIN_TEXT_MARKERS):
                found["login"] = True
                return
            try:
                if child.Name in CHAT_UI_MARKERS:
                    found["chat"] = True
            except Exception:
                pass
            rec(child, depth + 1)

    try:
        rec(w, 0)
    except Exception:
        pass

    if found["login"]:
        return "login"
    if found["chat"]:
        return "chat"
    return "unknown"
