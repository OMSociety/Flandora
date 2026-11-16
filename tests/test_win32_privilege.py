"""验证：进程权限级别查询与「注入是否会被 UIPI 拦下」的判定。

测试机的权限级别不可控（CI 可能以管理员跑），所以这里不假设当前权限，只验证
分支逻辑：读不到令牌视为未知，未知按「更高权限」处理。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora.win32 import privilege  # noqa: E402


def test_self_elevated_returns_bool():
    assert isinstance(privilege.self_elevated(), bool)


def test_process_elevated_unknown_pid_is_none():
    assert privilege.process_elevated(0) is None
    assert privilege.window_process_elevated(0) is None


def test_status_blocks_when_wechat_level_unknown(monkeypatch):
    monkeypatch.setattr(privilege, "self_elevated", lambda: False)
    monkeypatch.setattr(privilege, "window_process_elevated", lambda hwnd: None)

    st = privilege.status(123)

    assert st == {"self_elevated": False, "wechat_elevated": None, "blocked": True}


def test_status_blocks_when_wechat_is_elevated(monkeypatch):
    monkeypatch.setattr(privilege, "self_elevated", lambda: False)
    monkeypatch.setattr(privilege, "window_process_elevated", lambda hwnd: True)

    assert privilege.status(1)["blocked"] is True


def test_status_allows_when_both_medium(monkeypatch):
    monkeypatch.setattr(privilege, "self_elevated", lambda: False)
    monkeypatch.setattr(privilege, "window_process_elevated", lambda hwnd: False)

    st = privilege.status(1)

    assert st["blocked"] is False
    assert st["self_elevated"] is False


def test_status_allows_when_both_elevated(monkeypatch):
    monkeypatch.setattr(privilege, "self_elevated", lambda: True)
    monkeypatch.setattr(privilege, "window_process_elevated", lambda hwnd: True)

    assert privilege.status(1)["blocked"] is False
