# -*- coding: utf-8 -*-
"""发布前验收：交付物身份、运行态与自启项一次核完。

只读默认，不会碰正在跑的服务。``--live`` 才会向桥接发停止信号、等它收尾，
用来验证「停止后端口释放、进程退出」这条契约——这会停掉你正在用的实例。

用法：

    python tools/smoke.py                  只读核对（身份、端口、自启项、打包自检）
    python tools/smoke.py --live           额外做一次真实停止，验证收尾与端口释放
    python tools/smoke.py --payload <目录>  指定要核对的载荷（默认 dist/Flandora-v<版本>）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANAGER_DIR = ROOT / "manager"
for entry in (str(ROOT), str(MANAGER_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import bridge  # noqa: E402  manager/bridge.py：端口探测与进程状态
import paths  # noqa: E402  manager/paths.py：安装目录、端口、日志

BUILD_INFO_NAME = "build-info.json"


def payload_files(root: Path) -> dict[str, str]:
    """载荷里随包分发的文件与 sha256；``data/`` 是运行时可写状态，不参与身份核对。"""
    files: dict[str, str] = {}
    if not root.is_dir():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == BUILD_INFO_NAME or rel.startswith("data/"):
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def identity_problems(payload: Path, head: str) -> list[str]:
    """载荷身份：build-info 能读、提交对得上、每个文件的 sha256 与记录一致。"""
    info_path = payload / BUILD_INFO_NAME
    if not info_path.is_file():
        return [f"载荷里没有 {BUILD_INFO_NAME}：这个包说不出对应哪个提交"]
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"{BUILD_INFO_NAME} 读不出来：{e}"]
    problems: list[str] = []
    if info.get("commit") != head:
        problems.append(f"包里的提交 {info.get('commit')} 与当前 HEAD {head} 不一致")
    if info.get("dirty"):
        problems.append("这个包是在未提交改动上打的（build-info 里 dirty 为真）")
    recorded = info.get("files")
    if not isinstance(recorded, dict):
        return problems + [f"{BUILD_INFO_NAME} 里没有 files 清单"]
    actual = payload_files(payload)
    problems += [f"缺少记录里的 {name}" for name in sorted(set(recorded) - set(actual))]
    problems += [f"清单外的文件 {name}" for name in sorted(set(actual) - set(recorded))]
    problems += [
        f"{name} 的 sha256 与记录不一致"
        for name in sorted(set(recorded) & set(actual))
        if recorded[name] != actual[name]
    ]
    return problems


def port_states(ob_port: int, web_port: int) -> list[tuple[str, int, bool, int | None]]:
    """两个端口的现状：(标签, 端口, 是否在听, 监听 PID)。"""
    states = []
    for label, port in (("OneBot", ob_port), ("面板", web_port)):
        listening = bridge.port_is_listening(port)
        pid = bridge.find_listening_pid(port) if listening else None
        states.append((label, port, listening, pid))
    return states


def describe_ports(states: list[tuple[str, int, bool, int | None]]) -> list[str]:
    return [
        f"{label} {port}：" + (f"在听（PID {pid}）" if listening else "没在听")
        for label, port, listening, pid in states
    ]


def autostart_report() -> tuple[list[str], list[str]]:
    """开机自启项现状；返回（描述行，问题）。"""
    try:
        from flandora import autostart
    except Exception as e:  # noqa: BLE001  取不到自启模块就判失败，不静默跳过
        return [], [f"读不到自启模块：{e}"]
    status = autostart.status()
    command = status.get("command")
    lines = [
        f"自启项：{'已设置' if status.get('enabled') else '未设置'}",
        f"指向：{command or '（找不到管理器入口）'}",
    ]
    problems: list[str] = []
    if status.get("enabled") and command:
        target = command.strip().strip('"')
        if not Path(target).is_file():
            problems.append(f"自启项指向的文件不存在：{target}")
    if status.get("reason"):
        lines.append(f"说明：{status['reason']}")
    return lines, problems


def build_check() -> tuple[str, bool]:
    """跑一次 ``build.py --check``：版本、清单、前端产物、安装脚本引用。"""
    done = subprocess.run(
        [sys.executable, str(ROOT / "build.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines = (done.stdout or "").strip().splitlines()
    return (lines[-1] if lines else "没有输出"), done.returncode == 0


def stop_and_wait(timeout: float = 30.0) -> tuple[bool, str]:
    """向桥接发停止信号，等端口释放与进程退出。"""
    from flandora import stop_request

    if not stop_request.request():
        return False, "发送停止信号失败（命名事件不可用）"
    ob_port, web_port = paths.read_config_ports()
    start = time.monotonic()
    while True:
        states = port_states(ob_port, web_port)
        if not any(listening for _label, _port, listening, _pid in states):
            return True, f"{time.monotonic() - start:.1f}s 内两个端口都已释放"
        if time.monotonic() - start >= timeout:
            return False, f"{timeout:.0f}s 内端口没有全部释放"
        time.sleep(0.3)


def git_head() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flandora 发布前验收")
    parser.add_argument("--live", action="store_true", help="真实停止一次桥接，验证收尾")
    parser.add_argument("--payload", type=Path, default=None, help="要核对的载荷目录")
    args = parser.parse_args(argv)

    from flandora import __version__

    payload = args.payload or (ROOT / "dist" / f"Flandora-v{__version__}")
    ob_port, web_port = paths.read_config_ports()

    results: list[tuple[bool, str, list[str]]] = []

    problems = identity_problems(payload, git_head())
    results.append(
        (not problems, f"交付物身份（{payload.name}）", problems or ["提交与 sha256 都与记录一致"])
    )

    results.append((True, "运行态端口", describe_ports(port_states(ob_port, web_port))))

    lines, problems = autostart_report()
    results.append((not problems, "开机自启项", problems or lines))

    summary, ok = build_check()
    results.append((ok, "打包自检", [summary]))

    if args.live:
        ok, detail = stop_and_wait()
        results.append((ok, "停止桥接并收尾", [detail]))

    failed = 0
    for ok, title, lines in results:
        print(f"[{'OK' if ok else 'FAIL'}] {title}")
        for line in lines:
            print(f"       {line}")
        failed += 0 if ok else 1
    print(f"\n{len(results)} 项检查，{failed} 项失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
