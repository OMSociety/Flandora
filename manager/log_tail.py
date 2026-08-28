# -*- coding: utf-8 -*-
"""日志 tail —— 由 bridge_manager 通过 subprocess 拉起，把 bridge.log 增量输出。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        return 1
    log_path = Path(sys.argv[1])

    # 关键：Windows 上 Python 默认 stdout 是 cp936，bridge.log 里的非 GBK 字符
    # （emoji/部分中文生僻字/特殊符号）会让 write 抛 UnicodeEncodeError，
    # 进而被静默吞掉导致 GUI 看不到任何日志。强制 UTF-8 + errors='replace'。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    pos = 0
    if log_path.exists():
        pos = log_path.stat().st_size
        # 启动时先把文件末尾最多 100 行 dump 出来，便于 GUI 立刻看到历史
        try:
            with log_path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                end = f.tell()
                start = max(0, end - 64 * 1024)  # 64KB
                f.seek(start)
                tail = f.read(end - start).decode("utf-8", errors="replace")
                lines = tail.splitlines()
                for line in lines[-100:]:
                    sys.stdout.write(line + "\n")
                sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[log_tail] init dump error: {exc}\n")
            sys.stderr.flush()
    while True:
        try:
            if log_path.exists():
                size = log_path.stat().st_size
                if size < pos:
                    pos = 0  # 文件被截断/重建
                if size > pos:
                    with log_path.open("r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        for line in f:
                            sys.stdout.write(line)
                            sys.stdout.flush()
                    pos = size
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[log_tail error] {exc}\n")
            sys.stderr.flush()
        time.sleep(0.3)


if __name__ == "__main__":
    sys.exit(main())