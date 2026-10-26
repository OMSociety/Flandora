"""把微信放到独立的 Desktop 上运行，从而让它不抢默认桌面的前台。

## 为什么需要这个

向微信投递输入（无论鼠标消息、`WM_CHAR` 还是 UIA 动作）都会让微信把它自己
激活到前台——这是微信自身的行为，逐机制实测见
`docs/known-limits-desktop.md`。

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

from .win32 import windows as win32_windows

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
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class DesktopError(Exception):
    """隔离桌面操作失败。消息面向终端用户。"""


#: 微信主窗口的类名与标题。图片查看器（标题「图片和视频」）与主窗口同进程同类名，
#: 只能靠标题区分；选错的后果是实况截图与转发点击都落到查看器上。
MAIN_WINDOW_CLASS = "Qt51514QWindowIcon"
MAIN_WINDOW_TITLES = ("微信", "Weixin")


def pick_main_window(windows: list[dict]) -> dict | None:
    """从候选窗口里挑主窗口：先按标题认，认不出来再挑最大的那个。"""
    titled = [w for w in windows if str(w.get("title", "")).strip() in MAIN_WINDOW_TITLES]
    pool = titled or windows
    if not pool:
        return None
    return max(pool, key=lambda w: w["width"] * w["height"])


class WeChatAlreadyRunning(DesktopError):
    """默认桌面上已有微信在跑，隔离实例会停在登录窗。"""


def weixin_pids() -> list[int]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        errors="ignore",
        creationflags=0x08000000,
    ).stdout
    pids = []
    for line in out.splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def default_desktop_has_wechat() -> bool:
    """默认桌面上是否真有微信窗口（有的话隔离实例进不去）。

    按**窗口**判断，不按进程名：`weixin_pids()` 是全系统扫描，会把本程序上次
    留在隔离桌面上的微信也算进来，重启时就会误判成「用户的微信开着」而拒绝启动。
    """
    try:
        return bool(win32_windows.enum_wechat_windows())
    except Exception as exc:  # noqa: BLE001
        log.warning("检查默认桌面上的微信窗口失败：%s", exc)
        return False


def quit_default_desktop_wechat() -> int:
    """结束默认桌面上的微信，返回结束掉的进程数。

    只由用户在 WebUI 上显式点击触发。用户自己开的微信不参与启动/退出流程，
    不会被静默杀掉——要腾出隔离实例的位置，必须由人来按这个按钮。
    """
    try:
        pids = [w["pid"] for w in win32_windows.enum_wechat_windows()]
    except Exception as exc:  # noqa: BLE001
        log.warning("查找默认桌面上的微信失败：%s", exc)
        return 0
    killed = 0
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=0x08000000,
            )
            killed += 1
            log.info("已按用户要求结束默认桌面上的微信（pid=%d）", pid)
        except Exception as exc:  # noqa: BLE001
            log.warning("结束默认桌面上的微信失败（pid=%d）：%s", pid, exc)
    return killed


class IsolatedDesktop:
    """一个独立的 Desktop，微信可在其上运行而不打扰默认桌面。"""

    def __init__(self, name: str = "FlandoraSandbox") -> None:
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
            raise DesktopError(f"创建隔离桌面失败（错误码 {ctypes.get_last_error()}）")
        self.handle = h
        log.info("已创建隔离桌面 %s", self.name)

    def open_existing(self) -> bool:
        """打开同名桌面，但不启动任何程序——用于接管上一次运行留下的微信。"""
        if self.handle:
            return True
        h = _user32.OpenDesktopW(self.name, 0, False, DESKTOP_ALL_ACCESS)
        if not h:
            return False
        self.handle = h
        return True

    @property
    def is_open(self) -> bool:
        """桌面句柄是否已就绪（含接管来的桌面，此时 pid 为 0）。"""
        return bool(self.handle)

    def launch(self, exe: str) -> int:
        """在该桌面上启动程序，返回 pid。"""
        self.open()
        if default_desktop_has_wechat():
            raise WeChatAlreadyRunning(
                "默认桌面上已有微信在运行。隔离模式要求由本程序启动微信，"
                "可以在「微信画面」页点「结束桌面上的微信」把它关掉后重试。"
            )
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = f"WinSta0\\{self.name}"
        pi = _PROCESS_INFORMATION()
        ok = _kernel32.CreateProcessW(
            exe, None, None, None, False, 0, None, None, ctypes.byref(si), ctypes.byref(pi)
        )
        if not ok:
            raise DesktopError(
                f"在隔离桌面上启动 {os.path.basename(exe)} 失败（错误码 {ctypes.get_last_error()}）"
            )
        self._proc = pi
        self.pid = pi.dwProcessId
        self._started = True
        log.info("已在隔离桌面启动 %s（pid=%d）", os.path.basename(exe), self.pid)
        return self.pid

    def close(self) -> None:
        """结束本桌面上的微信并关闭桌面句柄。

        接管来的实例没有 `_proc`，按该桌面上枚举到的微信窗口取 pid 再结束，
        否则「结束微信」只会关掉句柄、微信继续在后台跑。
        """
        pids: set[int] = set()
        if self._started and self.pid:
            pids = {self.pid}
        elif self.handle:
            pids = self._pids()
        for pid in pids:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=0x08000000,
                )
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

    def close_quietly(self) -> None:
        """只放掉桌面句柄，不动上面的进程（接管探测失败时用）。"""
        if self.handle:
            try:
                _user32.CloseDesktop(self.handle)
            except Exception:
                pass
            self.handle = None

    # ---------- 窗口 ----------

    def _pids(self) -> set[int]:
        """该桌面上微信的进程号。

        自己启动的知道 pid；接管来的不知道，就从该桌面上真实的微信窗口反推
        ——只取这个桌面上的，默认桌面上如果有别的微信也不会被算进来。
        """
        if self.pid:
            return {self.pid}
        if not self.handle:
            return set()
        try:
            return {w["pid"] for w in win32_windows.enum_wechat_windows(self.name)}
        except Exception as exc:  # noqa: BLE001
            log.warning("枚举 %s 上的微信窗口失败：%s", self.name, exc)
            return set()

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
                found.append(
                    {
                        "hwnd": hwnd,
                        "class": cls.value,
                        "title": txt.value,
                        "width": r.right - r.left,
                        "height": r.bottom - r.top,
                        "left": r.left,
                        "top": r.top,
                    }
                )
            return True

        _user32.EnumDesktopWindows(self.handle, cb, 0)
        return found

    def main_window(self, timeout: float = 30.0) -> dict | None:
        """等到主窗口出现（登录页也算），返回窗口信息。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidates = [
                w
                for w in self.windows()
                if w["class"] == MAIN_WINDOW_CLASS and w["width"] > 50 and w["height"] > 50
            ]
            pick = pick_main_window(candidates)
            if pick is not None:
                return pick
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
        bi.bmiHeader.biHeight = -height  # 负数 = 自上而下
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

_EXE_NAMES = ("Weixin.exe", "WeChat.exe")
# 注册表与安装目录里用到的产品名
_PRODUCT_NAMES = ("Weixin", "WeChat")
# 每个盘符下可能装 Tencent 系产品的两级目录名
_PROGRAM_DIRS = ("Program Files", "Program Files (x86)")


def _drive_roots() -> list:
    """所有存在的盘符根，C 盘排最前（绝大多数安装在这里）。"""
    found = [f"{ch}:\\" for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{ch}:\\")]
    return sorted(found, key=lambda r: (r[0] != "C", r))


def _registry_dirs() -> list:
    """从注册表取微信安装目录。

    实测（微信 4.1.15.56）：
      `HKCU\\SOFTWARE\\Tencent\\Weixin` 的值 `InstallPath` 记的就是安装目录（装到非 C 盘也照记）

    老版本写在哪个键下没有实机可验，所以 hive、键路径、值名都多试几个，命中即止。
    取不到不算错——后面还有常见位置与逐盘扫描兜着。
    """
    try:
        import winreg
    except ImportError:
        return []
    dirs = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for prefix in ("SOFTWARE", r"SOFTWARE\WOW6432Node"):
            for product in _PRODUCT_NAMES:
                try:
                    with winreg.OpenKey(hive, rf"{prefix}\Tencent\{product}") as key:
                        for value in ("InstallPath", "InstallDir", "Path"):
                            try:
                                path, _ = winreg.QueryValueEx(key, value)
                            except OSError:
                                continue
                            if isinstance(path, str) and path:
                                dirs.append(path)
                except OSError:
                    continue
    return dirs


def find_weixin_exe(configured: str = "") -> str:
    """定位微信可执行文件。

    依次试：配置 → 环境变量 `WECHAT_EXE` → 注册表记录的安装目录 → 常见安装位置 →
    逐盘扫 `Program Files` 与 `Program Files (x86)`（两条都扫，微信有装在 32 位
    目录下的，也有装在非 C 盘的）。

    `Weixin.exe` 只是启动器，真正的版本目录里是 `Weixin.dll`——要启动的就是启动器
    本身，所以找它是对的。
    """
    if configured and os.path.isfile(configured):
        return configured
    env = os.environ.get("WECHAT_EXE", "")
    if env and os.path.isfile(env):
        return env

    dirs = _registry_dirs() + list(_CANDIDATE_DIRS)
    for root in _drive_roots():
        for prog in _PROGRAM_DIRS:
            for product in _PRODUCT_NAMES:
                dirs.append(os.path.join(root, prog, "Tencent", product))

    for d in dirs:
        for n in _EXE_NAMES:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return ""


_EXE_CACHE: dict[str, str] = {}


def cached_weixin_exe(configured: str = "") -> str:
    """带缓存的 `find_weixin_exe`，供 WebUI 频繁查询「本机到底有没有微信」。

    搜索要扫注册表与各盘 Program Files，单次要几百毫秒到数秒；状态查询每 2 秒
    一次，不能每次重扫。按配置值分键缓存——`wechat_exe` 改了立刻重新找。
    """
    key = configured or ""
    if key not in _EXE_CACHE:
        _EXE_CACHE[key] = find_weixin_exe(key)
    return _EXE_CACHE[key]


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
            from . import config

            return bool(getattr(config, "ISOLATED_DESKTOP", False))
        except Exception:
            return False

    def desktop_name(self) -> str:
        try:
            from . import config

            return getattr(config, "ISOLATED_DESKTOP_NAME", "FlandoraSandbox")
        except Exception:
            return "FlandoraSandbox"

    def start(self) -> bool:
        """按需启动隔离实例。已启动则直接返回 True（幂等）。

        返回时窗口已经出现（或已等满 60 秒）——调用方紧接着就要找窗口。
        """
        with self._lock:
            if self._desk and self._desk.is_open:
                desk = self._desk
            else:
                desk = self._adopt_existing()
                if desk is None:
                    try:
                        from . import config

                        exe = find_weixin_exe(getattr(config, "WECHAT_EXE", ""))
                    except Exception:
                        exe = find_weixin_exe()
                    if not exe:
                        self._error = (
                            "找不到微信可执行文件。请在 config.json 里把 "
                            "wechat_exe 填成 Weixin.exe 的完整路径。"
                        )
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

    def _adopt_existing(self) -> IsolatedDesktop | None:
        """接管上一次运行留在隔离桌面上的微信。

        桥接退出时不结束微信（否则每次重启都要重新扫码登录，微信会重新生成
        数据库口令，已存的密钥随之作废），所以启动时先看同名桌面上有没有现成的
        窗口；有就直接用，登录状态与密钥继续有效。
        """
        desk = IsolatedDesktop(self.desktop_name())
        try:
            if not desk.open_existing():
                return None
            win = desk.main_window(timeout=0.5)
        except Exception as exc:  # noqa: BLE001
            log.warning("检查隔离桌面上是否已有微信失败：%s", exc)
            return None
        if not win:
            # 桌面开着但上面没有微信：只放掉句柄，别动桌面
            desk.close_quietly()
            return None
        self._desk = desk
        self._error = ""
        log.info("已接管隔离桌面上正在运行的微信（hwnd=%d），无需重新登录", win["hwnd"])
        return desk

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
        if desk and desk.is_open:
            try:
                return desk.main_window(timeout=0.1)
            except Exception:
                return None
        try:
            wins = win32_windows.enum_wechat_windows()
        except Exception:
            return None
        if not wins:
            return None
        best = max(
            wins, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1])
        )
        return {
            "hwnd": best["hwnd"],
            "cls": best["class_name"],
            "title": best["title"],
            "left": best["rect"][0],
            "top": best["rect"][1],
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
            "exe_found": False,
        }
        try:
            from . import config

            found = cached_weixin_exe(getattr(config, "WECHAT_EXE", ""))
            info["exe_found"] = bool(found)
            info["exe"] = info["exe"] or found
        except Exception:
            pass
        try:
            from . import config

            if getattr(config, "ISOLATED_DESKTOP", False):
                info["running"] = bool(desk and desk.is_open)
            else:
                # 非隔离模式：微信由用户自己开，看默认桌面上有没有
                info["running"] = bool(weixin_pids())
        except Exception:
            info["running"] = bool(weixin_pids())
        win = self.window() if (desk and desk.is_open) else None
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
            state = win32_windows.state_from_handle(win["hwnd"])
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
        from .wechat_account import current_account

        acc = current_account()
        return getattr(acc, "wxid", "") or ""
    except Exception:
        return ""


_manager: DesktopManager | None = None
_manager_lock = threading.Lock()


def manager() -> DesktopManager:
    """取进程级管理器（首次调用时创建）。"""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DesktopManager()
        return _manager
