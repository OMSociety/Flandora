"""开机自启：当前用户 ``Run`` 项下一条字符串值，指向管理器。

登录时由资源管理器按这条值拉起管理器，管理器再把桥接启动起来。值名固定为
``Flandora``，网页面板里的勾选框与安装器写的是同一个位置、同一个值，所以两边
永远看到同一份状态。写在 HKCU，不需要管理员权限。

冻结形态下安装目录取 ``sys.executable`` 的所在目录：onefile 打包的 ``__file__``
指向解压临时目录，用它算出来的路径开机后再也找不到管理器。
"""

from __future__ import annotations

import sys
from pathlib import Path

VALUE_NAME = "Flandora"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MANAGER_EXE = "Flandora.exe"


def _winreg():
    import winreg

    return winreg


def install_dir() -> Path:
    """安装根目录：打包后是 exe 所在目录，源码形态是仓库根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _windowed_python() -> Path:
    """跑 ``manager/main.py`` 用的解释器：优先同级的 pythonw.exe，不开控制台。"""
    python = Path(sys.executable)
    if sys.platform == "win32":
        windowed = python.with_name("pythonw.exe")
        if windowed.exists():
            return windowed
    return python


def manager_command() -> str | None:
    """开机该执行什么（已带引号），找不到管理器时返回 None。

    调用方要据此拒绝写自启项，而不是写一条指向空气的注册表值。
    """
    root = install_dir()
    exe = root / MANAGER_EXE
    if exe.exists():
        return f'"{exe}"'
    main_py = root / "manager" / "main.py"
    if not main_py.exists():
        return None
    return f'"{_windowed_python()}" "{main_py}"'


def read() -> str | None:
    """注册表里现在那条自启命令；没设过返回 None。"""
    try:
        reg = _winreg()
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_QUERY_VALUE) as key:
            value, _kind = reg.QueryValueEx(key, VALUE_NAME)
    except (FileNotFoundError, OSError):
        return None
    text = str(value).strip()
    return text or None


def status() -> dict:
    """给面板用的状态：开关、它会执行什么、为什么不能用。"""
    command = manager_command()
    reason = ""
    if command is None:
        reason = f"找不到管理器入口（{MANAGER_EXE} 或 manager/main.py），先确认安装完整"
    return {
        "enabled": read() is not None,
        "command": command or "",
        "reason": reason,
    }


def enable() -> tuple[bool, str]:
    command = manager_command()
    if command is None:
        return False, f"找不到管理器入口（{MANAGER_EXE} 或 manager/main.py）"
    try:
        reg = _winreg()
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, command)
    except OSError as exc:
        return False, f"写注册表自启项失败：{exc}"
    return True, f"已设置开机自启（{command}）"


def disable() -> tuple[bool, str]:
    try:
        reg = _winreg()
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return True, "本来就没设开机自启"
    except OSError as exc:
        return False, f"删除注册表自启项失败：{exc}"
    return True, "已取消开机自启"
