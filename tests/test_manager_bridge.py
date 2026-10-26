# -*- coding: utf-8 -*-
"""管理器侧的状态探测与起停：假端口、假进程、假停止事件。"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MANAGER_DIR = REPO_ROOT / "manager"
for folder in (str(REPO_ROOT), str(MANAGER_DIR)):
    if folder not in sys.path:
        sys.path.insert(0, folder)

import bridge as bridge_mod  # noqa: E402
import paths  # noqa: E402


class _FakeChild:
    def __init__(self, pid: int = 4321):
        self.pid = pid
        self.running = True

    def poll(self):
        return None if self.running else 0


class _FakeStopRequest:
    def __init__(self, child: _FakeChild | None = None, *, fail: bool = False):
        self.child = child
        self.fail = fail
        self.cleared = 0
        self.requested = 0

    def clear(self):
        self.cleared += 1

    def request(self):
        self.requested += 1
        if self.fail:
            raise RuntimeError("事件打不开")
        if self.child is not None:
            self.child.running = False
        return True


@pytest.fixture()
def ports(monkeypatch):
    monkeypatch.setattr(paths, "read_config_ports", lambda: (7999, 4950))
    monkeypatch.setattr(bridge_mod, "port_is_listening", lambda _port: False)


def test_port_is_listening_sees_a_real_listener():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert bridge_mod.port_is_listening(port) is True
    finally:
        listener.close()
    assert bridge_mod.port_is_listening(port) is False


def test_find_listening_pid_reads_netstat(monkeypatch):
    output = (
        "  TCP    127.0.0.1:7999         0.0.0.0:0              LISTENING       4321\n"
        "  TCP    127.0.0.1:4950         127.0.0.1:50000        ESTABLISHED     555\n"
        "  UDP    0.0.0.0:7999           *:*                                    999\n"
    )

    class _Done:
        stdout = output

    monkeypatch.setattr(bridge_mod.paths, "IS_WINDOWS", True)
    monkeypatch.setattr(bridge_mod.subprocess, "run", lambda *a, **k: _Done())
    assert bridge_mod.find_listening_pid(7999) == 4321
    assert bridge_mod.find_listening_pid(4950) is None


def test_pid_alive_knows_its_own_process():
    assert bridge_mod.pid_alive(__import__("os").getpid()) is True


def test_is_running_sees_its_own_child(ports):
    bridge = bridge_mod.BridgeProcess()
    child = _FakeChild()
    bridge.process = child
    assert bridge.is_running() is True
    child.running = False
    assert bridge.is_running() is False
    assert bridge.process is None


def test_is_running_sees_a_listening_port(ports, monkeypatch):
    bridge = bridge_mod.BridgeProcess()
    monkeypatch.setattr(bridge_mod, "port_is_listening", lambda port: port == 4950)
    assert bridge.is_running() is True


def test_start_clears_the_stop_event_before_launching(ports, monkeypatch, tmp_path):
    main_py = tmp_path / "main.py"
    main_py.write_text("# entry", encoding="utf-8")
    monkeypatch.setattr(paths, "bridge_main", lambda: main_py)
    monkeypatch.setattr(paths, "bridge_exe", lambda: tmp_path / "missing.exe")
    monkeypatch.setattr(paths, "python_for_scripts", lambda: Path(sys.executable))
    stop = _FakeStopRequest()
    monkeypatch.setattr(bridge_mod, "_stop_request", lambda: stop)
    launched = {}

    up = {"listening": False}

    class _Popen:
        pid = 12345

        def __init__(self, cmd, **kwargs):
            launched["cmd"] = cmd
            launched["kwargs"] = kwargs
            up["listening"] = True  # 起来之后端口才在听

        def poll(self):
            return None  # 还活着

    monkeypatch.setattr(bridge_mod.subprocess, "Popen", _Popen)
    monkeypatch.setattr(bridge_mod, "port_is_listening", lambda _port: up["listening"])
    bridge = bridge_mod.BridgeProcess()
    ok, message = bridge.start()
    assert ok, message
    assert message == "已启动（PID 12345）"
    assert stop.cleared == 1
    assert launched["cmd"][-1] == str(main_py)
    assert launched["kwargs"]["cwd"] == str(paths.BRIDGE_DIR)


def test_start_refuses_a_second_instance(ports):
    bridge = bridge_mod.BridgeProcess()
    bridge.process = _FakeChild()
    ok, message = bridge.start()
    assert ok is False
    assert "已经在跑" in message


def test_start_reports_a_missing_bridge_program(ports, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "bridge_main", lambda: tmp_path / "nope.py")
    bridge = bridge_mod.BridgeProcess()
    ok, message = bridge.start()
    assert ok is False
    assert "找不到桥接主程序" in message


def test_stop_returns_when_the_bridge_closes_its_ports(ports, monkeypatch):
    bridge = bridge_mod.BridgeProcess()
    child = _FakeChild()
    bridge.process = child
    monkeypatch.setattr(bridge_mod, "_stop_request", lambda: _FakeStopRequest(child))
    ok, message = bridge.stop(timeout=2.0)
    assert ok is True
    assert message == "已停止桥接"


def test_stop_falls_back_to_killing_when_the_event_fails(ports, monkeypatch):
    bridge = bridge_mod.BridgeProcess()
    child = _FakeChild()
    bridge.process = child
    monkeypatch.setattr(bridge_mod, "_stop_request", lambda: _FakeStopRequest(child, fail=True))
    monkeypatch.setattr(
        bridge_mod.BridgeProcess, "_terminate", lambda _self, proc, timeout: "已停止（PID 4321）"
    )
    monkeypatch.setattr(bridge_mod, "find_listening_pid", lambda _port: None)
    ok, message = bridge.stop(timeout=0.3)
    assert ok is True
    assert "发送停止请求失败" in message
    assert "已停止（PID 4321）" in message


def test_restart_reports_both_steps(ports, monkeypatch):
    bridge = bridge_mod.BridgeProcess()
    calls = []

    def fake_stop(timeout=bridge_mod.STOP_TIMEOUT):
        calls.append("stop")
        return True, "已停止桥接"

    def fake_start():
        calls.append("start")
        return True, "已启动（PID 1）"

    monkeypatch.setattr(bridge, "stop", fake_stop)
    monkeypatch.setattr(bridge, "start", fake_start)
    monkeypatch.setattr(bridge_mod.time, "sleep", lambda _s: None)
    ok, message = bridge.restart()
    assert ok is True
    assert calls == ["stop", "start"]
    assert message == "已停止桥接 → 已启动（PID 1）"


def test_stop_when_nothing_runs_is_not_an_error(ports):
    bridge = bridge_mod.BridgeProcess()
    ok, message = bridge.stop()
    assert ok is True
    assert message == "本来就没在跑"


def test_force_kill_reports_failure(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("拒绝访问")

    monkeypatch.setattr(subprocess, "run", boom)
    message = bridge_mod.BridgeProcess._force_kill(777)
    assert "777" in message and "失败" in message


def test_start_reports_a_bridge_that_exits_immediately(ports, monkeypatch, tmp_path):
    """配置拒启时桥接会秒退：托盘要说「立即退出」，不能报「已启动」。"""
    main_py = tmp_path / "main.py"
    main_py.write_text("# entry", encoding="utf-8")
    monkeypatch.setattr(paths, "bridge_main", lambda: main_py)
    monkeypatch.setattr(paths, "bridge_exe", lambda: tmp_path / "missing.exe")
    monkeypatch.setattr(paths, "python_for_scripts", lambda: Path(sys.executable))
    monkeypatch.setattr(bridge_mod, "_stop_request", lambda: _FakeStopRequest())

    class _Popen:
        pid = 54321

        def __init__(self, cmd, **kwargs):
            pass

        def poll(self):
            return 3  # 已退出

    monkeypatch.setattr(bridge_mod.subprocess, "Popen", _Popen)
    bridge = bridge_mod.BridgeProcess()
    ok, message = bridge.start()

    assert ok is False
    assert "立即退出（退出码 3）" in message
    assert bridge.process is None
