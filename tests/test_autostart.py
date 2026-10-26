# -*- coding: utf-8 -*-
"""开机自启：HKCU Run 项的读写（用假注册表，不碰本机真实设置）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flandora import autostart  # noqa: E402


class _FakeKey:
    def __init__(self, values: dict[str, str]):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeRegistry:
    """够 autostart 用的一小块 winreg。"""

    HKEY_CURRENT_USER = "HKCU"
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values: dict[tuple[str, str], dict[str, str]] = {}

    def _bucket(self, hive, sub) -> dict[str, str]:
        return self.values[(hive, sub)]

    def OpenKey(self, hive, sub, reserved=0, access=0):
        if (hive, sub) not in self.values:
            raise FileNotFoundError(sub)
        return _FakeKey(self._bucket(hive, sub))

    def CreateKeyEx(self, hive, sub, reserved=0, access=0):
        self.values.setdefault((hive, sub), {})
        return _FakeKey(self._bucket(hive, sub))

    def QueryValueEx(self, key, name):
        value = key.values.get(name)
        if value is None:
            raise FileNotFoundError(name)
        return value, self.REG_SZ

    def SetValueEx(self, key, name, reserved, value_type, value):
        assert value_type == self.REG_SZ
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


@pytest.fixture()
def registry(monkeypatch):
    fake = _FakeRegistry()
    monkeypatch.setattr(autostart, "_winreg", lambda: fake)
    return fake


@pytest.fixture()
def frozen_install(monkeypatch, tmp_path):
    (tmp_path / autostart.MANAGER_EXE).write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / autostart.MANAGER_EXE))
    return tmp_path


def test_install_dir_follows_the_exe_when_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "Flandora-Bridge.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert autostart.install_dir() == tmp_path


def test_install_dir_is_the_repo_root_in_source_mode(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.install_dir() == REPO_ROOT


def test_enable_writes_one_quoted_command_and_disable_removes_it(registry, frozen_install):
    assert autostart.status()["enabled"] is False
    ok, message = autostart.enable()
    assert ok, message
    assert autostart.read() == f'"{frozen_install / autostart.MANAGER_EXE}"'
    assert autostart.status()["enabled"] is True
    assert autostart.status()["command"] == f'"{frozen_install / autostart.MANAGER_EXE}"'

    ok, message = autostart.disable()
    assert ok, message
    assert autostart.read() is None
    assert autostart.status()["enabled"] is False


def test_disable_without_a_value_is_not_an_error(registry):
    ok, message = autostart.disable()
    assert ok is True
    assert "开机自启" in message


def test_command_falls_back_to_the_source_entry(monkeypatch, tmp_path):
    (tmp_path / "manager").mkdir()
    (tmp_path / "manager" / "main.py").write_text("# entry", encoding="utf-8")
    monkeypatch.setattr(autostart, "install_dir", lambda: tmp_path)
    command = autostart.manager_command()
    assert command is not None
    assert "manager" in command and "main.py" in command
    assert autostart.status()["reason"] == ""


def test_missing_manager_is_refused_instead_of_writing_nothing(registry, monkeypatch, tmp_path):
    monkeypatch.setattr(autostart, "install_dir", lambda: tmp_path)
    status = autostart.status()
    assert status["command"] == ""
    assert "找不到管理器入口" in status["reason"]
    ok, message = autostart.enable()
    assert ok is False
    assert "找不到管理器入口" in message
    assert registry.values == {}


def test_installer_writes_the_same_run_value():
    """安装器与面板写同一条自启值：改一边就红。"""
    text = (REPO_ROOT / "installer" / "Flandora.iss").read_text(encoding="utf-8")
    assert f'#define AutostartValue "{autostart.VALUE_NAME}"' in text
    assert f'Subkey: "{autostart.RUN_KEY}"' in text
    assert f'#define ManagerExe "{autostart.MANAGER_EXE}"' in text
    assert "Tasks: autostart" in text
    assert "Flags: uninsdeletevalue" in text
