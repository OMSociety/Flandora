"""
uia_sender.py — 微信消息发送器
=================================================================

方案：把输入以窗口消息定向投递给微信线程，不移动真实光标。

- AttachThreadInput 共享输入队列 + SetActiveWindow，使微信线程能处理投递
  进来的鼠标/键盘消息
- 投递输入会使微信窗口升到前台：微信收到投递进来的鼠标或键盘消息就升前台，
  本模块无法避免（实测 SetFocus/SetValue/WM_CHAR/鼠标消息皆然）。因此发送通道
  由 allow_foreground_steal 开关控制，默认关闭；关闭时发送直接失败并记日志
- 开启后每条消息发完立刻把前台还给发送前的前台窗口（实测 3-11 毫秒），
  用户看到的是一次短暂切换，而不是前台被微信占住
- 联系人切换优先点会话列表项，列表里没有时才退回顶部搜索框
- 文本用 WM_CHAR 逐字符输入（中文可靠，避免剪贴板/输入法问题）
- 图片/文件用剪贴板 + 伪造 Ctrl 键态的 Ctrl+V
- 发送前回读输入框校验，发送后按会话摘要行确认；任一步不符即报失败
- 不移动窗口、不改动窗口尺寸与位置、不改动置顶状态

依赖:
  pip install uiautomation
  发送图片/文件需要 PowerShell (Windows 自带)
"""

import ctypes
import logging
import os
import random
import subprocess
import threading
import time
from ctypes import wintypes

try:
    from . import config
except Exception:
    config = None

log = logging.getLogger("ob11-bridge")

# Windows 消息 / 虚拟键码
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_END = 0x23
VK_ESCAPE = 0x1B
VK_V = 0x56

# ShowWindow 命令：还原并激活窗口
SW_RESTORE = 9

# WM_KEYUP 的 lParam：低位为重复次数 1，其余位标明键已抬起
KEYUP_LPARAM = 0xC0000001


class BaseSender:
    """消息发送器基类"""
    def send_text(self, contact: str, text: str) -> bool:
        raise NotImplementedError

    def send_image(self, contact: str, image_path: str) -> bool:
        raise NotImplementedError


# ================================================================
# 微信窗口枚举（多开识别用，纯 ctypes，不依赖 uiautomation）
# ================================================================


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
        ct, et, kt, ut = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if kernel32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(et),
                                    ctypes.byref(kt), ctypes.byref(ut)):
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
            if not (("QWindowIcon" in class_name) or
                    proc.lower() in ("wechat.exe", "weixin.exe")):
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            results.append({
                "hwnd": hwnd,
                "pid": pid.value,
                "title": title,
                "class_name": class_name,
                "process": proc,
                "start_time": _process_start_time(pid.value),
                "iconic": bool(user32.IsIconic(hwnd)),
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
            })
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
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                        ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                        ("dwTimeout", wintypes.DWORD)]

        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                          0x0003 | 0x000C, count, 0)  # FLASHW_ALL | FLASHW_TIMERNOFG
        return bool(user32.FlashWindowEx(ctypes.byref(info)))
    except Exception:
        return False


class UiaSender(BaseSender):
    """微信消息发送器：把输入以窗口消息定向投递给微信线程。"""

    WECHAT_TITLES = ["微信", "WeChat", "Weixin"]

    # 微信登录界面的窗口类名。未登录时微信只存在这个窗口，而它的标题同样是
    # 「微信」，仅按标题查找会误绑到登录界面：后续 Ctrl+F / 粘贴 / 回车都会
    # 打进登录界面，并在发送后假报成功（消息其实从未发出）。
    LOGIN_WINDOW_CLASS = "mmui::LoginWindow"

    # 会话列表项 / 输入框 / 搜索框 / 搜索结果项 / 搜索弹层（微信 4.1.15.56 实测类名）。
    # 会话项的名字是多行文本：会话名 / 最后一条消息摘要 / 时间。
    SESSION_CELL_CLASS = "mmui::ChatSessionCell"
    INPUT_FIELD_CLASS = "mmui::ChatInputField"
    SEARCH_FIELD_CLASS = "mmui::XSearchField"
    SEARCH_RESULT_CLASS = "mmui::SearchContentCellView"
    # 搜索结果项实测类名是 mmui::XTableCell（搜狗网络结果与「功能」动作项也是
    # 同一个类名），保留旧类名以兼容其它版本。
    SEARCH_RESULT_CLASSES = ("mmui::XTableCell", "mmui::SearchContentCellView")
    SEARCH_POPOVER_CLASS = "mmui::SearchContentPopover"
    # 弹层里「功能」分区的标题：它下面会出现与联系人同名的**动作项**
    # （如「发送文件」），不是会话，必须排除。
    SEARCH_ACTION_HEADER = "功能"

    # 图片贴进富文本输入框后，ValuePattern 读到的是嵌入对象占位符 U+FFFC。
    IMAGE_MARK = "\ufffc"

    # 控件树遍历预算：会话多时微信 UI 树可达数百节点。
    WALK_MAX_DEPTH = 30
    WALK_MAX_NODES = 4000

    # 抑制子进程控制台窗口。不能用 PowerShell 的 -WindowStyle Hidden：
    # Windows PowerShell 5.1 把它与 -Command 内联脚本组合时会以 -1 退出，
    # 剪贴板写不进去（图片/文件因此发不出去）。
    CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    # 窗口识别的首选依据是进程名：微信 4.1.x 通过 UIA 暴露的窗口 Name 会在
    # 「微信」/「Weixin」之间变化（同一 hwnd 实测两种取值），标题匹配不可靠。
    WECHAT_EXE_NAMES = ("weixin.exe", "wechat.exe")

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

    # 无障碍树为空的判据。微信被关到托盘后又被重新显示时，UIA 树只剩
    # MainWindow 与其渲染子窗两级；这与「界面尚未渲染完」不同，等多久都不会恢复。
    A11Y_EMPTY_NODES = 4

    _user32 = None

    @classmethod
    def _user32_inst(cls):
        if cls._user32 is None:
            cls._user32 = ctypes.windll.user32
        return cls._user32

    def __init__(self, search_enabled: bool = True,
                 allow_foreground_steal: bool = False,
                 isolated_desktop: str = ""):
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._auto = None
        self._ready = False

        # 微信窗口
        self._window = None
        self._hwnd = 0

        # 线程附着状态（共享输入队列用；必须成对出现）
        self._attached = False
        self._tid_me = 0
        self._tid_wx = 0

        # 最近联系人缓存（相同目标跳过切换，快速发送）
        self._last_contact = ""
        # 本次发送开始时的前台窗口（0 = 无需还原）
        self._fg_before = 0

        self.search_enabled = search_enabled
        # 是否允许发送时把微信窗口提到前台。关闭时发送通道整体不可用。
        self.allow_foreground_steal = bool(allow_foreground_steal)
        # 非空时表示微信跑在指定名字的独立 Desktop 上：窗口枚举改走
        # EnumDesktopWindows，前台提升只影响那个桌面，用户所在的桌面不受打扰。
        self.isolated_desktop = isolated_desktop or ""

        self._init()

    # ================================================================
    # 初始化
    # ================================================================

    def _init(self):
        """初始化并定位微信主窗口句柄"""
        try:
            import uiautomation as auto
            self._auto = auto
        except ImportError:
            log.error("请先安装 uiautomation: pip install uiautomation")
            return

        log.info("正在搜索微信窗口...")
        self._find_window()
        if self._window:
            log.info(f"微信窗口: '{self._window.Name}' ClassName={self._window.ClassName}")
            self._hwnd = self._window.NativeWindowHandle
            self._ready = True

    @staticmethod
    def _exe_name(pid: int) -> str:
        """取 pid 对应的可执行文件名（小写，含扩展名）。失败返回空串。"""
        if not pid:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        try:
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
        except Exception:
            return ""
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        except Exception:
            return ""
        finally:
            kernel32.CloseHandle(handle)

    def _is_wechat_process(self, w) -> bool:
        try:
            return self._exe_name(w.ProcessId) in self.WECHAT_EXE_NAMES
        except Exception:
            return False

    @classmethod
    def state_from_handle(cls, hwnd: int) -> str:
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
        return cls._window_state(ctrl)

    @classmethod
    def _window_state(cls, w) -> str:
        """按窗口内容判断状态，返回 'login' / 'chat' / 'unknown'。

        微信掉线后登录布局承载在 mmui::MainWindow 里（与已登录主窗口同一个
        类名），只比类名会把登录界面当主界面，所以有界遍历子孙节点看内容。
        """
        try:
            if w.ClassName == cls.LOGIN_WINDOW_CLASS:
                return "login"
        except Exception:
            pass

        budget = [cls.SCAN_MAX_NODES]
        found = {"login": False, "chat": False}

        def rec(ctrl, depth):
            if found["login"] or depth > cls.SCAN_MAX_DEPTH or budget[0] <= 0:
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
                if any(mk in blob for mk in cls.LOGIN_TEXT_MARKERS):
                    found["login"] = True
                    return
                try:
                    if child.Name in cls.CHAT_UI_MARKERS:
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

    def _find_window_isolated(self) -> bool:
        """在隔离桌面上定位微信主窗口。

        两条路都只看当前桌面、在隔离桌面上看不见微信：`EnumWindows` 与 UIA 的
        `GetRootControl()`。所以先用 `EnumDesktopWindows` 取 hwnd，再用
        `ControlFromHandle` 跨桌面拿 UIA 控件（实测可用）。
        """
        auto = self._auto
        cands = enum_wechat_windows(self.isolated_desktop)
        if not cands:
            log.error("隔离桌面 %s 上没有找到微信窗口，微信是否已启动？",
                      self.isolated_desktop)
            return False
        # 主窗口面积最大，优先试
        cands.sort(key=lambda c: -((c["rect"][2] - c["rect"][0]) *
                                   (c["rect"][3] - c["rect"][1])))
        for info in cands:
            try:
                w = auto.ControlFromHandle(info["hwnd"])
                if not w or not w.Exists(0.3):
                    continue
                if self._window_state(w) == "login":
                    log.error("隔离桌面上的微信停在登录界面，请先在 WebUI 里完成登录")
                    return False
                self._window = w
                self._hwnd = info["hwnd"]
                log.info("已绑定隔离桌面 %s 上的微信窗口 hwnd=%d",
                         self.isolated_desktop, info["hwnd"])
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("绑定隔离窗口 hwnd=%s 失败：%s", info["hwnd"], exc)
        log.error("隔离桌面上没有找到可用的已登录微信窗口")
        return False

    def _find_window(self) -> bool:
        """定位微信**已登录主窗口**：优先绑定 config.wechat_hwnd 指定的窗口（多开场景），
        未配置或已失效则回退到自动查找。

        查找顺序：按进程名（Weixin.exe/WeChat.exe）挑顶层窗口；识别不到进程时退回
        标题匹配。登录界面一律不绑定——否则模拟按键会打进登录界面并假报发送成功。

        隔离桌面模式下改走 `_find_window_isolated()`：那上面的窗口在
        `GetRootControl()` 的树里根本不存在。
        """
        if self.isolated_desktop:
            return self._find_window_isolated()
        auto = self._auto
        root = auto.GetRootControl()
        children = list(root.GetChildren())

        def _usable(w) -> bool:
            try:
                return w.ClassName not in ("Chrome_WidgetWin_1", "CabinetWClass")
            except Exception:
                return False

        target = 0
        try:
            target = int(getattr(config, "WECHAT_HWND", 0) or 0) if config else 0
        except (TypeError, ValueError):
            target = 0

        if target:
            for w in children:
                try:
                    if w.NativeWindowHandle == target and w.Exists(0.1):
                        if self._window_state(w) == "login":
                            log.error(f"配置的微信窗口 hwnd={target} 是登录界面，微信尚未登录")
                            return False
                        self._window = w
                        log.info(f"使用配置的微信窗口 hwnd={target}")
                        return True
                except Exception:
                    continue
            log.warning(f"配置的微信窗口 hwnd={target} 已失效，回退自动查找")

        by_process = []
        titled = []
        for w in children:
            if not _usable(w):
                continue
            if self._is_wechat_process(w):
                by_process.append(w)
                continue
            try:
                if any(kw in w.Name for kw in self.WECHAT_TITLES):
                    titled.append(w)
            except Exception:
                continue

        candidates = by_process or titled
        login_window = None
        unknown_window = None
        for w in candidates:
            state = self._window_state(w)
            if state == "login":
                login_window = w
                continue
            if state == "unknown":
                unknown_window = w
                continue
            self._window = w
            return True

        if unknown_window is not None:
            nodes = len(self._walk(unknown_window))
            if nodes <= self.A11Y_EMPTY_NODES:
                log.error(
                    f"微信窗口存在，但无障碍(UIA)树为空（只枚举到 {nodes} 个节点）。"
                    "通常是微信被关到托盘后又被重新显示：此时它只重绘界面，不重建无障碍树。"
                    "请从托盘彻底退出微信并重新启动；本次发送已取消"
                )
                return False
            # 聊天界面特征可能尚未渲染完（微信刚启动）；仍绑定，发送前会再校验。
            log.warning("微信窗口已绑定，但未识别到聊天界面特征（界面可能尚未渲染）")
            self._window = unknown_window
            return True

        if login_window is not None:
            log.error("微信当前停在登录界面（未登录），请先登录微信；本次发送已取消")
        return False

    def current_window_info(self) -> dict | None:
        """当前绑定的微信窗口信息（供 WebUI 状态展示）。句柄已失效则返回 None。"""
        if not self._hwnd:
            return None
        try:
            if not self._user32_inst().IsWindow(self._hwnd):
                return None
        except Exception:
            return None
        info = {"hwnd": self._hwnd, "pid": 0, "title": ""}
        try:
            user32 = self._user32_inst()
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(self._hwnd, ctypes.byref(pid))
            info["pid"] = pid.value
            length = user32.GetWindowTextLengthW(self._hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(self._hwnd, buf, length + 1)
            info["title"] = buf.value
        except Exception:
            pass
        return info

    def rebind_window(self, hwnd: int) -> bool:
        """重新绑定到指定窗口（WebUI 窗口选择）。hwnd=0 表示恢复自动查找。"""
        with self._lock:
            if not hwnd:
                self._window = None
                self._hwnd = 0
                self._last_contact = ""
                self._ready = False
                self._find_window()
                if self._window:
                    self._hwnd = self._window.NativeWindowHandle
                    self._ready = True
                    log.info(f"已恢复自动查找，当前窗口 hwnd={self._hwnd}")
                    return True
                return False
            try:
                ctrl = self._auto.ControlFromHandle(hwnd) if self._auto else None
                if ctrl and ctrl.Exists(0.3):
                    self._window = ctrl
                    self._hwnd = hwnd
                    self._last_contact = ""  # 换窗口后联系人缓存失效
                    self._ready = True
                    log.info(f"已绑定微信窗口 hwnd={hwnd}")
                    return True
                log.warning(f"绑定窗口失败，句柄无效: hwnd={hwnd}")
            except Exception as e:
                log.error(f"绑定窗口异常: {e}")
        return False

    def _is_login_window(self) -> bool:
        """当前绑定的窗口是否是登录界面（微信掉线后主窗口会被它取代）。"""
        if not self._window:
            return False
        try:
            return self._window_state(self._window) == "login"
        except Exception:
            return False

    def _window_alive(self) -> bool:
        """检测当前窗口控件是否仍然有效。

        微信重启/更新后，旧的 uiautomation 控件会变成失效 COM 对象，
        调用 Exists() 等方法会直接抛 COM 异常（如 0x80040201
        「事件无法调用任何订户」），必须捕获并视为窗口已失效。
        """
        if not self._window or not self._hwnd:
            return False
        try:
            if not self._window.Exists(0.2):
                return False
            # 再校验句柄本身是否仍是有效窗口
            if not self._user32_inst().IsWindow(self._hwnd):
                return False
            # 绑定的若是登录界面（发送途中微信掉线），同样不可用
            if self._is_login_window():
                log.warning("绑定的微信窗口已变为登录界面，微信可能已掉线")
                return False
            return True
        except Exception as e:
            log.warning(f"微信窗口控件已失效（可能微信已重启）: {e}")
            return False

    def _invalidate_window(self):
        """清除失效窗口缓存，下次发送时重新查找。"""
        self._window = None
        self._hwnd = 0
        self._ready = False
        self._last_contact = ""

    def _ensure_window(self) -> bool:
        """确保窗口可用。

        若启动时微信尚未运行（未找到窗口），这里会在每次发送时**惰性重找**，
        从而避免一直停在"UIA Sender 未就绪"（微信晚于 bridge 启动时无需重启 bridge）。
        微信重启导致旧控件失效时同样会重新查找。
        """
        if self._auto is None:
            log.error("uiautomation 不可用，无法定位微信窗口")
            return False
        if self._window_alive():
            self._ready = True
            try:
                self._hwnd = self._window.NativeWindowHandle
            except Exception:
                pass
            return True
        # 重新查找微信窗口（可能启动时微信未就绪，或微信重启后旧控件失效）
        if self._window or self._hwnd:
            log.warning("微信窗口已失效，重新查找...")
        self._invalidate_window()
        self._find_window()
        if not self._window:
            log.error("微信主窗口不可用（未登录或窗口未找到），本次发送已取消")
            self._ready = False
            return False
        self._ready = True
        self._hwnd = self._window.NativeWindowHandle
        return True

    # ================================================================
    # 窗口激活 / 键盘辅助
    # ================================================================

    def _attach_input(self):
        """附着到微信 UI 线程，使投递的鼠标/键盘消息能被 Qt 处理。

        SetForegroundWindow 会主动把微信提到最前，改用 AttachThreadInput 让本线程
        与微信线程共享输入队列，再 SetActiveWindow 把微信设为该队列的活动窗口，
        这样投递的鼠标/键盘消息会被处理，真实光标也不被移动。

        边界：这两步本身就会使微信窗口成为前台窗口（实测），见模块文档字符串。

        成功返回 True，调用方**必须**配一次 _detach_input()。
        """
        user32 = self._user32_inst()
        kernel32 = ctypes.windll.kernel32
        hwnd = self._hwnd
        if not hwnd:
            return False
        if self._attached:
            return True
        try:
            self._tid_wx = user32.GetWindowThreadProcessId(hwnd, None)
            self._tid_me = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(self._tid_me, self._tid_wx, True)
            user32.SetActiveWindow(hwnd)
            self._attached = True
            time.sleep(0.2)
            return True
        except Exception as e:
            log.warning(f"附着微信输入队列失败: {e}")
            self._attached = False
            return False

    def _detach_input(self):
        """解除线程附着。共享输入队列期间不做别的事，用完立刻解除。"""
        if not self._attached:
            return
        try:
            self._user32_inst().AttachThreadInput(self._tid_me, self._tid_wx, False)
        except Exception:
            pass
        self._attached = False

    # ================================================================
    # 前台窗口：发送通道开关与发完还原
    # ================================================================

    def _foreground_gate(self, contact: str) -> bool:
        """发送通道开关检查。

        微信收到投递进来的鼠标或键盘消息就会把自己升到前台，本模块无法避免，
        所以通道默认关闭，要 config.json 里显式打开才发送。

        隔离桌面模式下无需此开关：微信在那张桌面上怎么升前台都影响不到用户，
        这正是该模式存在的意义。
        """
        if self.isolated_desktop:
            return True
        if self.allow_foreground_steal:
            return True
        log.error(
            f"[UIA失败] {contact}: 发送通道未启用，发送会把微信窗口提到前台。"
            "确认可以接受后，把 config.json 里的 allow_foreground_steal 设为 true "
            "并重启 bridge"
        )
        return False

    @classmethod
    def _foreground_window(cls) -> int:
        """当前前台窗口句柄，取不到返回 0。"""
        try:
            return int(cls._user32_inst().GetForegroundWindow() or 0)
        except Exception:
            return 0

    @classmethod
    def _raise_window(cls, hwnd: int) -> bool:
        """把指定窗口提到前台，成功返回 True。

        裸 SetForegroundWindow 会被 Windows 的前台锁挡掉（实测三次只成一次）；
        先 AttachThreadInput 到「当前前台窗口所属线程」，再 BringWindowToTop +
        SetForegroundWindow，实测三次全成、耗时 3-11 毫秒。

        只做还原动作：不移动窗口、不改尺寸、不改置顶状态。ShowWindow 仅在窗口
        处于最小化时调用，避免把最大化的窗口还原成普通尺寸。
        """
        if not hwnd:
            return False
        user32 = cls._user32_inst()
        kernel32 = ctypes.windll.kernel32
        try:
            if not user32.IsWindow(hwnd):
                return False
            cur = int(user32.GetForegroundWindow() or 0)
            tid_cur = user32.GetWindowThreadProcessId(cur, None) if cur else 0
            tid_me = kernel32.GetCurrentThreadId()
            attached = False
            if tid_cur and tid_cur != tid_me:
                attached = bool(user32.AttachThreadInput(tid_me, tid_cur, True))
            try:
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(tid_me, tid_cur, False)
        except Exception as e:
            log.warning(f"把窗口提到前台失败: {e}")
            return False
        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline:
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                return True
            time.sleep(0.02)
        return int(user32.GetForegroundWindow() or 0) == hwnd

    def _restore_foreground(self, hwnd: int) -> None:
        """把前台还给发送前的前台窗口。

        投递输入会把微信升到前台；发完立刻还回去，用户看到的是一次短暂切换而不是
        前台被占住。发送前的前台本来就是微信、或那个窗口已经关掉时无需处理。

        还原失败不影响发送结果，只记一条日志。
        """
        if not hwnd or hwnd == self._hwnd:
            return
        if self._foreground_window() == hwnd:
            return
        if not self._raise_window(hwnd):
            log.warning("前台没有还给原窗口（消息本身已发出）")

    def _release_foreground(self) -> None:
        """在一次投递动作做完后立刻还前台，不等后面的送达确认。

        送达确认要轮询会话摘要（最长约 3 秒），那段时间没必要把用户的前台占着；
        每步投递后都调一次，前台本来就没被拿走时它只做两次 API 调用，几乎不耗时。
        """
        self._restore_foreground(self._fg_before)

    def _post_key(self, vk: int, *modifiers: int) -> None:
        """向微信主窗口投递一次按键（可带修饰键）。"""
        user32 = self._user32_inst()
        hwnd = self._hwnd
        if not hwnd:
            return
        for mod in modifiers:
            user32.PostMessageW(hwnd, WM_KEYDOWN, mod, 1)
            time.sleep(0.02)
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 1)
        time.sleep(0.02)
        user32.PostMessageW(hwnd, WM_KEYUP, vk, KEYUP_LPARAM)
        for mod in reversed(modifiers):
            user32.PostMessageW(hwnd, WM_KEYUP, mod, KEYUP_LPARAM)
            time.sleep(0.02)

    def _post_char(self, ch: str) -> None:
        """投递一个可见字符（WM_CHAR，支持中文）。"""
        user32 = self._user32_inst()
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CHAR, ord(ch), 1)

    def _client_lparam(self, sx: float, sy: float, hwnd: int = 0) -> int:
        """屏幕坐标 → 指定窗口客户区坐标，并打包成鼠标消息的 lParam。"""
        target = hwnd or self._hwnd
        point = wintypes.POINT(int(sx), int(sy))
        self._user32_inst().ScreenToClient(target, ctypes.byref(point))
        return ((point.y & 0xFFFF) << 16) | (point.x & 0xFFFF)

    def _post_click(self, control, hwnd: int = 0) -> bool:
        """向控件中心投递一次鼠标点击。

        用 PostMessage 而非真实光标移动（SendInput）：不会挪动用户的鼠标，
        也不需要窗口在前台。

        `hwnd` 指定接收消息的窗口，默认主窗口。搜索弹层是**独立顶层窗口**
        （Qt popup 有自己的 HWND，类名 Qt51514QWindowToolSaveBits），消息发给
        主窗到不了它，点结果项必须显式把消息投给弹层自己。
        """
        user32 = self._user32_inst()
        try:
            rect = control.BoundingRectangle
        except Exception:
            return False
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return False
        target = hwnd or self._hwnd
        if not target:
            return False
        lp = self._client_lparam((rect.left + rect.right) / 2,
                                 (rect.top + rect.bottom) / 2, target)
        user32.PostMessageW(target, WM_MOUSEMOVE, 0, lp)
        user32.PostMessageW(target, WM_LBUTTONDOWN, 1, lp)
        user32.PostMessageW(target, WM_LBUTTONUP, 0, lp)
        return True

    # ================================================================
    # 控件树查询
    # ================================================================

    @staticmethod
    def _class_of(node) -> str:
        try:
            return node.ClassName or ""
        except Exception:
            return ""

    def _walk(self, root=None):
        """广度优先遍历控件树，返回 [(深度, 控件)]，受深度与节点预算约束。"""
        root = self._window if root is None else root
        if root is None:
            return []
        queue, out = [(0, root)], []
        while queue and len(out) < self.WALK_MAX_NODES:
            depth, node = queue.pop(0)
            out.append((depth, node))
            if depth >= self.WALK_MAX_DEPTH:
                continue
            try:
                for child in node.GetChildren():
                    queue.append((depth + 1, child))
            except Exception:
                continue
        return out

    def _find_by_class(self, cls: str, root=None):
        for _, node in self._walk(root):
            if self._class_of(node) == cls:
                return node
        return None

    def _input_box(self):
        """当前会话的输入框；没有打开任何会话时返回 None。"""
        return self._find_by_class(self.INPUT_FIELD_CLASS)

    def _input_text(self) -> str:
        """输入框里当前的文本（拿不到就返回空串）。"""
        box = self._input_box()
        if box is None:
            return ""
        try:
            return box.GetValuePattern().Value or ""
        except Exception:
            return ""

    def _open_chat_title(self):
        """当前打开的会话名——输入框的 Name 就是对方的名字。"""
        box = self._input_box()
        if box is None:
            return None
        try:
            return box.Name or None
        except Exception:
            return None

    @staticmethod
    def _cell_contact(cell) -> str:
        """会话项名字的第一行是会话名（后面两行是摘要与时间）。"""
        try:
            return (cell.Name or "").split("\n")[0]
        except Exception:
            return ""

    def _session_cells(self):
        return [n for _, n in self._walk()
                if self._class_of(n) == self.SESSION_CELL_CLASS]

    def _session_summary(self, contact: str):
        """会话列表里该会话的摘要行，即微信自己渲染的「最后一条消息」。"""
        for cell in self._session_cells():
            if self._cell_contact(cell) == contact:
                parts = (cell.Name or "").split("\n")
                return parts[1] if len(parts) > 1 else ""
        return None

    # ================================================================
    # 联系人切换
    # ================================================================

    def _select_visible_session(self, contact: str) -> bool:
        """在会话列表里点开目标会话；列表里没有它时返回 False。

        坑：重复点击「当前已打开的会话」会把聊天面板清空（控件树从 155 个节点
        掉到 80）。所以先比对输入框标题，已经是当前会话就直接算命中。
        """
        if self._open_chat_title() == contact:
            return True
        for cell in self._session_cells():
            if self._cell_contact(cell) != contact:
                continue
            if not self._post_click(cell):
                return False
            time.sleep(0.9)
            return self._open_chat_title() == contact
        return False

    @staticmethod
    def _native_hwnd(node) -> int:
        """控件所属的顶层窗口句柄（0 表示拿不到）。"""
        if node is None:
            return 0
        try:
            return int(node.NativeWindowHandle or 0)
        except Exception:
            return 0

    def _search_hit(self, contact: str, popover):
        """搜索弹层里指向该联系人的**会话结果项**（同名项取最靠上的一个）。

        弹层里同名的项不止一个：搜狗网络结果的名字带后缀（如「文件传输助手
        打开」），「功能」分区下还有与联系人**完全同名**的动作项（如「发送
        文件」）。所以只认名字完全相同的项，并排除「功能」标题之下的那些。
        """
        if popover is None:
            return None
        nodes = self._walk(popover)

        limit = None
        for _, node in nodes:
            if (self._class_of(node) in self.SEARCH_RESULT_CLASSES
                    and (node.Name or "") == self.SEARCH_ACTION_HEADER):
                try:
                    limit = node.BoundingRectangle.top
                except Exception:
                    pass
                break

        best = None
        best_top = None
        for _, node in nodes:
            if self._class_of(node) not in self.SEARCH_RESULT_CLASSES:
                continue
            if (node.Name or "") != contact:
                continue
            try:
                top = node.BoundingRectangle.top
            except Exception:
                continue
            if limit is not None and top >= limit:
                continue
            if best is None or top < best_top:
                best, best_top = node, top
        return best

    def _search_and_select(self, contact: str) -> bool:
        """用主窗口顶部的搜索框找会话并点开（会话列表里没有它时的兜底）。

        搜索框是常驻控件（不是 Ctrl+F 弹层），点一下就能打字。搜索结果项虽然
        作为 `mmui::SearchContentPopover` 的子节点出现在 UIA 树里，但那个弹层是
        **独立顶层窗口**（Qt popup，类名 Qt51514QWindowToolSaveBits）——点结果项
        必须把鼠标消息投给弹层自己的 HWND，投给主窗到不了；这正是「打字能出
        结果、点了却切不过去」的原因。
        """
        user32 = self._user32_inst()
        field = (self._find_by_class(self.SEARCH_FIELD_CLASS)
                 or self._find_by_class("mmui::XValidatorTextEdit"))
        if field is None or not self._post_click(field):
            return False
        time.sleep(0.7)
        for ch in contact:
            user32.PostMessageW(self._hwnd, WM_CHAR, ord(ch), 1)
            time.sleep(0.05)
        time.sleep(1.5)

        popover = self._find_by_class(self.SEARCH_POPOVER_CLASS)
        hit = self._search_hit(contact, popover) if popover is not None else None
        if hit is not None:
            self._post_click(hit, self._native_hwnd(popover))
            time.sleep(1.0)

        # 退出搜索：先清空关键词，再**在弹层确实还在时**才按 Esc。
        # 没有弹层时按 Esc，微信会把它当成「关闭主窗口到托盘」——那个窗口再显示
        # 出来只会重绘界面，无障碍树不会重建（UIA 从此只剩两级节点），只能重启微信。
        for _ in range(len(contact) + 4):
            user32.PostMessageW(self._hwnd, WM_KEYDOWN, VK_BACK, 1)
            user32.PostMessageW(self._hwnd, WM_KEYUP, VK_BACK, KEYUP_LPARAM)
            time.sleep(0.01)
        time.sleep(0.4)
        if self._find_by_class(self.SEARCH_POPOVER_CLASS) is not None:
            user32.PostMessageW(self._hwnd, WM_KEYDOWN, VK_ESCAPE, 1)
            user32.PostMessageW(self._hwnd, WM_KEYUP, VK_ESCAPE, KEYUP_LPARAM)
            time.sleep(0.5)
        return self._open_chat_title() == contact

    def _switch_contact(self, contact: str) -> bool:
        """把当前会话切到目标联系人（调用前必须已经 _attach_input）。"""
        if self._select_visible_session(contact):
            log.info(f"已切到联系人: {contact}")
            return True
        if self._search_and_select(contact):
            log.info(f"已切到联系人（搜索）: {contact}")
            return True
        log.error(f"切换联系人 {contact} 失败：会话列表与搜索都未能选中")
        return False

    def _prepare_target(self, contact: str) -> bool:
        """发送前把会话切到目标联系人；切不过去返回 False（此次不可发送）。

        只在换目标时切换：同联系人连续发送（麦麦一段话拆成多条回复）沿用当前
        会话。切换失败必须中止——否则按键会打进当前界面，消息其实没发出去，
        却照样报发送成功。
        """
        if not (self.search_enabled and contact and contact != self._last_contact):
            return True
        if not self._ensure_window():
            return False
        self._attach_input()
        try:
            switched = self._switch_contact(contact)
        finally:
            self._detach_input()
        if not switched:
            self._last_contact = ""
            log.error(
                f"[UIA失败] {contact}: 未能切换到该联系人"
                "（会话列表与搜索都不可用，微信可能未登录），本次发送已取消"
            )
            return False
        self._last_contact = contact
        return True

    # ================================================================
    # 发送文字
    # ================================================================

    def send_text(self, contact: str, text: str) -> bool:
        """发送文本消息。

        顶层防护：通道未启用时直接拒绝；微信重启/COM 失效时安全失败并自动重绑；
        发送结束后把前台还给发送前的前台窗口。
        """
        if not self._foreground_gate(contact):
            return False
        with self._send_lock:
            self._fg_before = self._foreground_window()
            try:
                return self._send_text_impl(contact, text)
            except Exception as e:
                log.error(f"[UIA失败] {contact}: 发送异常（窗口可能已失效）: {e}")
                with self._lock:
                    self._invalidate_window()
                return False
            finally:
                self._restore_foreground(self._fg_before)
                self._fg_before = 0

    def _clear_input(self) -> bool:
        """清空输入框（先把插入点移到末尾，再退格逐字符删），并回读确认。

        插入点不一定在末尾——输入框里的内容可能是别的程序写进去的，此时退格删不到
        任何东西，输入框看着「清不掉」。所以每轮先投一次 VK_END。
        """
        user32 = self._user32_inst()
        for _ in range(8):
            current = self._input_text()
            if not current:
                return True
            user32.PostMessageW(self._hwnd, WM_KEYDOWN, VK_END, 1)
            user32.PostMessageW(self._hwnd, WM_KEYUP, VK_END, KEYUP_LPARAM)
            time.sleep(0.05)
            for _ in range(len(current) + 4):
                user32.PostMessageW(self._hwnd, WM_KEYDOWN, VK_BACK, 1)
                user32.PostMessageW(self._hwnd, WM_KEYUP, VK_BACK, KEYUP_LPARAM)
                time.sleep(0.01)
            time.sleep(0.2)
        left = self._input_text()
        if left:
            log.warning(f"输入框未能清空，残留 {left[:40]!r}")
            return False
        return True

    def _type_text(self, text: str) -> bool:
        """逐字符投递 WM_CHAR 并回读校验。

        输入法会扰动字符（实测输入 `t` 落到输入框里变成 `上`），所以打完必须
        回读比对；不符就清空重打，三次仍不符判定失败。
        """
        for _ in range(3):
            for ch in text:
                self._post_char(ch)
                time.sleep(random.uniform(0.02, 0.04))
            time.sleep(0.3)
            got = self._input_text()
            if got == text:
                return True
            log.warning(
                f"输入框回读不符（期望 {text[:40]!r}，实得 {got[:40]!r}），清空重打"
            )
            self._clear_input()
        return False

    def _confirm_sent(self, contact: str, text: str, summary_before: str) -> bool:
        """按会话列表的摘要行确认消息确实发出去了。

        摘要行是微信渲染的「最后一条消息」，比任何自报都硬。正文可能被截断，
        所以只比前 20 个非空白字符；再兜一层：输入框已空且摘要确实变了。
        """
        probe = "".join(text.split())[:20]
        for _ in range(8):
            summary = self._session_summary(contact) or ""
            if probe and probe in "".join(summary.split()):
                return True
            time.sleep(0.35)
        summary = self._session_summary(contact) or ""
        if not self._input_text() and summary and summary != summary_before:
            log.warning(f"{contact}: 摘要未匹配正文，但输入框已清空且摘要已更新")
            return True
        return False

    def _paste_clipboard(self, expect_image: bool) -> bool:
        """用伪造 Ctrl 键态的 Ctrl+V 把剪贴板内容贴进输入框。

        修饰键状态是线程级的，PostMessage 投递的 Ctrl 进不了微信线程的键态，
        V 会变成字面字符。AttachThreadInput 让两个线程共享输入状态，再用
        SetKeyboardState 把本线程的 Ctrl 置为按下，微信就能把随后的 V 解析成
        Ctrl+V。粘贴结果回读校验，不符即当失败。
        """
        user32 = self._user32_inst()
        user32.SetKeyboardState.argtypes = [ctypes.c_char_p]
        state = bytearray(256)
        state[VK_CONTROL] = 0x80
        state[VK_LCONTROL] = 0x80
        try:
            user32.SetKeyboardState(bytes(state))
            self._post_key(VK_V, VK_CONTROL)
            # 粘贴键一投出去，抢前台就已经发生了，立刻还回去，不等粘贴落定
            self._release_foreground()
            time.sleep(1.2)
        finally:
            state[VK_CONTROL] = 0
            state[VK_LCONTROL] = 0
            try:
                user32.SetKeyboardState(bytes(state))
            except Exception:
                pass
        staged = self._input_text()
        return self.IMAGE_MARK in staged if expect_image else bool(staged)

    def _send_text_impl(self, contact: str, text: str) -> bool:
        """发送文本消息。"""
        with self._lock:
            if not self._ensure_window():
                return False

            # 安全检查：过滤 PIL 引用
            if "<PIL." in text or "PIL." in text:
                log.warning(f"跳过 PIL 引用消息: {text[:60]}")
                return False

            if not self._prepare_target(contact):
                return False
            self._release_foreground()

            self._attach_input()
            try:
                summary_before = self._session_summary(contact) or ""
                if not self._clear_input():
                    log.error(f"[UIA失败] {contact}: 输入框有清不掉的残留，本次发送已取消")
                    return False
                self._release_foreground()
                if not self._type_text(text):
                    self._clear_input()
                    log.error(f"[UIA失败] {contact}: 文字未能完整进到输入框，本次发送已取消")
                    return False
                self._release_foreground()
                if self._open_chat_title() != contact:
                    self._clear_input()
                    log.error(f"[UIA失败] {contact}: 输入期间会话已改变，本次发送已取消")
                    return False

                self._post_key(VK_RETURN)
                self._release_foreground()
                time.sleep(0.6)

                if not self._confirm_sent(contact, text, summary_before):
                    log.error(f"[UIA失败] {contact}: 回车后摘要未更新，消息可能没发出去")
                    return False
                log.info(f"[UIA成功] {contact}: {text[:50]}")
                return True
            except Exception as e:
                log.error(f"[UIA失败] {contact}: {e}")
                return False
            finally:
                self._detach_input()

    # ================================================================
    # 发送图片
    # ================================================================

    def send_image(self, contact: str, image_path: str) -> bool:
        """发送图片。

        顶层防护：通道未启用时直接拒绝；微信重启/COM 失效时安全失败并自动重绑；
        发送结束后把前台还给发送前的前台窗口。
        """
        if not self._foreground_gate(contact):
            return False
        with self._send_lock:
            self._fg_before = self._foreground_window()
            try:
                return self._send_image_impl(contact, image_path)
            except Exception as e:
                log.error(f"[UIA失败] 图片 -> {contact}: 发送异常（窗口可能已失效）: {e}")
                with self._lock:
                    self._invalidate_window()
                return False
            finally:
                self._restore_foreground(self._fg_before)
                self._fg_before = 0

    def _send_image_impl(self, contact: str, image_path: str) -> bool:
        """发送图片（剪贴板 + 后台 Ctrl+V + Enter）。"""
        with self._lock:
            if not os.path.isfile(image_path):
                log.error(f"图片不存在: {image_path}")
                return False
            if not self._ensure_window():
                return False
            if not self._prepare_target(contact):
                return False
            self._release_foreground()
            if not self._copy_image_to_clipboard(image_path):
                return False

            self._attach_input()
            try:
                if not self._clear_input():
                    log.error(f"[UIA失败] 图片 -> {contact}: 输入框有清不掉的残留，本次发送已取消")
                    return False
                self._release_foreground()
                if not self._paste_clipboard(expect_image=True):
                    self._clear_input()
                    log.error(
                        f"[UIA失败] 图片 -> {contact}: 剪贴板图片没能贴进输入框，本次发送已取消"
                    )
                    return False
                self._release_foreground()

                self._post_key(VK_RETURN)
                self._release_foreground()
                time.sleep(0.8)

                summary = self._session_summary(contact) or ""
                if "图片" not in summary:
                    log.error(f"[UIA失败] 图片 -> {contact}: 摘要未显示图片（{summary[:24]!r}）")
                    return False
                log.info(f"[UIA成功] 图片 -> {contact}: {os.path.basename(image_path)}")
                return True
            except Exception as e:
                log.error(f"[UIA失败] 图片 -> {contact}: {e}")
                return False
            finally:
                self._detach_input()

    def send_file(self, contact: str, file_path: str) -> bool:
        """发送文件（mp3 等任意文件）。

        由于微信 PC 客户端不支持发送"语音消息气泡"（只能按住麦克风按钮录制），
        mp3 只能以"文件卡片"形式发送：双击文件即可播放。适用于把麦麦的 TTS 语音
        发到微信场景。

        实现方式：用 PowerShell 把文件路径放到剪贴板的 CF_HDROP 格式（即
        "拖入文件"语义），再到聊天窗口 Ctrl+V，微信会识别为文件并显示文件名，
        按 Enter 发送。

        顶层防护：通道未启用时直接拒绝；发送结束后把前台还给发送前的前台窗口。
        """
        if not self._foreground_gate(contact):
            return False
        with self._send_lock:
            self._fg_before = self._foreground_window()
            try:
                return self._send_file_impl(contact, file_path)
            except Exception as e:
                log.error(f"[UIA失败] 文件 -> {contact}: 发送异常（窗口可能已失效）: {e}")
                with self._lock:
                    self._invalidate_window()
                return False
            finally:
                self._restore_foreground(self._fg_before)
                self._fg_before = 0

    def _send_file_impl(self, contact: str, file_path: str) -> bool:
        """发送文件（CF_HDROP 剪贴板 + 后台 Ctrl+V + Enter）。"""
        with self._lock:
            if not os.path.isfile(file_path):
                log.error(f"文件不存在: {file_path}")
                return False
            if not self._ensure_window():
                return False
            if not self._prepare_target(contact):
                return False
            self._release_foreground()
            name = os.path.basename(file_path)
            if not self._copy_file_to_clipboard(file_path):
                return False

            self._attach_input()
            try:
                if not self._clear_input():
                    log.error(f"[UIA失败] 文件 -> {contact}: 输入框有清不掉的残留，本次发送已取消")
                    return False
                self._release_foreground()
                if not self._paste_clipboard(expect_image=False):
                    self._clear_input()
                    log.error(f"[UIA失败] 文件 -> {contact}: 文件没能贴进输入框，本次发送已取消")
                    return False
                self._release_foreground()

                self._post_key(VK_RETURN)
                self._release_foreground()
                time.sleep(0.8)

                summary = self._session_summary(contact) or ""
                if name not in summary and "文件" not in summary:
                    log.error(f"[UIA失败] 文件 -> {contact}: 摘要未显示该文件（{summary[:24]!r}）")
                    return False
                log.info(f"[UIA成功] 文件 -> {contact}: {name}")
                return True
            except Exception as e:
                log.error(f"[UIA失败] 文件 -> {contact}: {e}")
                return False
            finally:
                self._detach_input()

    def _run_powershell(self, script: str) -> bool:
        """跑一段 PowerShell（用来写系统剪贴板），成功返回 True。

        不能用 `-WindowStyle Hidden`：Windows PowerShell 5.1 把它与 `-Command`
        内联脚本组合时会直接以 -1 退出，脚本一行都不执行——图片与文件因此静默
        发不出去。改用 CREATE_NO_WINDOW 抑制控制台窗口。
        """
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=20,
                creationflags=self.CREATE_NO_WINDOW,
            )
        except Exception as e:
            log.error(f"调用 PowerShell 失败: {e}")
            return False
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            log.error(f"PowerShell 退出码 {proc.returncode}: {err[:200]}")
            return False
        return True

    def _copy_file_to_clipboard(self, path: str) -> bool:
        """把文件放进剪贴板的 CF_HDROP 格式（即「拖入文件」语义）。

        微信收到 Ctrl+V 会把它识别为附件而非文本。路径必须先转成绝对路径，
        否则拖放格式无效。
        """
        abs_path = os.path.abspath(path).replace("'", "''")
        return self._run_powershell(
            "Add-Type -AssemblyName System.Windows.Forms;"
            f"$abs = Resolve-Path -LiteralPath '{abs_path}';"
            "$col = New-Object System.Collections.Specialized.StringCollection;"
            "$col.Add($abs.Path);"
            "[System.Windows.Forms.Clipboard]::SetFileDropList($col)"
        )

    def _copy_image_to_clipboard(self, path: str) -> bool:
        """把图片放进系统剪贴板。

        必须显式加载 System.Drawing 程序集，否则 [System.Drawing.Image] 类型
        找不到，SetImage 会因参数为 null 而失败。
        """
        abs_path = os.path.abspath(path)
        return self._run_powershell(
            "Add-Type -AssemblyName System.Drawing;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            f"$img = [System.Drawing.Image]::FromFile('{abs_path}');"
            "[System.Windows.Forms.Clipboard]::SetImage($img);"
            "$img.Dispose()"
        )
