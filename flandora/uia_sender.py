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
- 文本用 WM_CHAR 逐字符输入（中文走这条路最快，也不碰剪贴板）；正文里出现
  U+FFFF 以上的字符（emoji）时改走剪贴板粘贴——WM_CHAR 的 wParam 只有一个
  16 位码元，这种字符投进去会被微信输入框丢掉（代理项拆开投递也一样）
- 图片/文件/含 emoji 的文本用剪贴板 + 伪造 Ctrl 键态的 Ctrl+V
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

from . import config
from .win32 import windows as win32_windows

log = logging.getLogger("flandora")

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
VK_DOWN = 0x28
VK_V = 0x56

# ShowWindow 命令：还原并激活窗口
SW_RESTORE = 9

# WM_KEYUP 的 lParam：低位为重复次数 1，其余位标明键已抬起
KEYUP_LPARAM = 0xC0000001


def utf16_units(ch: str) -> tuple[int, ...]:
    """把一个字符拆成 WM_CHAR 的 wParam 序列（UTF-16 码元）。

    WM_CHAR 的 wParam 是一个 16 位码元，U+FFFF 以上的字符（emoji、生僻字）
    得按 UTF-16 拆成高低代理项分两条消息投递。微信输入框连代理项也收不下
    （实测），所以这类字符改走剪贴板粘贴：见 ``UiaSender._enter_text``。
    """
    code = ord(ch)
    if code <= 0xFFFF:
        return (code,)
    code -= 0x10000
    return (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))


def has_non_bmp(text: str) -> bool:
    """文本里是否含 U+FFFF 以上的字符（emoji 这类字符 WM_CHAR 送不进去）。"""
    return any(ord(ch) > 0xFFFF for ch in text)


def needs_paste(text: str) -> bool:
    """正文是否必须走剪贴板：含 emoji 或换行。

    两类字符 WM_CHAR 都送不进去：
    - U+FFFF 以上（emoji）：wParam 只有一个 16 位码元，投进去会被微信丢掉；
    - 换行：微信输入框把 LF 整行吞掉（实测一次 270 字的正文回读只有 261 字，
      每个换行少一个字），而按 Enter 是「发送」而不是换行，只能靠粘贴带进去。
    """
    return has_non_bmp(text) or "\n" in text


def _looks_like_pil_repr(text: str) -> bool:
    """正文里是否夹着 PIL 对象的 repr（`<PIL.Image …>`）。

    只认 repr 残留，不认「PIL.」这个子串：后者会把讨论 Pillow 的正常正文一起
    拦下，那种消息没有任何理由发不出去。
    """
    return "<PIL." in text


def mismatch_window(want: str, got: str) -> str:
    """回读不符时的定位信息：第几个字起不同、两边的上下文、各自的长度。

    只打前 40 个字看不出是「漏了一个换行」还是「多了一个杂字」——2026-10-26 的
    一次真机失败就卡在这里：期望以换行结尾、实得末尾是个 `U`，两种可能都说得通。
    """
    at = next(
        (i for i, (a, b) in enumerate(zip(want, got)) if a != b),
        min(len(want), len(got)),
    )
    return (
        f"第 {at} 个字起不同：期望 {want[max(0, at - 20) : at + 20]!r}，"
        f"实得 {got[max(0, at - 20) : at + 20]!r}；长度 {len(want)}/{len(got)}"
    )


class BaseSender:
    """消息发送器基类"""

    def send_text(self, contact: str, text: str) -> bool:
        raise NotImplementedError

    def send_image(self, contact: str, image_path: str) -> bool:
        raise NotImplementedError


# ================================================================
# 进程窗口枚举（多开识别用，纯 ctypes，不依赖 uiautomation）
# ================================================================


def _with_cancel_note(message: str) -> str:
    """给失败原因接一句「本次发送已取消」，按前句末尾的标点接续。

    原因本身可能是一整句（以句号收尾），也可能是不带标点的短语；
    直接拼「，本次发送已取消」会拼出「。」后的逗号。
    """
    note = "本次发送已取消"
    if not message:
        return note
    if message[-1] in "。！？…":
        return f"{message}{note}"
    return f"{message}，{note}"


def enum_process_windows(pid: int, desktop_name: str = "") -> list[int]:
    """枚举某个进程的所有顶层窗口句柄（含没有标题的 Qt 弹层）。

    `enum_wechat_windows` 只认标题里带「微信 / WeChat」的主窗口，而成员选择
    弹层、搜索弹层这类 Qt popover 没有标题，只能按 PID 找。按「输入 @ 前后
    比对窗口集合、多出来的那个就是弹层」的方式定位弹层，靠的就是这里。
    """
    if not pid:
        return []
    user32 = ctypes.windll.user32
    out: list[int] = []
    Proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        try:
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if int(owner.value) == int(pid):
                out.append(int(hwnd))
        except Exception:
            pass
        return True

    if desktop_name:
        hdesk = user32.OpenDesktopW(desktop_name, 0, False, 0x000F01FF)
        if not hdesk:
            return out
        try:
            user32.EnumDesktopWindows(hdesk, Proto(_cb), 0)
        finally:
            user32.CloseDesktop(hdesk)
    else:
        user32.EnumWindows(Proto(_cb), 0)
    return out


# 会话摘要里代表「一张图片」的词：中文界面写「图片」，英文界面写 Photo / Image。
_IMAGE_SUMMARY_WORDS = ("图片", "Photo", "Image")


def _is_image_summary(summary: str) -> bool:
    return any(word in (summary or "") for word in _IMAGE_SUMMARY_WORDS)


class UiaSender(BaseSender):
    """微信消息发送器：把输入以窗口消息定向投递给微信线程。"""

    WECHAT_TITLES = ["微信", "WeChat", "Weixin"]

    # 会话列表项 / 输入框 / 搜索框 / 搜索结果项 / 搜索弹层（微信 4.1.15.56 实测类名）。
    # 会话项的名字是多行文本：会话名 / 最后一条消息摘要 / 时间。
    SESSION_CELL_CLASS = "mmui::ChatSessionCell"
    INPUT_FIELD_CLASS = "mmui::ChatInputField"
    # 会话列表底部的「折叠的聊天」分组项（微信把不常用的会话收进这里）
    FOLD_CELL_CLASS = "mmui::ChatStickyFoldCell"
    # 输入框右下角的「发送」按钮：只认精确名字（输入栏里另有「发送表情」等按钮）
    SEND_BUTTON_NAMES = ("发送", "发送(S)", "Send")
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

    # 无障碍树为空的判据。微信被关到托盘后又被重新显示时，UIA 树只剩
    # MainWindow 与其渲染子窗两级；这与「界面尚未渲染完」不同，等多久都不会恢复。
    A11Y_EMPTY_NODES = 4

    _user32 = None

    @classmethod
    def _user32_inst(cls):
        if cls._user32 is None:
            cls._user32 = ctypes.windll.user32
        return cls._user32

    def __init__(
        self,
        search_enabled: bool = True,
        allow_foreground_steal: bool = False,
        isolated_desktop: str = "",
    ):
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
        # 最近一次发送失败的原因（供 WebUI 回显，不只是留在日志里）
        self._last_error = ""
        # 微信里配置的发送快捷键："" 表示还没探过，"enter" 回车，"ctrl_enter" Ctrl+回车
        self._send_key = ""

        self.search_enabled = search_enabled
        # 是否允许发送时把微信窗口提到前台。关闭时发送通道整体不可用。
        self.allow_foreground_steal = bool(allow_foreground_steal)
        # 非空时表示微信跑在指定名字的独立 Desktop 上：窗口枚举改走
        # EnumDesktopWindows，前台提升只影响那个桌面，用户所在的桌面不受打扰。
        self.isolated_desktop = isolated_desktop or ""
        # 每个线程记住自己有没有归位到微信那张桌面（见 _join_wechat_desktop）
        self._desktop_state = threading.local()

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
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
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

    @staticmethod
    def state_from_handle(hwnd: int) -> str:
        """按窗口句柄判断微信状态，返回 'login' / 'chat' / 'unknown'。

        实现在 ``win32.windows``：同一套判定隔离桌面管理器也要用，放这里会让
        ``wx_desktop`` 反过来依赖发送器。
        """
        return win32_windows.state_from_handle(hwnd)

    @staticmethod
    def _window_state(w) -> str:
        """按窗口内容判断状态；实现见 ``win32.windows.state_of``。"""
        return win32_windows.state_of(w)

    def _find_window_isolated(self) -> bool:
        """在隔离桌面上定位微信主窗口。

        两条路都只看当前桌面、在隔离桌面上看不见微信：`EnumWindows` 与 UIA 的
        `GetRootControl()`。所以先用 `EnumDesktopWindows` 取 hwnd，再用
        `ControlFromHandle` 跨桌面拿 UIA 控件（实测可用）。
        """
        auto = self._auto
        cands = win32_windows.enum_wechat_windows(self.isolated_desktop)
        if not cands:
            log.error("隔离桌面 %s 上没有找到微信窗口，微信是否已启动？", self.isolated_desktop)
            return False
        # 主窗口面积最大，优先试
        cands.sort(key=lambda c: -((c["rect"][2] - c["rect"][0]) * (c["rect"][3] - c["rect"][1])))
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
                log.info(
                    "已绑定隔离桌面 %s 上的微信窗口 hwnd=%d", self.isolated_desktop, info["hwnd"]
                )
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("绑定隔离窗口 hwnd=%s 失败：%s", info["hwnd"], exc)
        log.error("隔离桌面上没有找到可用的已登录微信窗口")
        return False

    def _find_window(self) -> bool:
        """定位微信**已登录主窗口**：优先绑定 config.wechat_hwnd 指定的窗口（多开场景），
        未配置或已失效时按自动查找处理。

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

    def window_handle(self) -> int:
        """当前绑定的窗口句柄；未绑定为 0。"""
        return int(self._hwnd or 0)

    def last_error(self) -> str:
        """最近一次发送失败的原因；空串表示没有记录。"""
        return str(self._last_error or "")

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
        self._join_wechat_desktop()
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

    def _join_wechat_desktop(self) -> None:
        """把执行发送的线程归位到微信所在的那张桌面。

        隔离桌面模式下微信在另一张桌面上，而本进程的线程都在默认桌面上；修饰键
        状态只共享给同桌面的线程，附着失败就等于 Ctrl 丢失（粘贴变成字面的 ``v``）。
        同一个线程只归位一次，之后由它自己记着。
        """
        if not self.isolated_desktop:
            return
        if getattr(self._desktop_state, "name", "") == self.isolated_desktop:
            return
        if win32_windows.join_thread_to_desktop(self.isolated_desktop):
            self._desktop_state.name = self.isolated_desktop
            return
        log.warning(
            "执行线程没能归位到隔离桌面 %s（错误码 %d）：粘贴用的修饰键可能传不过去",
            self.isolated_desktop,
            ctypes.get_last_error(),
        )

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
            if not user32.AttachThreadInput(self._tid_me, self._tid_wx, True):
                log.warning(
                    f"附着微信输入队列失败（错误码 {ctypes.get_last_error()}）："
                    "两个线程不在同一张桌面上时必然失败，修饰键与鼠标消息都会丢"
                )
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
    def _hwnd_alive(cls, hwnd: int) -> bool:
        """指定窗口句柄是否还指向一个存在的窗口（注意与实例方法 _window_alive 区分）。"""
        if not hwnd:
            return False
        try:
            return bool(cls._user32_inst().IsWindow(hwnd))
        except Exception:
            return False

    @classmethod
    def _hwnd_class(cls, hwnd: int) -> str:
        """指定窗口的类名（日志里认人用），取不到返回空串。"""
        try:
            buf = ctypes.create_unicode_buffer(256)
            cls._user32_inst().GetClassNameW(hwnd, buf, 256)
            return buf.value
        except Exception:
            return ""

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

        隔离桌面模式下不还：那张桌面上只有微信一个窗口、没人看，没有「被占住的前台」
        要还，还不上时还要白等 1 秒轮询。

        还原失败不影响发送结果。失败时把「发送前的前台」清掉：一次发送里每步投递
        之后都会调这里，同一次失败没必要重复报，也不用再试。
        """
        if self.isolated_desktop:
            return
        if not hwnd or hwnd == self._hwnd:
            return
        if self._foreground_window() == hwnd:
            return
        if self._raise_window(hwnd):
            return
        self._fg_before = 0
        log.warning(
            f"前台没有还给原窗口（消息本身已发出）：目标 hwnd={hwnd}"
            f"（类名 {self._hwnd_class(hwnd) or '取不到'}、"
            f"{'还在' if self._hwnd_alive(hwnd) else '已不存在'}），"
            f"当前前台 hwnd={self._foreground_window()}"
        )

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

    def _send_button(self):
        """输入框右下角的「发送」按钮；没有打开会话时返回 None。

        输入栏里另有「发送表情」「发送文件」这类按钮，按名字前缀认会认错，所以
        只认精确的名字。
        """
        for _, node in self._walk():
            try:
                if node.ControlTypeName != "ButtonControl":
                    continue
                name = (node.Name or "").strip()
            except Exception:
                continue
            if name in self.SEND_BUTTON_NAMES:
                return node
        return None

    def _click_send_button(self) -> bool:
        """点「发送」按钮发出去。

        微信「设置 → 快捷键」里把发送键改成 Ctrl+回车之后，回车只会在正文里插
        换行；点按钮与按键设置无关，是这条路径最稳的入口。找不到按钮时说明当前
        界面没有会话输入区（未登录、窗口没打开），直接返回 False。
        """
        button = self._send_button()
        if button is None:
            log.error("找不到微信的「发送」按钮，本次发送已取消")
            return False
        try:
            log.debug(
                "点「发送」按钮：name=%r class=%r rect=%s",
                button.Name,
                button.ClassName,
                button.BoundingRectangle,
            )
        except Exception:  # noqa: BLE001
            pass
        return self._post_click(button)

    def _configured_send_mode(self) -> str:
        """配置里指定的发送方式：auto / enter / button。

        每次发送都读一次配置，面板里改完立刻生效，不必重启桥接；配置读不出来或
        取值不认识时按 auto 处理，与默认行为一致。
        """
        try:
            mode = str(getattr(config, "SEND_KEY", "auto") or "auto").strip().lower()
        except Exception:  # noqa: BLE001
            return "auto"
        return mode if mode in ("auto", "enter", "button") else "auto"

    def _press_send(self) -> None:
        """用当前认定的方式发送。

        配置里指定了 enter / button 就照办；auto 时用试出来的结论（默认回车）。
        """
        mode = self._configured_send_mode()
        if mode == "auto":
            mode = self._send_key or "enter"
        if mode == "button":
            self._click_send_button()
            return
        self._post_key(VK_RETURN)

    def _may_retry_by_button(self) -> bool:
        """这一轮发送失败后还能不能再点「发送」按钮试。

        配置里固定了发送方式就照办、不去猜；auto 时按键发送落空，说明微信的发送键
        不是回车，改点按钮试一次，已经试成过就不必再试。
        """
        return self._configured_send_mode() == "auto" and self._send_key != "button"

    def _retry_by_send_button(self, contact: str, text: str, summary_before: str) -> bool:
        """按键没发出去时，改点「发送」按钮再发一次，成功了就把结论记住。

        微信允许把发送键改成 Ctrl+回车，那时回车只在正文里插一个换行、消息留在
        输入框里。这里不读微信的设置，直接按结果判定：清空输入框（回车插进去的
        那个换行也一并清掉）、重新输入、点「发送」按钮，摘要更新了就是按钮这条路
        走得通。每次进程只试一次，之后一律点按钮发送。
        """
        if not self._clear_input():
            return False
        if not self._type_text(text):
            self._clear_input()
            return False
        if not self._click_send_button():
            return False
        self._release_foreground()
        time.sleep(0.8)
        if not self._confirm_sent(contact, text, summary_before):
            return False
        self._send_key = "button"
        log.info("按键发不出去、点「发送」按钮发得出去：后续改用按钮发送")
        return True

    def _post_char(self, ch: str) -> None:
        """投递一个可见字符（WM_CHAR，支持中文与 emoji）。"""
        user32 = self._user32_inst()
        if self._hwnd:
            for unit in utf16_units(ch):
                user32.PostMessageW(self._hwnd, WM_CHAR, unit, 1)

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
        lp = self._client_lparam((rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2, target)
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
        return [n for _, n in self._walk() if self._class_of(n) == self.SESSION_CELL_CLASS]

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

    def _fold_cell(self):
        """会话列表里的「折叠的聊天」分组项；没有分组时返回 None。"""
        for _, node in self._walk():
            if self._class_of(node) == self.FOLD_CELL_CLASS:
                return node
        return None

    def _folded_hint(self, contact: str) -> str:
        """切换失败时给出原因：要么确认有折叠分组，要么只说明没选中。

        会话列表里找不到「折叠的聊天」分组项时不能就此下结论——那一行只在
        列表滚到底时才进 UIA 树，读不到它不代表没有折叠会话。
        """
        try:
            folded = self._fold_cell() is not None
        except Exception:  # noqa: BLE001
            folded = False
        if folded:
            return (
                f"「{contact}」可能被收进了微信的「折叠的聊天」："
                "那个分组里的会话不在会话列表里，程序点不到。"
            )
        return (
            f"「{contact}」不在会话列表里，也没能从搜索里选中；"
            "如果它被收进了微信的「折叠的聊天」，那个分组里的会话程序点不到。"
        )

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

    def _search_input(self):
        """搜索框里真正接收输入的编辑框。

        `mmui::XSearchField` 是外框容器，点它中心可能落在图标或空白上、抢不到
        焦点；真正能打字的是它内部的 `mmui::XValidatorTextEdit`。
        """
        return self._find_by_class("mmui::XValidatorTextEdit") or self._find_by_class(
            self.SEARCH_FIELD_CLASS
        )

    def _search_and_select(self, contact: str) -> bool:
        """用主窗口顶部的搜索框找会话并点开（会话列表里没有它时的兜底）。

        搜索框是常驻控件（不是 Ctrl+F 弹层），点一下就能打字。搜索结果项虽然
        作为 `mmui::SearchContentPopover` 的子节点出现在 UIA 树里，但那个弹层是
        **独立顶层窗口**（Qt popup，类名 Qt51514QWindowToolSaveBits）——点结果项
        必须把鼠标消息投给弹层自己的 HWND，投给主窗到不了；这正是「打字能出
        结果、点了却切不过去」的原因。
        """
        field = self._search_input()
        if field is None or not self._post_click(field):
            return False
        time.sleep(0.7)
        for ch in contact:
            self._post_char(ch)
            time.sleep(0.05)
        time.sleep(1.2)
        # 搜索结果弹层是独立顶层窗口，主窗口的 UIA 树里未必能拿到；键盘选中更稳：
        # 上下键把焦点送进结果列表，回车打开第一条。
        if self._open_first_search_hit(contact):
            self._leave_search()
            return self._open_chat_title() == contact
        self._leave_search()
        return False

    def _open_first_search_hit(self, contact: str) -> bool:
        """用键盘打开搜索框的第一条结果。

        搜索框获得焦点时，按一次下键把选择移进结果列表（第一条通常就是联系人），
        回车打开。打开成功的判据是输入框标题变成了目标会话名——弹层里点到什么都
        不比这个硬。
        """
        for keys in (("down", "enter"), ("enter",)):
            for key in keys:
                self._post_vk({"down": VK_DOWN, "enter": VK_RETURN}[key])
                time.sleep(0.45)
            time.sleep(0.9)
            if self._open_chat_title() == contact:
                return True
            log.info("搜索里按 %s 没打开 %s", "+".join(keys), contact)
        return False

    def _post_vk(self, vk: int) -> None:
        """向微信主窗口投递一次按下-抬起。"""
        user32 = self._user32_inst()
        user32.PostMessageW(self._hwnd, WM_KEYDOWN, vk, 1)
        time.sleep(0.02)
        user32.PostMessageW(self._hwnd, WM_KEYUP, vk, KEYUP_LPARAM)

    def _leave_search(self) -> None:
        """清掉搜索框里的关键词。

        只清关键词、不按 Esc：没有弹层时按 Esc，微信会把它当成「关闭主窗口到
        托盘」——窗口再显示出来只会重绘界面，无障碍树不会重建（UIA 从此只剩两级
        节点），只能重启微信。
        """
        user32 = self._user32_inst()
        for _ in range(40):
            user32.PostMessageW(self._hwnd, WM_KEYDOWN, VK_BACK, 1)
            user32.PostMessageW(self._hwnd, WM_KEYUP, VK_BACK, KEYUP_LPARAM)
            time.sleep(0.01)
        time.sleep(0.3)

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

        只在换目标时切换：同联系人连续发送（客户端把一段话拆成多条回复）沿用当前
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
            self._last_error = self._folded_hint(contact)
            log.error(f"[UIA失败] {contact}: {_with_cancel_note(self._last_error)}")
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
        self._last_error = ""
        if not self._foreground_gate(contact):
            self._last_error = "发送通道未启用或被前台占用"
            return False
        with self._send_lock:
            self._fg_before = self._foreground_window()
            try:
                return self._send_text_impl(contact, text)
            except Exception as e:
                self._last_error = f"发送异常，窗口可能已失效: {e}"
                log.error(f"[UIA失败] {contact}: {self._last_error}")
                with self._lock:
                    self._invalidate_window()
                return False
            finally:
                self._restore_foreground(self._fg_before)
                self._fg_before = 0

    def _clear_input(self) -> bool:
        """清空输入框并回读确认。

        两条路依次走：先显式把焦点给输入控件，再投 VK_END + 退格逐字符删；退格
        没生效（输入框里的内容可能是别的程序写进去的，也可能根本收不到投递的
        按键，此时看起来「清不掉」）就退回 UIA 的 ValuePattern 直接置空。
        清空前由调用方确保已切到目标会话，失败时把原因写进 self._last_error。
        """
        box = self._input_box()
        if box is not None:
            try:
                box.SetFocus()
                time.sleep(0.05)
            except Exception as e:  # noqa: BLE001
                log.debug(f"输入框聚焦失败（继续尝试投递按键）: {e}")

        if not self._input_text():
            return True

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
        if not left:
            return True

        # 退格路径没删掉：改用 UIA 直接把值置空
        if box is not None:
            try:
                box.GetValuePattern().SetValue("")
                time.sleep(0.2)
            except Exception as e:  # noqa: BLE001
                log.warning(f"ValuePattern 清空输入框失败: {e}")
            if not self._input_text():
                log.info("输入框由 ValuePattern 清空（退格路径未生效）")
                return True

        left = self._input_text()
        log.warning(f"输入框未能清空，残留 {left[:40]!r}")
        self._last_error = f"输入框有清不掉的残留（{left[:20]}），本次发送已取消"
        return False

    def _send_failure_hint(self) -> str:
        """发送没确认成功时的说明。

        输入框里还留着刚才的内容，说明那一下回车没被当成发送键（被改成了换行），
        或者输入框在发送时被别的窗口抢走了焦点；两种都直接说清楚，并把残留清掉，
        否则残留内容会在下一条消息准备好后被一起发出去。
        """
        leftover = self._input_text()
        if leftover:
            self._clear_input()
            return (
                "按发送键后消息没发出去，输入框里的内容已清掉。"
                "可能是微信的发送键设成了别的组合，也可能发送时输入框被别的窗口抢走了焦点"
            )
        return "发了但会话摘要没更新，消息可能没发出去"

    def _type_text(self, text: str) -> bool:
        """逐字符投递 WM_CHAR 并回读校验。

        输入法会扰动字符（实测输入 `t` 落到输入框里变成 `上`），所以打完必须
        回读比对；不符就清空重打，三次仍不符判定失败。

        走窗口消息投递；文本里有 emoji 或换行时，交给 :meth:`_enter_text` 粘贴。
        """
        for _ in range(3):
            for ch in text:
                self._post_char(ch)
                time.sleep(random.uniform(0.02, 0.04))
            time.sleep(0.3)
            got = self._input_text()
            if got == text:
                return True
            log.warning(f"输入框回读不符（{mismatch_window(text, got)}），清空重打")
            self._clear_input()
        return False

    def _enter_text(self, text: str) -> bool:
        """把正文送进输入框，按内容挑通路（输入框此刻应为空）。

        普通文本逐字符投 WM_CHAR；含 emoji 或换行的正文改走剪贴板粘贴（判据见
        :func:`needs_paste`）——这两类字符 WM_CHAR 传不进去。
        """
        if not needs_paste(text):
            return self._type_text(text)
        log.info("正文含 emoji 或换行，改用剪贴板粘贴投递")
        if self._paste_text(text):
            return True
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

    def _reattach_input(self) -> bool:
        """重新附着微信输入队列。

        修饰键状态是线程自己的键态，只有共享输入队列时微信才看得见，`V` 才不会
        变成字面字符；而发送流程里的前台还原会重新 Attach/Detach。粘贴是唯一
        依赖修饰键的动作，所以每次粘贴前都重新附一次。
        """
        self._detach_input()
        return self._attach_input()

    def _paste_clipboard(self, expect_image: bool) -> bool:
        """用伪造 Ctrl 键态的 Ctrl+V 把剪贴板内容贴进输入框。

        修饰键状态是线程级的，PostMessage 投递的 Ctrl 进不了微信线程的键态，
        V 会变成字面字符。AttachThreadInput 让两个线程共享输入状态，再用
        SetKeyboardState 把本线程的 Ctrl 置为按下，微信就能把随后的 V 解析成
        Ctrl+V（附着由 :meth:`_reattach_input` 在每次粘贴前重新建立）。粘贴结果
        回读校验，不符即当失败。
        """
        user32 = self._user32_inst()
        self._reattach_input()
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

    def _paste_text(self, text: str, read=None) -> bool:
        """把整段文本写进剪贴板再粘贴，并按原样回读校验。

        `read` 指定从哪里回读（默认聊天输入框；搜索框传它自己的读取函数）。
        调用前输入框应当是空的，失败时由调用方决定怎么收场。
        """
        read = read or self._input_text
        if not self._copy_text_to_clipboard(text):
            return False
        if not self._paste_clipboard(expect_image=False):
            return False
        got = read()
        if got == text:
            return True
        log.warning(f"粘贴后回读不符（{mismatch_window(text, got)}）")
        return False

    def _send_text_impl(self, contact: str, text: str) -> bool:
        """发送文本消息。"""
        # 内容检查放在最前：这类正文根本不该发出去，也不该为它去碰窗口。
        if _looks_like_pil_repr(text):
            self._last_error = "正文里出现 PIL 对象的 repr（<PIL.…），已拒绝发送"
            log.warning(f"跳过 PIL 引用消息: {text[:60]}")
            return False

        with self._lock:
            if not self._ensure_window():
                return False

            if not self._prepare_target(contact):
                return False
            self._release_foreground()

            self._attach_input()
            try:
                summary_before = self._session_summary(contact) or ""
                if not self._clear_input():
                    log.error(f"[UIA失败] {contact}: {self._last_error or '输入框未能清空'}")
                    return False
                self._release_foreground()
                if not self._enter_text(text):
                    self._clear_input()
                    self._last_error = "文字未能完整进到输入框，本次发送已取消"
                    log.error(f"[UIA失败] {contact}: {self._last_error}")
                    return False
                self._release_foreground()
                if self._open_chat_title() != contact:
                    self._clear_input()
                    self._last_error = "输入期间会话已改变，本次发送已取消"
                    log.error(f"[UIA失败] {contact}: {self._last_error}")
                    return False

                self._press_send()
                self._release_foreground()
                time.sleep(0.6)

                if not self._confirm_sent(contact, text, summary_before):
                    if self._may_retry_by_button() and self._retry_by_send_button(
                        contact, text, summary_before
                    ):
                        log.info(f"[UIA成功] {contact}: {text[:50]}")
                        return True
                    self._last_error = self._send_failure_hint()
                    log.error(f"[UIA失败] {contact}: {self._last_error}")
                    return False
                log.info(f"[UIA成功] {contact}: {text[:50]}")
                return True
            except Exception as e:
                log.error(f"[UIA失败] {contact}: {e}")
                return False
            finally:
                self._detach_input()

    # ================================================================
    # 发送带 @ 的文字
    # ================================================================

    def send_mention_text(self, contact: str, mentions: list[str], text: str) -> bool:
        """在群里发一条带 @ 的文字。

        微信里的 @ 不是正文，而是一个「提及」对象：必须从成员选择弹层里选出
        来，直接把 `@昵称` 打进输入框只会变成普通文字。所以这条路径是：输入
        `@` → 确认弹层出现 → 输入昵称 → 点中唯一匹配项 → 校验输入框里确实
        带上了这个提及 → 输入正文 → 回车发送。

        任何一步对不上就清空输入框返回 False（fail-closed）：宁可这一条不发，
        也不能把 `@昵称` 当正文发出去，更不能 @ 错人。
        """
        names = [str(n or "").strip() for n in (mentions or []) if str(n or "").strip()]
        if not names:
            self._last_error = "没有可以 @ 的成员名"
            return False
        if not self._foreground_gate(contact):
            self._last_error = "发送通道未启用或被前台占用"
            return False
        with self._send_lock:
            self._fg_before = self._foreground_window()
            try:
                return self._send_mention_impl(contact, names, text)
            except Exception as e:
                self._last_error = f"发送异常，窗口可能已失效: {e}"
                log.error(f"[UIA失败] {contact}: {self._last_error}")
                with self._lock:
                    self._invalidate_window()
                return False
            finally:
                self._restore_foreground(self._fg_before)
                self._fg_before = 0

    def _send_mention_impl(self, contact: str, names: list[str], text: str) -> bool:
        with self._lock:
            if not self._ensure_window():
                return False
            if not self._prepare_target(contact):
                return False
            self._release_foreground()
            self._attach_input()
            try:
                summary_before = self._session_summary(contact) or ""
                if not self._clear_input():
                    self._last_error = self._last_error or "输入框未能清空"
                    log.error(f"[UIA失败] {contact}: {self._last_error}")
                    return False
                self._release_foreground()
                for name in names:
                    if not self._pick_mention(name):
                        self._clear_input()
                        return False
                    self._release_foreground()
                if text and not self._type_suffix(text):
                    self._clear_input()
                    self._last_error = self._last_error or "正文未能完整进到输入框"
                    log.error(f"[UIA失败] {contact}: {self._last_error}")
                    return False
                self._release_foreground()
                if self._open_chat_title() != contact:
                    self._clear_input()
                    self._last_error = "输入期间会话已改变，本次发送已取消"
                    log.error(f"[UIA失败] {contact}: {self._last_error}")
                    return False
                self._press_send()
                self._release_foreground()
                time.sleep(0.6)
                if not self._confirm_sent(contact, text or names[0], summary_before):
                    self._last_error = self._send_failure_hint()
                    log.error(f"[UIA失败] {contact}: {self._last_error}")
                    return False
                log.info(f"[UIA成功] {contact}: @{'、'.join(names)} {text[:40]}")
                return True
            except Exception as e:
                log.error(f"[UIA失败] {contact}: {e}")
                return False
            finally:
                self._detach_input()

    def _type_suffix(self, text: str) -> bool:
        """在已有的 @ 提及之后追加正文，并校验输入框以它结尾。

        `_type_text` 要求输入框内容与正文完全相等；带上提及之后输入框里还有
        `@昵称` 那段，不可能相等。所以这里只要求结尾是本段正文，提及保持原样。
        """
        for _ in range(3):
            for ch in text:
                self._post_char(ch)
                time.sleep(random.uniform(0.02, 0.04))
            time.sleep(0.3)
            if self._input_text().endswith(text):
                return True
            log.warning("正文回读不符合（输入框尾部与正文不一致），清空重打")
            self._clear_input()
        return False

    def _pick_mention(self, name: str) -> bool:
        """把某个成员变成输入框里的「提及」对象。

        弹层是独立顶层窗口且没有标题，`enum_wechat_windows` 看不到它；这里按
        PID 枚举窗口、比对输入 `@` 前后的差集来定位。
        """
        pid = self._self_pid()
        before = set(enum_process_windows(pid, self.isolated_desktop))
        self._post_char("@")
        time.sleep(0.8)
        fresh = [
            hwnd
            for hwnd in enum_process_windows(pid, self.isolated_desktop)
            if hwnd not in before and hwnd != self._hwnd
        ]
        if not fresh:
            self._last_error = f"输入 @ 之后没有出现成员选择弹层，无法在群里 @ {name}"
            log.error(f"[UIA失败] {self._last_error}")
            return False
        # 逐字符收窄弹层：打全名会把列表筛空、弹层直接关掉，之后再没有东西可点，
        # 所以一旦列表只剩一项就停下，用它当目标。
        item = None
        item_hwnd = 0
        for ch in name:
            self._post_char(ch)
            time.sleep(0.12)
            for _ in range(4):
                time.sleep(0.15)
                found, found_hwnd = self._mention_unique(fresh)
                if found is not None:
                    item, item_hwnd = found, found_hwnd
                    break
            if item is not None:
                break
        if item is None:
            self._last_error = f"成员选择弹层里没能选中「{name}」，本次发送已取消"
            log.error(f"[UIA失败] {self._last_error}")
            return False

        # 弹层不激活时，投进去的鼠标消息不生效（实测点了等于没点）
        try:
            self._user32_inst().SetActiveWindow(item_hwnd)
            time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            log.debug(f"激活成员弹层失败（继续尝试点击）: {e}")

        # 选中后输入框里是一个内嵌对象（值里出现 U+FFFC），不是纯文本昵称
        for attempt in range(3):
            if not self._post_click(item, item_hwnd):
                break
            for _ in range(8):
                time.sleep(0.15)
                if self._mention_selected(name):
                    return True
            log.warning(f"第 {attempt + 1} 次点选「{name}」后输入框里还没有提及对象")

        self._last_error = f"成员选择弹层里点中了「{name}」但没变成提及，本次发送已取消"
        log.error(f"[UIA失败] {self._last_error}")
        return False

    def _mention_selected(self, name: str) -> bool:
        """输入框里是否已经放进了提及对象。

        真机上提及是内嵌对象，输入框的值里表现为 U+FFFC（对象占位符）；
        少数路径取到的是「昵称 + U+2005」，两种都认。
        """
        text = self._input_text()
        if "\ufffc" in text:
            return True
        return any(f"{name}{sep}" in text for sep in ("\u2005", "\u200b"))

    def _mention_unique(self, hwnds):
        """弹层里恰好只剩一项时返回该项及其窗口，否则返回 (None, 0)。"""
        for hwnd in hwnds:
            items = self._mention_items(hwnd)
            if len(items) == 1:
                return items[0], hwnd
        return None, 0

    def _mention_items(self, hwnd: int):
        """弹层里所有成员项。"""
        import uiautomation as auto

        try:
            root = auto.ControlFromHandle(hwnd)
        except Exception as e:  # noqa: BLE001
            log.debug(f"读取成员弹层失败: {e}")
            return []
        if root is None:
            return []
        items = []
        for _, node in self._walk(root):
            try:
                if (node.ControlTypeName or "") == "ListItemControl":
                    items.append(node)
            except Exception:  # noqa: BLE001
                continue
        return items

    def _self_pid(self) -> int:
        try:
            user32 = self._user32_inst()
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(self._hwnd, ctypes.byref(pid))
            return int(pid.value)
        except Exception:
            return 0

    # ================================================================
    # 发送图片
    # ================================================================

    def send_image(self, contact: str, image_path: str) -> bool:
        """发送图片。

        顶层防护：通道未启用时直接拒绝；微信重启/COM 失效时安全失败并自动重绑；
        发送结束后把前台还给发送前的前台窗口。
        """
        self._last_error = ""
        if not self._foreground_gate(contact):
            self._last_error = "发送通道未启用或被前台占用"
            return False
        with self._send_lock:
            self._fg_before = self._foreground_window()
            try:
                return self._send_image_impl(contact, image_path)
            except Exception as e:
                self._last_error = f"发送异常，窗口可能已失效: {e}"
                log.error(f"[UIA失败] 图片 -> {contact}: {self._last_error}")
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

                self._press_send()
                self._release_foreground()
                time.sleep(0.8)

                summary = self._session_summary(contact) or ""
                if not _is_image_summary(summary):
                    if self._input_text():
                        self._clear_input()
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
        mp3 只能以"文件卡片"形式发送：双击文件即可播放。适用于把客户端合成的 TTS 语音
        发到微信场景。

        实现方式：用 PowerShell 把文件路径放到剪贴板的 CF_HDROP 格式（即
        "拖入文件"语义），再到聊天窗口 Ctrl+V，微信会识别为文件并显示文件名，
        按 Enter 发送。

        顶层防护：通道未启用时直接拒绝；发送结束后把前台还给发送前的前台窗口。
        """
        self._last_error = ""
        if not self._foreground_gate(contact):
            self._last_error = "发送通道未启用或被前台占用"
            return False
        with self._send_lock:
            self._fg_before = self._foreground_window()
            try:
                return self._send_file_impl(contact, file_path)
            except Exception as e:
                self._last_error = f"发送异常，窗口可能已失效: {e}"
                log.error(f"[UIA失败] 文件 -> {contact}: {self._last_error}")
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

                self._press_send()
                self._release_foreground()
                time.sleep(0.8)

                summary = self._session_summary(contact) or ""
                if name not in summary and "文件" not in summary:
                    if self._input_text():
                        self._clear_input()
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
                capture_output=True,
                timeout=20,
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

    def _copy_text_to_clipboard(self, text: str) -> bool:
        """把文本放进系统剪贴板的 CF_UNICODETEXT。

        含 emoji 的正文只能靠粘贴送出，代价是占用系统剪贴板——与发图片、发文件
        相同。句柄一旦交给系统就不能再释放，失败路径上才由本函数自己释放。
        """
        GMEM_MOVEABLE = 0x0002
        CF_UNICODETEXT = 13
        user32 = self._user32_inst()
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            log.error("写剪贴板失败：GlobalAlloc 没有拿到内存")
            return False
        moved = False
        try:
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                log.error("写剪贴板失败：GlobalLock 失败")
                return False
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(handle)
            if not user32.OpenClipboard(None):
                log.error("写剪贴板失败：剪贴板被别的程序占着")
                return False
            try:
                user32.EmptyClipboard()
                moved = bool(user32.SetClipboardData(CF_UNICODETEXT, handle))
            finally:
                user32.CloseClipboard()
            if not moved:
                log.error("写剪贴板失败：SetClipboardData 被拒绝")
            return moved
        finally:
            if not moved:
                kernel32.GlobalFree(handle)

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
        abs_path = os.path.abspath(path).replace("'", "''")
        return self._run_powershell(
            "Add-Type -AssemblyName System.Drawing;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            f"$img = [System.Drawing.Image]::FromFile('{abs_path}');"
            "[System.Windows.Forms.Clipboard]::SetImage($img);"
            "$img.Dispose()"
        )
