# -*- coding: utf-8 -*-
"""桥接进程的启停与状态查询。

管理器只剩托盘菜单，所以这里不碰界面、也不读日志文件：只负责把桥接拉起来、
停干净、回答"现在在不在跑"。

停止走两步：先置位 ``flandora.stop_request`` 的命名事件，让桥接自己收尾
（收件循环、OneBot 入口、隔离桌面里的微信、面板），等它把两个端口都关了再返回；
桥接没响应（旧进程、卡死）才按端口找 PID 强杀。停止时也会收掉"端口上已有的"
别的桥接进程 —— 不留没人管的孤儿进程，是这套东西存在的理由。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

import paths

# 桥接收到停止请求后自尽的宽限：够它 join 收件线程（5s）再关面板
STOP_TIMEOUT = 10.0

# 起桥接后确认它真的挂上端口（或至少还活着）的等待上限：秒退时要把失败报出来。
START_CONFIRM_SECONDS = 3.0


def _stop_request():
    """取桥接侧的停止信号模块（同一个仓库，不在 manager/ 目录里）。"""
    root = str(paths.BRIDGE_DIR)
    if root not in sys.path:
        sys.path.insert(0, root)
    from flandora import stop_request

    return stop_request


def port_is_listening(port: int) -> bool:
    """端口上有没有人在听。

    自己试着绑一下：绑不上说明已经有人占了（比连一下干净 —— 不会在对方那里
    留下一条失败连接），绑得上就说明没人在听。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", int(port)))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def find_listening_pid(port: int) -> Optional[int]:
    """谁在监听这个端口（解析 netstat -ano，找不到返回 None）。"""
    if not paths.IS_WINDOWS:
        return None
    try:
        done = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        return None
    needle = f":{int(port)}"
    for line in done.stdout.splitlines():
        parts = line.split()
        # TCP 与 TCPv6 都是这个命令的输出，行首按前缀认，别把 v6 行漏掉
        if len(parts) < 5 or not parts[0].upper().startswith("TCP"):
            continue
        if not parts[1].endswith(needle) or parts[3].upper() != "LISTENING":
            continue
        try:
            return int(parts[4])
        except ValueError:
            continue
    return None


def pid_alive(pid: int) -> bool:
    """进程还在不在。Windows 下问一句 OpenProcess，不真发信号。"""
    if not paths.IS_WINDOWS:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


class BridgeProcess:
    """桥接的启动、停止、重启与状态。

    `is_running()` 认两种"在跑"：自己启动的子进程还活着，或者 OneBot 端口上
    已经有人在监听（别人起的桥接，管理器照样能停掉它）。
    """

    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self.ob_port, self.web_port = paths.read_config_ports()

    # ---------- 状态 ----------
    def refresh_ports(self) -> tuple[int, int]:
        with self._lock:
            self.ob_port, self.web_port = paths.read_config_ports()
            return self.ob_port, self.web_port

    def _child_alive(self) -> bool:
        if self.process is None:
            return False
        if self.process.poll() is None:
            return True
        self.process = None
        return False

    def _ports_listening(self) -> bool:
        return port_is_listening(self.ob_port) or port_is_listening(self.web_port)

    def is_running(self) -> bool:
        """桥接在不在跑。

        认三种"在跑"：自己启动的子进程还活着、配置里的 OneBot 端口有人在听、
        或者网页面板端口有人在听。后两种是别人（或上一轮）起的桥接 —— 网络入口
        的端口可以自定，所以不能只盯 ob_server_port。
        """
        with self._lock:
            return self._child_alive() or self._ports_listening()

    def listening_pid(self) -> Optional[int]:
        """桥接的监听进程：先看 OneBot 端口，再看网页面板端口。"""
        return find_listening_pid(self.ob_port) or find_listening_pid(self.web_port)

    # ---------- 动作 ----------
    def start(self) -> tuple[bool, str]:
        with self._lock:
            self.refresh_ports()
            if self.is_running():
                return False, "桥接已经在跑了"
            main = paths.bridge_main()
            if not main.exists():
                return False, f"找不到桥接主程序：{main}"
            exe = paths.bridge_exe()
            if exe.exists():
                cmd = [str(exe)]
            else:
                python = paths.python_for_scripts()
                if not python.exists():
                    return False, f"找不到解释器：{python}"
                cmd = [str(python), str(main)]
            # 上一次停止请求还留在事件里，不清掉的话新桥接一起来就自己退出
            try:
                _stop_request().clear()
            except Exception as exc:  # noqa: BLE001  —— 清不掉就让桥接自己判定
                return False, f"重置停止信号失败：{exc}"
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            flags = subprocess.CREATE_NO_WINDOW if paths.IS_WINDOWS else 0
            try:
                self.process = subprocess.Popen(
                    cmd,
                    cwd=str(paths.BRIDGE_DIR),
                    env=env,
                    creationflags=flags,
                )
            except OSError as exc:
                return False, f"启动失败：{exc}"
            # 桥接可能因配置拒启而秒退：等它把端口挂上（或确认进程还在），再报成功
            deadline = time.monotonic() + START_CONFIRM_SECONDS
            while time.monotonic() < deadline:
                code = self.process.poll()
                if code is not None:
                    self.process = None
                    return False, f"桥接启动后立即退出（退出码 {code}），看一眼「打开日志」"
                if self._ports_listening():
                    return True, f"已启动（PID {self.process.pid}）"
                time.sleep(0.2)
            if self._child_alive():
                return True, f"已启动（PID {self.process.pid}）"
            return False, "桥接没有起来，看一眼「打开日志」"

    def stop(self, timeout: float = STOP_TIMEOUT) -> tuple[bool, str]:
        """请桥接自己退出；超过 ``timeout`` 秒还在就按端口找 PID 强杀。"""
        with self._lock:
            self.refresh_ports()
            if not self._child_alive() and not self._ports_listening():
                return True, "本来就没在跑"
            try:
                signalled = _stop_request().request()
            except Exception as exc:  # noqa: BLE001
                signalled = False
                note = f"发送停止请求失败：{exc}"
            else:
                note = "桥接没有响应，已强制结束"

            if signalled:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if not self._child_alive() and not self._ports_listening():
                        return True, "已停止桥接"
                    time.sleep(0.2)

            # 兜底：超时后仍活着的进程直接结束（桥接不响应停止请求时走这里）
            notes: list[str] = [note]
            if self._child_alive():
                notes.append(self._terminate(self.process, 3.0))
                self.process = None
            others = {
                pid
                for pid in (find_listening_pid(self.ob_port), find_listening_pid(self.web_port))
                if pid is not None and pid != os.getpid() and pid_alive(pid)
            }
            for pid in sorted(others):
                notes.append(self._force_kill(pid))
            return True, "；".join(item for item in notes if item)

    def restart(self) -> tuple[bool, str]:
        _, first = self.stop()
        time.sleep(0.5)
        ok, second = self.start()
        return ok, f"{first} → {second}"

    # ---------- 内部 ----------
    @staticmethod
    def _terminate(proc: subprocess.Popen, timeout: float) -> str:
        pid = proc.pid
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
            return f"已停止（PID {pid}）"
        except subprocess.TimeoutExpired:
            proc.kill()
            return f"已强制终止（PID {pid}）"
        except Exception as exc:  # noqa: BLE001
            return f"停止 PID {pid} 失败：{exc}"

    @staticmethod
    def _force_kill(pid: int) -> str:
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
            return f"已终止占用端口的进程（PID {pid}）"
        except Exception as exc:  # noqa: BLE001
            return f"终止 PID {pid} 失败：{exc}"
