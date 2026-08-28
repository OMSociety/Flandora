# -*- coding: utf-8 -*-
"""MaiBot Bridge 管理器 — 图形界面

双击 启动.bat 即可运行；关闭窗口时最小化到托盘，右键托盘图标可彻底退出。

功能：
- 启动 / 停止 / 重启 bridge（运行 main.py）
- 实时显示：进程状态、端口 7999 连接数、最近日志
- 打开 WebUI（http://127.0.0.1:8766）
- 打开日志目录
- 开机自启动（写 Startup 快捷方式）
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk

# 单实例锁（Windows 具名互斥量）
try:
    import ctypes
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\MaiBotBridgeManager_SingleInstance")
    _already_running = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
except Exception:  # noqa: BLE001
    _mutex_handle = None
    _already_running = False
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

# === 路径 ===
# 兼容两种运行方式：
#   1) 源码：__file__ 指向 bridge-manager/bridge_manager.py
#   2) 打包后：PyInstaller 把 __file__ 指向 _MEIPASS 临时目录，要用 sys.executable 找 exe 所在目录
if getattr(sys, "frozen", False):  # PyInstaller 打包模式
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent

# bridge 项目目录：
#   打包后：必须在 manager.exe 同目录（dist/），因为所有 exe/config/log 都在一起
#   源码：在 ../wechat-weflow-bridge-ob11/
if getattr(sys, "frozen", False):
    BRIDGE_DIR = HERE
else:
    BRIDGE_DIR = HERE.parent / "wechat-weflow-bridge-ob11"

# 找 bridge 主程序：优先 bridge.exe（打包后），回退 main.py（源码）
BRIDGE_EXE = BRIDGE_DIR / ("bridge.exe" if sys.platform == "win32" else "bridge")
BRIDGE_MAIN = BRIDGE_EXE if BRIDGE_EXE.exists() else (BRIDGE_DIR / "main.py")

BRIDGE_LOG = BRIDGE_DIR / "bridge.log"
BRIDGE_PID = BRIDGE_DIR / "bridge.pid"

# Python 解释器：打包后用 sys.executable（同目录 Python）；源码用系统 Python（自动探测）
if getattr(sys, "frozen", False):
    PYTHON_EXE = Path(sys.executable)
else:
    _cand = [
        Path(sys.executable),
        Path(r"C:\Python312\python.exe"),
        Path(r"C:\Python311\python.exe"),
        Path(r"C:\Program Files\Python312\python.exe"),
    ]
    PYTHON_EXE = next((c for c in _cand if c.exists()), _cand[0])

# log_tail：优先 log_tail.exe（打包后），回退 log_tail.py
LOG_TAIL_EXE = HERE / ("log_tail.exe" if sys.platform == "win32" else "log_tail")
LOG_TAIL_PY = HERE / "log_tail.py"
LOG_TAIL_PATH = LOG_TAIL_EXE if LOG_TAIL_EXE.exists() else LOG_TAIL_PY

LOG_DIR = HERE / "logs"
AUTOSTART_NAME = "MaiBotBridgeManager"

# Windows
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import ctypes
    STARTUP_DIR = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup"
    AUTOSTART_LNK = STARTUP_DIR / f"{AUTOSTART_NAME}.lnk"
    APPDATA_AUTOSTART_BAT = STARTUP_DIR / f"{AUTOSTART_NAME}.bat"

# 防止子进程弹出终端窗口（打包成 GUI exe 后没有控制台可继承）
CREATE_NO_WINDOW_FLAG = 0x08000000 if IS_WINDOWS else 0


def _run_powershell(command: str, timeout: int = 5) -> bytes:
    """运行 PowerShell，用 CREATE_NO_WINDOW 防止每次都弹出终端窗口。

    这是 worker 线程频繁调用的路径，绝不能弹出窗口。
    """
    kwargs: dict[str, object] = {"timeout": timeout}
    if IS_WINDOWS:
        kwargs["creationflags"] = CREATE_NO_WINDOW_FLAG
    return subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", command], **kwargs  # type: ignore[arg-type]
    )


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


# === 托盘图标 ===
TRAY_SUPPORTED = False
try:
    if IS_WINDOWS:
        import pystray  # type: ignore
        from PIL import Image, ImageDraw  # type: ignore
        TRAY_SUPPORTED = True
except Exception:  # noqa: BLE001
    TRAY_SUPPORTED = False


def make_tray_icon_image() -> "Image.Image":
    """生成托盘图标：蓝底圆角 + 白色 'B' 字"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 圆角矩形底
    draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(33, 150, 243, 255))
    # B
    draw.text((22, 14), "B", fill=(255, 255, 255, 255))
    return img


# === Bridge 进程控制 ===
class BridgeController:
    """封装 bridge 进程的启动/停止/状态查询

    既能管理 manager 自己启动的子进程，也能"领养"外部已在跑的 bridge（通过 PID）。
    """

    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self.external_pid: Optional[int] = None  # 领养的外部 bridge PID
        self.lock = threading.Lock()
        self.log_tail_proc: Optional[subprocess.Popen] = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        # 主线程用来知道队列里有新数据（reader_thread 在此设置）
        self._log_event = threading.Event()
        # 缓存状态：worker 线程更新，main 线程只读（避免 main 线程跑 PowerShell）
        self._running_cached: bool = False
        self._pid_text_cached: str = "-"
        self._external_flag_cached: bool = False  # True 表示 external PID（"外部"标签）

    # 以下方法**只能在 worker 线程**调用（会跑 PowerShell）——主线程禁止！
    def _find_listening_pid(self, port: int = 7999) -> Optional[int]:
        """查找正在监听 port 的 PID（Windows）"""
        if not IS_WINDOWS:
            return None
        try:
            out = _run_powershell(
                f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess",
                timeout=5,
            )
            s = out.strip().decode("ascii", errors="ignore")
            return int(s) if s.isdigit() else None
        except Exception:  # noqa: BLE001
            return None

    def _alive_pid(self, pid: int) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            out = _run_powershell(
                f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null",
                timeout=5,
            )
            return out.strip().lower() in (b"true", b"True")
        except Exception:  # noqa: BLE001
            return False

    def live_check(self) -> tuple[bool, Optional[int]]:
        """worker 线程调用：检查 bridge 真实状态。返回 (is_running, pid)"""
        # 1) 自管进程
        if self.process is not None and self.process.poll() is None:
            return True, self.process.pid
        # 2) 外部 PID 已知且活
        if self.external_pid is not None and self._alive_pid(self.external_pid):
            return True, self.external_pid
        # 3) 端口扫描兜底
        pid = self._find_listening_pid(7999)
        if pid and pid != os.getpid():
            self.external_pid = pid
            return True, pid
        return False, None

    def refresh_cached_state(self) -> None:
        """worker 线程调用：更新 _running_cached / _pid_text_cached / _external_flag_cached"""
        running, pid = self.live_check()
        with self.lock:
            self._running_cached = running
            if running and pid is not None:
                if self.process is not None and self.process.poll() is None:
                    self._pid_text_cached = str(pid)
                    self._external_flag_cached = False
                else:
                    self._pid_text_cached = str(pid)
                    self._external_flag_cached = True
            else:
                self._pid_text_cached = "-"
                self._external_flag_cached = False

    # 以下方法主线程安全（只读缓存）
    def is_running(self) -> bool:
        return self._running_cached

    def status_text(self) -> str:
        if not self._running_cached:
            return "未运行"
        if self.process is not None and self.process.poll() is None:
            return f"运行中  PID={self._pid_text_cached}（自管）"
        return f"运行中  PID={self._pid_text_cached}（外部，已接管）"

    def _clean_stale_bridge_pid(self) -> None:
        """bridge.pid 存在但 PID 不在跑 → 视为残留，删除之（bridge 自身的启动检查太严苛）"""
        if not IS_WINDOWS or not BRIDGE_PID.exists():
            return
        try:
            content = BRIDGE_PID.read_text(encoding="utf-8", errors="replace").strip()
            if content.isdigit():
                pid = int(content)
                if not self._alive_pid(pid):
                    BRIDGE_PID.unlink()
                    self._log(f"[manager] 清理残留 bridge.pid (旧 PID={pid})")
        except Exception as exc:  # noqa: BLE001
            self.log_queue.put(f"[manager] 清理 bridge.pid 失败: {exc}\n")

    def start(self) -> tuple[bool, str]:
        with self.lock:
            if self.is_running():
                return False, "bridge 已在运行"
            if not PYTHON_EXE.exists():
                return False, f"找不到 Python: {PYTHON_EXE}"
            if not BRIDGE_MAIN.exists():
                return False, f"找不到 bridge 主程序: {BRIDGE_MAIN}"
            self._clean_stale_bridge_pid()
            try:
                flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"
                # 打包后：直接调 bridge.exe；源码：调 python main.py
                if BRIDGE_EXE.exists():
                    cmd = [str(BRIDGE_EXE)]
                else:
                    cmd = [str(PYTHON_EXE), str(BRIDGE_MAIN)]
                self.process = subprocess.Popen(
                    cmd,
                    cwd=str(BRIDGE_DIR),
                    env=env,
                    creationflags=flags,
                )
                self._start_log_tail()
                return True, f"已启动 PID={self.process.pid}"
            except Exception as exc:  # noqa: BLE001
                return False, f"启动失败: {exc}"

    def _start_log_tail(self) -> None:
        if not IS_WINDOWS:
            return
        try:
            flags = subprocess.CREATE_NO_WINDOW
            # 打包后：直接调 log_tail.exe；源码：调 python log_tail.py
            if LOG_TAIL_EXE.exists():
                cmd = [str(LOG_TAIL_EXE), str(BRIDGE_LOG)]
            else:
                cmd = [str(PYTHON_EXE), "-u", str(LOG_TAIL_PY), str(BRIDGE_LOG)]
            self.log_tail_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(HERE),
                creationflags=flags,
            )
            threading.Thread(target=self._reader_thread, args=(self.log_tail_proc,), daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            self.log_queue.put(f"[manager] 启动 log_tail 失败: {exc}\n")

    def _stop_log_tail(self) -> None:
        try:
            if self.log_tail_proc and self.log_tail_proc.poll() is None:
                self.log_tail_proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        self.log_tail_proc = None

    def stop(self, timeout: float = 5.0) -> tuple[bool, str]:
        with self.lock:
            msg_parts: list[str] = []
            # 1) 停自己启动的子进程
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=timeout)
                    msg_parts.append("自管进程已停止")
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    msg_parts.append("自管进程已强制终止")
                except Exception as exc:  # noqa: BLE001
                    msg_parts.append(f"自管进程停止失败: {exc}")
            self.process = None
            # 2) 停外部领养的进程
            if self.external_pid is not None and self._alive_pid(self.external_pid):
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(self.external_pid)],
                        capture_output=True, timeout=5,
                    )
                    msg_parts.append(f"外部 PID={self.external_pid} 已终止")
                except Exception as exc:  # noqa: BLE001
                    msg_parts.append(f"外部进程停止失败: {exc}")
            self.external_pid = None
            # 3) 兜底：端口仍被占用就强制 kill 监听者
            still = self._find_listening_pid(7999)
            if still and still != os.getpid():
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(still)],
                        capture_output=True, timeout=5,
                    )
                    msg_parts.append(f"兜底 kill PID={still}")
                except Exception:  # noqa: BLE001
                    pass
            # 4) 停 log_tail
            self._stop_log_tail()
            return True, "；".join(msg_parts) or "未运行，无需停止"

    def restart(self) -> tuple[bool, str]:
        ok1, m1 = self.stop()
        time.sleep(0.5)
        ok2, m2 = self.start()
        return ok2, f"{m1} → {m2}"

    def _reader_thread(self, proc: subprocess.Popen) -> None:
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                try:
                    text = line.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    text = str(line)
                self.log_queue.put(text)
                self._log_event.set()  # 通知主线程有新日志
        except Exception:  # noqa: BLE001
            pass


# === GUI ===
class BridgeManagerApp:
    POLL_INTERVAL_MS = 1500
    MAX_LOG_LINES = 800

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MaiBot Bridge 管理器")
        self.root.geometry("720x560")
        self.root.minsize(620, 460)

        self.controller = BridgeController()

        # 后台状态采集线程（避免 PowerShell 阻塞 Tk 主线程）
        self._stat_snapshot: dict[str, object] = {
            "pid": None, "port_clients": -1, "weflow": "?", "wechat": "?",
        }
        self._stat_lock = threading.Lock()
        self._stat_dirty = threading.Event()
        self._stat_thread_stop = threading.Event()
        self._stat_thread = threading.Thread(target=self._stat_worker, daemon=True)
        self._stat_thread.start()

        # 日志事件直接挂在 controller 上（reader_thread 也会 set）

        self._tray_icon = None
        self._tray_thread: Optional[threading.Thread] = None
        self._really_quit = False
        self._log_buffer: list[str] = []
        self._last_log_flush_ms: int = 0  # 日志节流刷新时间戳

        self._build_ui()
        self._poll_status()
        # 窗口关闭 = 最小化到托盘（由 _on_close 处理）
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # 启动时自动 start bridge（首次运行更顺手）
        # 等 worker 线程跑完第一轮采集再 auto-start（1500ms），避免重复启动外部 bridge
        self.root.after(1500, self._auto_start)

    # ---------- UI 构建 ----------
    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use(style.theme_use())

        # === 顶部：状态 + 按钮 ===
        top = ttk.Frame(self.root, padding=(12, 10))
        top.pack(fill="x")

        self.status_var = tk.StringVar(value="状态：未知")
        self.status_label = ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 11, "bold"))
        self.status_label.pack(side="left")

        btn_box = ttk.Frame(top)
        btn_box.pack(side="right")
        self.btn_start = ttk.Button(btn_box, text="▶ 启动", width=8, command=self._cmd_start)
        self.btn_stop = ttk.Button(btn_box, text="■ 停止", width=8, command=self._cmd_stop)
        self.btn_restart = ttk.Button(btn_box, text="↻ 重启", width=8, command=self._cmd_restart)
        self.btn_start.pack(side="left", padx=2)
        self.btn_stop.pack(side="left", padx=2)
        self.btn_restart.pack(side="left", padx=2)

        # === 中部：信息卡 ===
        info = ttk.LabelFrame(self.root, text="运行信息", padding=10)
        info.pack(fill="x", padx=12, pady=(0, 8))

        self.info_vars = {
            "PID": tk.StringVar(value="-"),
            "端口 7999 客户端": tk.StringVar(value="-"),
            "WeFlow (5031)": tk.StringVar(value="-"),
            "微信进程": tk.StringVar(value="-"),
            "日志文件": tk.StringVar(value="-" if not BRIDGE_LOG.exists() else str(BRIDGE_LOG)),
        }
        for i, (k, v) in enumerate(self.info_vars.items()):
            ttk.Label(info, text=k + "：").grid(row=i, column=0, sticky="w", padx=(0, 8), pady=1)
            ttk.Label(info, textvariable=v, foreground="#0a66c2").grid(row=i, column=1, sticky="w")

        # === 工具栏 ===
        tools = ttk.Frame(self.root, padding=(12, 0, 12, 6))
        tools.pack(fill="x")
        ttk.Button(tools, text="🌐 打开 WebUI", command=self._open_webui).pack(side="left", padx=2)
        ttk.Button(tools, text="📁 打开日志目录", command=self._open_log_dir).pack(side="left", padx=2)
        ttk.Button(tools, text="🧹 清空日志", command=self._clear_log).pack(side="left", padx=2)
        ttk.Button(tools, text="🪟 最小化到托盘", command=self._hide_to_tray).pack(side="left", padx=2)
        self.autostart_var = tk.BooleanVar(value=self._is_autostart_enabled())
        ttk.Checkbutton(
            tools, text="开机自启动", variable=self.autostart_var, command=self._toggle_autostart
        ).pack(side="right", padx=2)

        # === 日志窗口 ===
        log_box = ttk.LabelFrame(self.root, text="bridge 日志（实时）", padding=6)
        log_box.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(
            log_box, height=14, state="disabled", wrap="none", font=("Consolas", 9)
        )
        self.log_text.pack(fill="both", expand=True)

        # === 底部 ===
        bottom = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        bottom.pack(fill="x")
        self.hint_var = tk.StringVar(value="关闭窗口会最小化到托盘；右键托盘图标可彻底退出")
        ttk.Label(bottom, textvariable=self.hint_var, foreground="#666").pack(side="left")
        ttk.Button(bottom, text="❌ 退出", command=self._quit).pack(side="right")

    # ---------- 操作 ----------
    def _auto_start(self) -> None:
        if not self.controller.is_running():
            self._cmd_start()

    def _cmd_start(self) -> None:
        ok, msg = self.controller.start()
        self._log(f"[操作] {msg}")
        if not ok:
            messagebox.showerror("启动失败", msg)

    def _cmd_stop(self) -> None:
        ok, msg = self.controller.stop()
        self._log(f"[操作] {msg}")
        if not ok:
            messagebox.showerror("停止失败", msg)

    def _cmd_restart(self) -> None:
        ok, msg = self.controller.restart()
        self._log(f"[操作] {msg}")
        if not ok:
            messagebox.showerror("重启失败", msg)

    def _open_webui(self) -> None:
        if IS_WINDOWS:
            os.startfile("http://127.0.0.1:8766")  # noqa: S606
        return None

    def _open_log_dir(self) -> None:
        target = LOG_DIR if LOG_DIR.exists() else BRIDGE_DIR
        if IS_WINDOWS:
            os.startfile(str(target))  # noqa: S606
        return None

    def _clear_log(self) -> None:
        self._log_buffer.clear()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self._really_quit:
            return
        if not TRAY_SUPPORTED:
            # 没有托盘支持时直接退出
            self._quit()
            return
        self._hide_to_tray()

    def _hide_to_tray(self) -> None:
        if not TRAY_SUPPORTED:
            messagebox.showinfo("提示", "当前环境没有 pystray，无法托盘。直接最小化窗口。")
            self.root.iconify()
            return
        self.root.withdraw()
        self._ensure_tray()

    def _ensure_tray(self) -> None:
        if self._tray_icon is not None:
            return

        icon_img = make_tray_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", lambda _: self.root.after(0, self._show_window), default=True),
            pystray.MenuItem("▶ 启动", lambda _: self.root.after(0, self._cmd_start)),
            pystray.MenuItem("■ 停止", lambda _: self.root.after(0, self._cmd_stop)),
            pystray.MenuItem("↻ 重启", lambda _: self.root.after(0, self._cmd_restart)),
            pystray.MenuItem("🌐 打开 WebUI", lambda _: self._open_webui()),
            pystray.MenuItem("📁 日志目录", lambda _: self._open_log_dir()),
            pystray.MenuSeparator(),
            pystray.MenuItem("❌ 退出", lambda _: self.root.after(0, self._quit)),
        )
        self._tray_icon = pystray.Icon("MaiBotBridge", icon_img, "MaiBot Bridge 管理器", menu)
        self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
        self._tray_thread.start()

    def _show_window(self) -> None:
        self.root.after(0, self._deiconify)

    def _deiconify(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit(self) -> None:
        self._really_quit = True
        self._stat_thread_stop.set()
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
        except Exception:  # noqa: BLE001
            pass
        # 退出时停止 bridge
        try:
            self.controller.stop()
        except Exception:  # noqa: BLE001
            pass
        self.root.after(50, self.root.destroy)

    # ---------- 自启动 ----------
    def _is_autostart_enabled(self) -> bool:
        if not IS_WINDOWS:
            return False
        return APPDATA_AUTOSTART_BAT.exists() or AUTOSTART_LNK.exists()

    def _toggle_autostart(self) -> None:
        if self.autostart_var.get():
            self._enable_autostart()
        else:
            self._disable_autostart()

    def _enable_autostart(self) -> None:
        if not IS_WINDOWS:
            return
        # 写一个 bat 到 Startup 目录，绕过快捷方式依赖
        bat = APPDATA_AUTOSTART_BAT
        bat.write_text(
            f'@echo off\r\n'
            f'cd /d "{HERE}"\r\n'
            f'start "" "{PYTHON_EXE}" "{HERE / "bridge_manager.py"}"\r\n',
            encoding="gbk",
        )
        self._log(f"[自启] 已写入 {bat}")

    def _disable_autostart(self) -> None:
        for f in (APPDATA_AUTOSTART_BAT, AUTOSTART_LNK):
            try:
                if f.exists():
                    f.unlink()
                    self._log(f"[自启] 已删除 {f}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"[自启] 删除失败: {exc}")

    # ---------- 后台状态采集 ----------
    def _stat_worker(self) -> None:
        """独立线程做 PowerShell 查询，避免阻塞 Tk 主线程导致拖动卡顿。

        频率控制：每秒 1 次主 tick，但只在主 tick 跑 PowerShell；
        中间的轻量 tick（每 1s）只刷新 cached state 里的进程存活（用本地 API 免 PowerShell）。
        客户端数 / WeFlow / 微信查询每 3s 一次。
        """
        tick = 0
        while not self._stat_thread_stop.is_set():
            try:
                # 1) 维护 log_tail 健康（死了就重启）
                if self.controller.log_tail_proc is None or self.controller.log_tail_proc.poll() is not None:
                    self.controller._start_log_tail()

                # 2) 核心：cached state 更新（含 PowerShell 调用，但只每 3 tick 一次）
                if tick % 3 == 0:
                    self.controller.refresh_cached_state()

                snapshot: dict[str, object] = {"pid": None}
                if self.controller.is_running():
                    snapshot["pid"] = (
                        self.controller._pid_text_cached,
                        self.controller._external_flag_cached,
                    )

                # 3) 端口客户端数（每 3 tick 一次，≈3s）
                if tick % 3 == 0:
                    snapshot["port_clients"] = self._count_clients()
                    snapshot["weflow"] = self._probe_weflow()
                    snapshot["wechat"] = self._count_wechat()

                with self._stat_lock:
                    self._stat_snapshot.update(snapshot)
                    self._stat_dirty.set()
            except Exception as exc:  # noqa: BLE001
                self.controller.log_queue.put(f"[manager] stat_worker err: {exc}\n")

            tick += 1
            self._stat_thread_stop.wait(timeout=1.0)

    def _poll_status(self) -> None:
        try:
            if self._stat_dirty.is_set():
                with self._stat_lock:
                    snap = dict(self._stat_snapshot)
                    self._stat_dirty.clear()

                running = self.controller.is_running()
                self.status_var.set("状态：" + self.controller.status_text())
                self.status_label.configure(foreground=("#1a7f37" if running else "#9a6700"))

                pid_info = snap.get("pid") if running else None
                if isinstance(pid_info, tuple):
                    self.info_vars["PID"].set(pid_info[0] + ("（外部）" if pid_info[1] else ""))
                else:
                    self.info_vars["PID"].set("-")
                self.info_vars["端口 7999 客户端"].set(str(snap.get("port_clients", "-")))
                self.info_vars["WeFlow (5031)"].set(snap.get("weflow", "?"))
                self.info_vars["微信进程"].set(snap.get("wechat", "?"))

                state_run = "disabled" if running else "normal"
                state_stop = "normal" if running else "disabled"
                self.btn_start.configure(state=state_run)
                self.btn_stop.configure(state=state_stop)
                self.btn_restart.configure(state=state_stop)

            # 日志：节流刷新（避免频繁触发 Text widget 重绘）
            now_ms = int(time.time() * 1000)
            if self.controller._log_event.is_set() and (now_ms - self._last_log_flush_ms) >= 150:
                self._flush_log()
                self._last_log_flush_ms = now_ms
        finally:
            # 主线程空闲为主，0.5s 一次轮询
            self.root.after(500, self._poll_status)

    def _count_clients(self) -> int:
        """7999 上 MaiBot 客户端连接数（Established 数）"""
        if not IS_WINDOWS:
            return 0
        try:
            out = _run_powershell(
                "Get-NetTCPConnection -LocalPort 7999 -State Established | Measure-Object | Select-Object -ExpandProperty Count",
                timeout=5,
            )
            return int(out.strip().decode("ascii", errors="ignore"))
        except Exception:  # noqa: BLE001
            return -1

    def _probe_weflow(self) -> str:
        if not IS_WINDOWS:
            return "?"
        try:
            out = _run_powershell(
                "(Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:5031/api/v1/health).StatusCode",
                timeout=5,
            )
            return "✓ 正常" if out.strip() == b"200" else f"异常 {out.strip().decode(errors='ignore')}"
        except Exception:  # noqa: BLE001
            return "✗ 离线"

    def _count_wechat(self) -> str:
        if not IS_WINDOWS:
            return "?"
        try:
            out = _run_powershell(
                "(Get-Process Weixin -ErrorAction SilentlyContinue | Measure-Object).Count",
                timeout=5,
            )
            n = int(out.strip().decode("ascii", errors="ignore"))
            return f"{n} 个" if n > 0 else "✗ 未启动"
        except Exception:  # noqa: BLE001
            return "?"

    # ---------- 日志 ----------
    def _log(self, text: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._append(f"[{ts}] {text}\n")

    def _drain_log_queue(self) -> None:
        """已被 _flush_log 替代（保留以备后用）"""
        pass

    def _flush_log(self) -> None:
        """主线程调用：把队列里的新行刷到 Text 控件（增量更新，不全量重绘）"""
        if not self.controller._log_event.is_set():
            return
        new_lines: list[str] = []
        try:
            while True:
                new_lines.append(self.controller.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.controller._log_event.clear()
        if not new_lines:
            return
        self._log_buffer.extend(new_lines)
        if len(self._log_buffer) > self.MAX_LOG_LINES:
            self._log_buffer = self._log_buffer[-self.MAX_LOG_LINES:]
        # 一次性 update + 一次性 insert（避免逐行触发重绘）
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "".join(new_lines))
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > self.MAX_LOG_LINES:
            extra = line_count - self.MAX_LOG_LINES
            self.log_text.delete("1.0", f"{extra + 1}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _append(self, text: str) -> None:
        """被 _log 等调用：把内容塞进队列并标记 dirty；buffer 由 _flush_log 维护"""
        self.controller.log_queue.put(text)
        self.controller._log_event.set()


def main() -> None:
    if _already_running:
        try:
            import tkinter.messagebox as mb
            mb.showwarning("MaiBot Bridge", "管理器已在运行，请到系统托盘查看。")
        except Exception:  # noqa: BLE001
            pass
        return
    root = tk.Tk()
    try:
        # 应用图标（窗口标题栏）
        if TRAY_SUPPORTED:
            root.iconphoto(True, tk.PhotoImage(data=_icon_photo_png()))
    except Exception:  # noqa: BLE001
        pass
    BridgeManagerApp(root)
    root.mainloop()


def _icon_photo_png() -> bytes:
    """生成窗口标题栏小图标（PNG bytes）"""
    from io import BytesIO
    img = make_tray_icon_image().resize((32, 32))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    main()