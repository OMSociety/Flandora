"""数据目录、config.json 与示例文件的定位。

这里只有纯函数：不读文件、不建目录、不缓存。目录与文件的落盘动作属于
``store.ConfigStore`` 与 ``logging_setup``，本模块只回答「路径应该在哪」。
"""

from __future__ import annotations

import os
import sys


def _package_dir() -> str:
    """``flandora/`` 目录（本文件在 ``flandora/config/paths.py``）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_dir() -> str:
    """``flandora/`` 的上一级；源码运行时 config.json 也可能放在这里。"""
    return os.path.dirname(_package_dir())


def default_data_dir() -> str:
    """按运行方式给出默认数据目录。

    打包后跟随 exe（数据跟着程序走，卸载目录一动就散），源码运行则落在仓库根的
    ``data/``。``FLANDORA_DATA_DIR`` 可以整体改道（测试与便携部署都用它）。
    """
    override = os.environ.get("FLANDORA_DATA_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data")
    return os.path.join(_repo_dir(), "data")


def find_config_path() -> str:
    """按优先级找 config.json；都没有时返回默认数据目录下的路径（不创建文件）。"""
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates += [
            os.path.join(exe_dir, "data", "config.json"),
            os.path.join(exe_dir, "config.json"),
        ]
    candidates += [
        os.path.join(default_data_dir(), "config.json"),
        os.path.join(_repo_dir(), "config.json"),
        os.path.join(os.getcwd(), "config.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


def find_example() -> str | None:
    """找 ``config.example.json``；没有模板时返回 None。"""
    candidates: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "config.example.json"))
    candidates.append(os.path.join(_package_dir(), "config.example.json"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None
