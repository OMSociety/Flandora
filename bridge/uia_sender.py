"""
uia_sender.py — 微信消息发送器
=================================================================

方案：微信窗口激活到前台 + uiautomation SendKeys 键盘模拟。

- 发送前激活微信窗口（会抢焦点，用户已知晓并接受）
- 联系人切换用 Ctrl+F 搜索（前台可靠）
- 文本用 WM_CHAR 逐字符输入（中文可靠，避免剪贴板/输入法问题）
- 发送后尝试最小化微信（尽力减少打扰）
- 清空输入框用 SendKeys Ctrl+A + Delete（避免残留字符）

依赖:
  pip install uiautomation pyperclip
  发送图片需要 PowerShell (Windows 自带)
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
    import config
except Exception:
    config = None

log = logging.getLogger("weflow-bridge")

# Windows 消息 / 虚拟键码
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_BACK = 0x08
VK_DELETE = 0x2E
VK_A = 0x41
VK_F = 0x46
VK_V = 0x56


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


def enum_wechat_windows() -> list[dict]:
    """枚举所有疑似微信主窗口。

    判定：顶层可见窗口，标题含「微信/WeChat」，且类名像 Qt 窗口或进程是
    WeChat.exe/Weixin.exe（过滤浏览器标签页等同名窗口）。
    """
    user32 = ctypes.windll.user32
    results = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
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
    """
    微信消息发送器（SendKeys 键盘模拟 + WM_CHAR 中文输入）。
    """

    WECHAT_TITLES = ["微信", "WeChat"]
    _user32 = None

    @classmethod
    def _user32_inst(cls):
        if cls._user32 is None:
            cls._user32 = ctypes.windll.user32
        return cls._user32

    def __init__(self, search_enabled: bool = True):
        self._lock = threading.Lock()
        self._auto = None
        self._ready = False

        # 微信窗口
        self._window = None
        self._hwnd = 0

        # 最近联系人缓存（相同目标跳过搜索，快速发送）
        self._last_contact = ""

        self.search_enabled = search_enabled

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

    def _find_window(self):
        """定位微信主窗口：优先绑定 config.wechat_hwnd 指定的窗口（多开场景），
        未配置或已失效则回退到按标题自动查找（取第一个）。"""
        auto = self._auto
        root = auto.GetRootControl()
        children = list(root.GetChildren())

        target = 0
        try:
            target = int(getattr(config, "WECHAT_HWND", 0) or 0) if config else 0
        except (TypeError, ValueError):
            target = 0

        if target:
            for w in children:
                try:
                    if w.NativeWindowHandle == target and w.Exists(0.1):
                        self._window = w
                        log.info(f"使用配置的微信窗口 hwnd={target}")
                        return
                except Exception:
                    continue
            log.warning(f"配置的微信窗口 hwnd={target} 已失效，回退自动查找")

        for w in children:
            if w.ClassName in ("Chrome_WidgetWin_1", "CabinetWClass"):
                continue
            for kw in self.WECHAT_TITLES:
                if kw in w.Name:
                    self._window = w
                    return

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
            log.warning("微信窗口未找到")
            self._ready = False
            return False
        self._ready = True
        self._hwnd = self._window.NativeWindowHandle
        return True

    # ================================================================
    # 窗口激活 / 键盘辅助
    # ================================================================

    def _activate(self):
        """激活微信窗口到前台（会抢焦点，用户已知晓并接受）。

        微信 4.1.12 下键盘模拟（SendKeys）依赖前台窗口：若微信不在前台，
        按键会打到其它窗口，导致切换联系人 / 回车发送失效（只有定向投递的
        WM_CHAR 能进输入框）。这里用「Alt 键技巧」绕过窗口激活限制，把微信
        可靠地置为前台（失败则重试），并用 GetForegroundWindow 校验。
        """
        user32 = self._user32_inst()
        hwnd = self._hwnd
        if not hwnd:
            return
        # 若窗口被最小化（发送后 _restore_foreground 会最小化），先恢复，
        # 否则按键会落到前台其它窗口，导致消息发不出去。
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        except Exception:
            pass
        time.sleep(0.15)
        kernel32 = ctypes.windll.kernel32
        for _ in range(4):
            try:
                user32.keybd_event(0x12, 0, 0, 0)  # ALT down
                time.sleep(0.05)
                we_tid = user32.GetWindowThreadProcessId(hwnd, None)
                cur_tid = kernel32.GetCurrentThreadId()
                user32.AttachThreadInput(cur_tid, we_tid, True)
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
                user32.AttachThreadInput(cur_tid, we_tid, False)
                user32.keybd_event(0x12, 0, 2, 0)  # ALT up
            except Exception:
                pass
            time.sleep(0.2)
            if user32.GetForegroundWindow() == hwnd:
                break
        time.sleep(0.2)

    def _post_key(self, vk: int, *modifiers: int) -> None:
        """向微信主窗口发送组合键（PostMessage 辅助，WM_CHAR 场景用）。"""
        user32 = self._user32_inst()
        hwnd = self._hwnd
        if not hwnd:
            return
        for mod in modifiers:
            user32.PostMessageW(hwnd, WM_KEYDOWN, mod, 1)
            time.sleep(0.02)
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 1)
        time.sleep(0.02)
        user32.PostMessageW(hwnd, WM_KEYUP, vk, 1)
        for mod in modifiers:
            user32.PostMessageW(hwnd, WM_KEYUP, mod, 1)
            time.sleep(0.02)

    def _post_char(self, ch: str) -> None:
        """发送一个可见字符（PostMessage WM_CHAR，支持中文）。"""
        user32 = self._user32_inst()
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CHAR, ord(ch), 1)

    def _restore_foreground(self) -> None:
        """发送后尽力最小化微信（减少打扰）。"""
        try:
            user32 = self._user32_inst()
            hwnd = self._hwnd
            if hwnd and not user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception as e:
            log.warning(f"最小化微信失败: {e}")

    # ================================================================
    # 联系人切换
    # ================================================================

    def _search_pane_open(self) -> bool:
        """检测微信搜索面板是否仍开着。

        微信主窗口在打开搜索时会多出一个 'Weixin' 子面板（面板数从 2 变 3），
        选中联系人/搜索结束后该面板关闭（回到 2）。据此判断"切换是否成功"。
        """
        try:
            return len(self._window.GetChildren()) >= 3
        except Exception:
            return True

    def _switch_contact(self, contact: str) -> bool:
        """
        切换到指定联系人/群聊的聊天窗口。

        流程：激活微信 → Ctrl+F 搜索 → 清空 → 粘贴 → Enter（带等待与校验重试）。
        微信 4.1.12 搜索结果渲染较慢，旧逻辑 0.3s 后按 Enter 常选不中，
        这里加长等待，并在 Enter 后检测搜索面板是否关闭；未关闭则重试。
        """
        if not self._ensure_window():
            return False

        try:
            import pyperclip
            auto = self._auto
        except ImportError:
            return False

        self._activate()

        for attempt in range(3):
            # Ctrl+F 打开搜索
            try:
                auto.SendKeys('{Ctrl}f')
            except Exception:
                self._post_key(VK_F, VK_CONTROL)
            time.sleep(0.6)

            # Ctrl+A 全选 → 清空
            try:
                auto.SendKeys('{Ctrl}a')
            except Exception:
                self._post_key(VK_A, VK_CONTROL)
            time.sleep(0.3)

            # 粘贴联系人名
            pyperclip.copy(contact)
            time.sleep(0.2)
            try:
                auto.SendKeys('{Ctrl}v')
            except Exception:
                self._post_key(VK_V, VK_CONTROL)
            time.sleep(1.4)  # 等待搜索结果渲染（4.1.12 较慢）

            # Enter 选中第一个结果
            try:
                auto.SendKeys('{Enter}')
            except Exception:
                self._post_key(VK_RETURN)
            time.sleep(1.3)

            # 验证：搜索面板应关闭（回到 2 个面板）。若仍是 3，说明没切过去。
            if not self._search_pane_open():
                break
            log.warning(f"切换联系人 {contact} 失败（第 {attempt + 1} 次），重试")
        else:
            log.error(f"切换联系人 {contact} 失败：搜索面板未关闭")
            return False

        log.info(f"已切到联系人: {contact}")
        return True

    # ================================================================
    # 发送文字
    # ================================================================

    def send_text(self, contact: str, text: str) -> bool:
        """发送文本消息（顶层防护：微信重启/COM 失效时安全失败并自动重绑）。"""
        try:
            return self._send_text_impl(contact, text)
        except Exception as e:
            log.error(f"[UIA✗] {contact}: 发送异常（窗口可能已失效）: {e}")
            with self._lock:
                self._invalidate_window()
            return False

    def _send_text_impl(self, contact: str, text: str) -> bool:
        """发送文本消息。"""
        with self._lock:
            if not self._ensure_window():
                return False

            # 安全检查：过滤 PIL 引用
            if "<PIL." in text or "PIL." in text:
                log.warning(f"跳过 PIL 引用消息: {text[:60]}")
                return False

            # 切换联系人只在换目标时进行：同联系人连续发送（麦麦一段话拆成多条
            # 回复）时不再抢前台，直接用 PostMessage 在最小化状态下发送。
            need_switch = self.search_enabled and contact and contact != self._last_contact
            if need_switch:
                self._switch_contact(contact)   # 内部会激活窗口（抢前台）
                self._last_contact = contact
                time.sleep(0.3)

            try:
                auto = self._auto

                # 切到新联系人时窗口在前台，用 SendKeys 清空输入框（防残留）。
                if need_switch:
                    time.sleep(random.uniform(0.2, 0.5))
                    try:
                        auto.SendKeys('{Ctrl}a')
                    except Exception:
                        self._post_key(VK_A, VK_CONTROL)
                    time.sleep(0.15)
                    try:
                        auto.SendKeys('{Delete}')
                    except Exception:
                        self._post_key(VK_DELETE)
                    time.sleep(0.15)
                else:
                    # 同联系人连续发送：微信可能在最小化，不做 SendKeys 清空，
                    # 直接靠 PostMessage 输入（上一条成功后输入框已清空）。
                    time.sleep(random.uniform(0.3, 1.0))

                # WM_CHAR 逐字符输入（中文可靠；定向投递，不依赖前台）
                for ch in text:
                    self._post_char(ch)
                    time.sleep(random.uniform(0.02, 0.04))

                # 等待输入完成
                type_wait = min(len(text) * 0.02, 2.0) + random.uniform(0.3, 0.8)
                time.sleep(type_wait)

                # Enter 发送（PostMessage 定向微信句柄；窗口最小化时也能触发发送，
                # 从而同联系人连续发送无需抢前台）
                try:
                    self._post_key(VK_RETURN)
                except Exception:
                    auto.SendKeys('{Enter}')
                time.sleep(0.4)

                # 发送后最小化微信（减少打扰；已最小化则无操作）
                self._restore_foreground()

                log.info(f"[UIA✓] {contact}: {text[:50]}...")
                return True

            except Exception as e:
                log.error(f"[UIA✗] {contact}: {e}")
                return False

    # ================================================================
    # 发送图片
    # ================================================================

    def send_image(self, contact: str, image_path: str) -> bool:
        """发送图片（顶层防护：微信重启/COM 失效时安全失败并自动重绑）。"""
        try:
            return self._send_image_impl(contact, image_path)
        except Exception as e:
            log.error(f"[UIA✗] 图片 → {contact}: 发送异常（窗口可能已失效）: {e}")
            with self._lock:
                self._invalidate_window()
            return False

    def _send_image_impl(self, contact: str, image_path: str) -> bool:
        """发送图片（剪贴板 + 粘贴 + Enter）。"""
        with self._lock:
            if not os.path.isfile(image_path):
                log.error(f"图片不存在: {image_path}")
                return False

            try:
                if not self._ensure_window():
                    return False

                # 确保微信在前台（同联系人连续发送时不会走 _switch_contact）
                self._activate()
                if self.search_enabled and contact and contact != self._last_contact:
                    self._switch_contact(contact)
                    self._last_contact = contact

                auto = self._auto
                time.sleep(random.uniform(0.3, 0.8))

                # 再次强制激活微信到前台，确保粘贴目标正确（防止粘贴到其他窗口）
                self._activate()
                time.sleep(0.3)

                # PowerShell 复制图片到剪贴板
                self._copy_image_to_clipboard(image_path)
                time.sleep(0.3)

                # Ctrl+V 粘贴
                try:
                    auto.SendKeys('{Ctrl}v')
                except Exception:
                    self._post_key(VK_V, VK_CONTROL)
                time.sleep(random.uniform(0.8, 1.5))

                # Enter 发送（PostMessage 定向微信句柄，不依赖前台）
                try:
                    self._post_key(VK_RETURN)
                except Exception:
                    auto.SendKeys('{Enter}')

                self._restore_foreground()

                log.info(f"[UIA✓] 图片 → {contact}: {os.path.basename(image_path)}")
                return True

            except Exception as e:
                log.error(f"[UIA✗] 图片 → {contact}: {e}")
                return False

    def _copy_image_to_clipboard(self, path: str):
        """复制图片到剪贴板（通过 PowerShell）。

        注意：必须显式加载 System.Drawing 程序集，否则 [System.Drawing.Image]
        类型找不到，SetImage 会因参数为 null 而失败（图片发不出去）。
        """
        abs_path = os.path.abspath(path)
        try:
            subprocess.run([
                "powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command",
                f"Add-Type -AssemblyName System.Drawing;"
                f"Add-Type -AssemblyName System.Windows.Forms;"
                f"$img = [System.Drawing.Image]::FromFile('{abs_path}');"
                f"[System.Windows.Forms.Clipboard]::SetImage($img);"
                f"$img.Dispose()"
            ], check=True, timeout=10)
        except Exception as e:
            log.error(f"复制图片到剪贴板失败: {e}")
            raise
