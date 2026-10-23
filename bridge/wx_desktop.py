"""把微信放到独立的 Desktop 上运行，从而让它不抢默认桌面的前台。

## 为什么需要这个

向微信投递输入（无论鼠标消息、`WM_CHAR` 还是 UIA 动作）都会让微信把它自己
激活到前台——这是微信自身的行为，逐机制实测见
`docs/known-limits-nonfg.md`。

但「抢前台」是**桌面局部**的：每个 Desktop 有自己的前台窗口。把微信放到另一个
Desktop 上，它升到前台只影响那个桌面，用户所在的 Default 桌面不受影响。

## 代价（必须写清楚）

1. 微信必须由本程序启动，不能是用户双击打开的那个——Windows 上微信单实例互斥，
   Default 桌面上已有实例时，隔离实例只会停在登录窗。
2. 隔离桌面用户看不见，所以登录得靠**截图**：本模块提供 `capture()`，WebUI 把
   登录页画面显示出来，用户扫码或用手机确认。实测该界面文案为「需在手机上完成登录」。
3. 隔离桌面上 UIA 的 `Invoke` 不生效（Qt 在窗口未激活时不响应），点击要走
   `PostMessage` 投鼠标消息。

## 用法

    d = IsolatedDesktop()          # 造桌面
    d.launch(wechat_exe)           # 在其上启动微信
    hwnd = d.find_main_window()    # 找主窗（登录页也行）
    d.capture(hwnd, path)          # 截图给用户看
    d.click(hwnd, x, y)            # 投鼠标消息（客户区坐标）
    d.close()                      # 清理
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import threading
import time
from ctypes import wintypes

log = logging.getLogger("wx_desktop")

DESKTOP_ALL_ACCESS = 0x000F01FF
PW_RENDERFULLCONTENT = 0x00000002
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class DesktopError(Exception):
    """隔离桌面操作失败。消息面向终端用户。"""


class WeChatAlreadyRunning(DesktopError):
    """默认桌面上已有微信在跑，隔离实例会停在登录窗。"""


def weixin_pids() -> list[int]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, errors="ignore",
        creationflags=0x08000000).stdout
    pids = []
    for line in out.splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def default_desktop_has_wechat() -> bool:
    """默认桌面上是否有微信在跑（有的话隔离实例进不去）。"""
    return bool(weixin_pids())


class IsolatedDesktop:
    """一个独立的 Desktop，微信可在其上运行而不打扰默认桌面。"""

    def __init__(self, name: str = "WeBotSandbox") -> None:
        self.name = name
        self.handle = None
        self.pid = 0
        self._proc = _PROCESS_INFORMATION()
        self._started = False

    # ---------- 生命周期 ----------

    def open(self) -> None:
        if self.handle:
            return
        h = _user32.CreateDesktopW(self.name, None, None, 0, DESKTOP_ALL_ACCESS, None)
        if not h:
            raise DesktopError(
                f"创建隔离桌面失败（错误码 {ctypes.get_last_error()}）")
        self.handle = h
        log.info("已创建隔离桌面 %s", self.name)

    def launch(self, exe: str) -> int:
        """在该桌面上启动程序，返回 pid。"""
        self.open()
        if default_desktop_has_wechat():
            raise WeChatAlreadyRunning(
                "默认桌面上已有微信在运行。隔离模式要求由本程序启动微信，"
                "请先完全退出微信再启动 bridge。")
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = f"WinSta0\\{self.name}"
        pi = _PROCESS_INFORMATION()
        ok = _kernel32.CreateProcessW(
            exe, None, None, None, False, 0, None, None,
            ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            raise DesktopError(
                f"在隔离桌面上启动 {os.path.basename(exe)} 失败"
                f"（错误码 {ctypes.get_last_error()}）")
        self._proc = pi
        self.pid = pi.dwProcessId
        self._started = True
        log.info("已在隔离桌面启动 %s（pid=%d）", os.path.basename(exe), self.pid)
        return self.pid

    def close(self) -> None:
        """结束本次启动的进程并关闭桌面。只动自己启动的。"""
        if self._started and self.pid:
            try:
                subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"],
                               capture_output=True, creationflags=0x08000000)
            except Exception as exc:  # noqa: BLE001
                log.warning("结束隔离实例失败：%s", exc)
        for h in (self._proc.hThread, self._proc.hProcess):
            try:
                if h:
                    _kernel32.CloseHandle(h)
            except Exception:
                pass
        self._proc = _PROCESS_INFORMATION()
        if self.handle:
            try:
                _user32.CloseDesktop(self.handle)
            except Exception:
                pass
            self.handle = None
        self._started = False
        self.pid = 0

    # ---------- 窗口 ----------

    def _pids(self) -> set[int]:
        """本次启动的微信及其子进程。"""
        return {self.pid} if self.pid else set()

    def windows(self) -> list[dict]:
        """该桌面上的窗口清单（只含本次启动的进程）。"""
        if not self.handle:
            return []
        pids = self._pids()
        found: list[dict] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _l):
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                cls = ctypes.create_unicode_buffer(160)
                txt = ctypes.create_unicode_buffer(160)
                _user32.GetClassNameW(hwnd, cls, 160)
                _user32.GetWindowTextW(hwnd, txt, 160)
                r = wintypes.RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(r))
                found.append({
                    "hwnd": hwnd, "class": cls.value, "title": txt.value,
                    "width": r.right - r.left, "height": r.bottom - r.top,
                    "left": r.left, "top": r.top,
                })
            return True

        _user32.EnumDesktopWindows(self.handle, cb, 0)
        return found

    def main_window(self, timeout: float = 30.0) -> dict | None:
        """等到主窗口出现（登录页也算），返回窗口信息。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            wins = [w for w in self.windows()
                    if w["class"] == "Qt51514QWindowIcon"
                    and w["width"] > 50 and w["height"] > 50]
            if wins:
                wins.sort(key=lambda w: -(w["width"] * w["height"]))
                return wins[0]
            time.sleep(0.5)
        return None

    # ---------- 截图与点击 ----------

    def capture(self, hwnd: int, width: int, height: int) -> bytes | None:
        """截取窗口内容，返回 BGRA 像素。

        用 `PrintWindow` 而非 `BitBlt`：前者由消息驱动，窗口不可见也能渲染；
        后者依赖屏幕合成，在隔离桌面上拿不到内容（实测返回全 0）。
        """
        hdc = _user32.GetWindowDC(hwnd)
        if not hdc:
            return None
        mem = _gdi32.CreateCompatibleDC(hdc)
        bmp = _gdi32.CreateCompatibleBitmap(hdc, width, height)
        old = _gdi32.SelectObject(mem, bmp)
        bi = _BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = width
        bi.bmiHeader.biHeight = -height      # 负数 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        try:
            ok = _user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
            buf = ctypes.create_string_buffer(width * height * 4)
            got = _gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(bi), 0)
            if not ok or not got:
                return None
            return buf.raw
        finally:
            _gdi32.SelectObject(mem, old)
            _gdi32.DeleteObject(bmp)
            _gdi32.DeleteDC(mem)
            _user32.ReleaseDC(hwnd, hdc)

    def capture_png(self, hwnd: int, width: int, height: int) -> bytes | None:
        """截图为 PNG 字节，供 WebUI 直接返回。"""
        raw = self.capture(hwnd, width, height)
        if raw is None:
            return None
        try:
            from PIL import Image
        except ImportError:
            log.warning("没有 PIL，无法编码 PNG；退回原始像素")
            return raw
        import io
        img = Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", 0, 1)
        out = io.BytesIO()
        img.convert("RGB").save(out, format="PNG")
        return out.getvalue()

    def click(self, hwnd: int, client_x: int, client_y: int) -> bool:
        """向窗口投递一次左键点击（客户区坐标）。

        隔离桌面上 UIA 的 `InvokePattern.Invoke()` 会报告成功但界面不动——Qt 在
        窗口未激活时不处理 UIA 触发的动作。投鼠标消息这条路径实测有效。
        """
        lp = (client_y << 16) | (client_x & 0xFFFF)
        try:
            _user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
            time.sleep(0.05)
            _user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
            time.sleep(0.05)
            _user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
        except Exception as exc:  # noqa: BLE001
            log.warning("投递点击失败：%s", exc)
            return False
        return True


# ====================================================================
# 微信可执行文件定位
# ====================================================================

# 常见安装位置。微信 4.x 换成了 Weixin.exe，老版本是 WeChat.exe。
_CANDIDATE_DIRS = (
    r"C:\Program Files\Tencent\Weixin",
    r"C:\Program Files (x86)\Tencent\Weixin",
    r"C:\Program Files\Tencent\WeChat",
    r"C:\Program Files (x86)\Tencent\WeChat",
)


def find_weixin_exe(configured: str = "") -> str:
    """定位微信可执行文件。配置优先，其次环境变量，最后扫常见安装位置。

    `Weixin.exe` 只是启动器，真正的版本目录里是 `Weixin.dll`——但我们要启动的
    就是启动器本身，所以找它是对的。
    """
    if configured and os.path.isfile(configured):
        return configured
    env = os.environ.get("WECHAT_EXE", "")
    if env and os.path.isfile(env):
        return env
    names = ("Weixin.exe", "WeChat.exe")
    for d in _CANDIDATE_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    # 装到非默认盘时按盘符扫一层 Tencent 目录
    for letter in "CDEFGH":
        for sub in ("Tencent\\Weixin", "Tencent\\WeChat"):
            for n in names:
                p = os.path.join(f"{letter}:\\Program Files", sub, n)
                if os.path.isfile(p):
                    return p
    return ""


# ====================================================================
# 进程级管理器（供 bridge 主流程与 WebUI 共用）
# ====================================================================


class DesktopManager:
    """隔离桌面的进程级唯一入口。

    主流程用它启动/接管微信，WebUI 用它取登录截图与转发点击。两者拿到的必须是
    同一个 `IsolatedDesktop`——桌面句柄与 pid 都挂在实例上，各建一个会互相抢。
    """

    def __init__(self) -> None:
        self._desk: IsolatedDesktop | None = None
        self._lock = threading.Lock()
        self._error = ""
        self._exe = ""

    # ---- 生命周期 ----

    def enabled(self) -> bool:
        try:
            import config
            return bool(getattr(config, "ISOLATED_DESKTOP", False))
        except Exception:
            return False

    def desktop_name(self) -> str:
        try:
            import config
            return getattr(config, "ISOLATED_DESKTOP_NAME", "WeBotSandbox")
        except Exception:
            return "WeBotSandbox"

    def start(self) -> bool:
        """按需启动隔离实例。已启动则直接返回 True（幂等）。

        返回时窗口已经出现（或已等满 60 秒）——调用方紧接着就要找窗口。
        """
        with self._lock:
            if self._desk and self._desk.pid:
                desk = self._desk
            else:
                try:
                    import config
                    exe = find_weixin_exe(getattr(config, "WECHAT_EXE", ""))
                except Exception:
                    exe = find_weixin_exe()
                if not exe:
                    self._error = ("找不到微信可执行文件。请在 config.json 里把 "
                                   "wechat_exe 填成 Weixin.exe 的完整路径。")
                    log.error(self._error)
                    return False
                self._exe = exe
                try:
                    desk = IsolatedDesktop(self.desktop_name())
                    desk.launch(exe)
                except DesktopError as exc:
                    # WeChatAlreadyRunning 是它的子类，消息已面向终端用户写好
                    self._error = str(exc)
                    log.error("隔离桌面启动失败：%s", exc)
                    return False
                self._desk = desk
                self._error = ""
                log.info("隔离桌面已就绪（%s，pid=%d）", self.desktop_name(), desk.pid)
        # 在锁外等窗口：start() 可能要等几十秒，持锁会把 WebUI 的状态查询卡住
        if desk.main_window(timeout=60.0) is None:
            log.warning("隔离桌面上 60 秒内没等到微信窗口，微信可能启动失败")
        return True

    def stop(self) -> None:
        with self._lock:
            if self._desk:
                self._desk.close()
                self._desk = None

    # ---- 查询 ----

    def window(self) -> dict | None:
        """当前微信主窗口（登录页也算）。

        隔离模式看隔离桌面，非隔离模式看用户自己那个桌面——后者只为状态展示，
        截图与点击不提供（用户本来就看得见自己的微信）。
        """
        with self._lock:
            desk = self._desk
        if desk and desk.pid:
            try:
                return desk.main_window(timeout=0.1)
            except Exception:
                return None
        try:
            from uia_sender import enum_wechat_windows
        except ImportError:
            return None
        try:
            wins = enum_wechat_windows()
        except Exception:
            return None
        if not wins:
            return None
        best = max(wins, key=lambda w: (w["rect"][2] - w["rect"][0]) *
                  (w["rect"][3] - w["rect"][1]))
        return {
            "hwnd": best["hwnd"],
            "cls": best["class_name"],
            "title": best["title"],
            "left": best["rect"][0], "top": best["rect"][1],
            "width": best["rect"][2] - best["rect"][0],
            "height": best["rect"][3] - best["rect"][1],
        }

    def status(self) -> dict:
        desk = self._desk
        info: dict = {
            "enabled": self.enabled(),
            "running": False,
            "logged_in": False,
            "wxid": "",
            "width": 0,
            "height": 0,
            "error": self._error,
            "desktop": self.desktop_name(),
            "exe": self._exe,
        }
        try:
            import config
            if getattr(config, "ISOLATED_DESKTOP", False):
                info["running"] = bool(desk and desk.pid)
            else:
                # 非隔离模式：微信由用户自己开，看默认桌面上有没有
                info["running"] = bool(weixin_pids())
        except Exception:
            info["running"] = bool(weixin_pids())
        win = self.window() if (desk and desk.pid) else None
        if win:
            info["width"] = win["width"]
            info["height"] = win["height"]
        try:
            info["wxid"] = _current_wxid()
        except Exception:
            pass
        # 「已登录」按窗口内容判定，不看有没有历史登录记录——微信停在登录页时
        # 磁盘上的旧登录记录依然在，按记录判断会把登录页报成已登录。
        if win:
            state = _window_state(win["hwnd"])
            info["state"] = state
            info["logged_in"] = state == "chat"
        else:
            info["state"] = "absent"
            info["logged_in"] = False
        return info

    # ---- 截图与点击 ----

    def screenshot_png(self) -> bytes | None:
        win = self.window()
        if not win:
            return None
        with self._lock:
            desk = self._desk
        if not desk:
            return None
        return desk.capture_png(win["hwnd"], win["width"], win["height"])

    def click(self, client_x: int, client_y: int) -> bool:
        """把 WebUI 上的点击转发到隔离桌面上的微信窗口。"""
        win = self.window()
        if not win:
            return False
        if not (0 <= client_x < win["width"] and 0 <= client_y < win["height"]):
            return False
        with self._lock:
            desk = self._desk
        if not desk:
            return False
        return desk.click(win["hwnd"], client_x, client_y)


def _current_wxid() -> str:
    """当前登录账号的 wxid，取不到返回空串。"""
    try:
        from wechat_account import current_account
        acc = current_account()
        return getattr(acc, "wxid", "") or ""
    except Exception:
        return ""


def _window_state(hwnd: int) -> str:
    """窗口状态判定。真身是 `UiaSender._window_state`，这里只做懒加载转发。

    多处需要这个判断（发送前、WebUI 状态），而它依赖一整套 LOGIN_TEXT_MARKERS
    等常量，复制一份迟早会漂移。
    """
    try:
        from uia_sender import UiaSender
    except ImportError:
        return "unknown"
    try:
        return UiaSender.state_from_handle(hwnd)
    except Exception:
        return "unknown"


_manager: DesktopManager | None = None
_manager_lock = threading.Lock()


def manager() -> DesktopManager:
    """取进程级管理器（首次调用时创建）。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DesktopManager()
        return _manager
