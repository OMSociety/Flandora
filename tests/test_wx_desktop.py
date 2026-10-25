"""验证：隔离桌面的配置接通、状态判定与安全边界。

不启动真微信、不建真桌面——只验证不用真微信就能判定的部分：可执行文件定位、
配置开关的读取、状态字典的字段语义、以及「没有实例时各操作不得抛异常或误报」。

真实启动/截图/点击的链路另在带真微信的环境上验证过，不在这个文件里重复。

`wx_desktop` 在调用时用 ``getattr(config, ...)`` 读配置，所以这里直接替换配置
属性即可，不需要 ``importlib.reload(config)``——后者会让同进程里其它模块继续
持有重载前的 config 对象，跑全量测试时互相干扰。

两种跑法等价：

    python -m pytest tests/test_wx_desktop.py -q
    python tests/test_wx_desktop.py
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import config, wx_desktop  # noqa: E402


def _probe_weixin_exe() -> str:
    """找本机的微信可执行文件：环境变量优先，其次注册表与常见安装路径。

    ``FLANDORA_WECHAT_EXE`` 供 CI 与临时环境直接指定；没设就退回桥接自己的定位
    逻辑（注册表 + 各盘 Program Files）。都找不到时返回空串，依赖它的用例 skip 并
    在报告里留下用例名——CI 另有一道把 SKIPPED 打印出来，避免静默跳过。
    """
    for name in ("FLANDORA_WECHAT_EXE", "WECHAT_EXE"):
        value = os.environ.get(name, "").strip()
        if value and os.path.isfile(value):
            return value
    return wx_desktop.find_weixin_exe("")


# 本机若真有微信，才值得验「配置优先」这条；没有就整条 skip，而不是写死一个路径
WECHAT_EXE = _probe_weixin_exe()


@pytest.fixture
def desktop_cfg(monkeypatch):
    """按需设定隔离桌面相关的配置项，用例结束自动还原。"""

    def apply(**overrides):
        for key, value in overrides.items():
            monkeypatch.setattr(config, key, value)

    return apply


# ===== 1. 可执行文件定位 =====


@pytest.mark.skipif(
    not WECHAT_EXE, reason="本机没有微信可执行文件（可用 FLANDORA_WECHAT_EXE 指定路径）"
)
def test_configured_exe_wins_when_exists():
    """配置指定路径有效时优先采用。"""
    assert wx_desktop.find_weixin_exe(WECHAT_EXE) == WECHAT_EXE


def test_auto_locate_returns_existing_path_or_empty():
    """自动定位只返回真实存在的路径，找不到就给空串，不给假路径。"""
    auto = wx_desktop.find_weixin_exe("")
    assert auto == "" or os.path.isfile(auto), f"自动定位返回了不存在的路径 {auto!r}"


def test_bogus_configured_path_falls_back():
    """配置指向不存在的文件时不抛异常，退回自动定位。"""
    bogus = wx_desktop.find_weixin_exe(r"C:\definitely\not\here\Weixin.exe")
    assert bogus == "" or os.path.isfile(bogus), bogus


# ===== 2. 没有实例时的安全边界 =====


def test_unopened_desktop_has_no_windows():
    """未 open 的桌面不应有窗口，也不应报错。"""
    assert wx_desktop.IsolatedDesktop("FlandoraTestNoOpen").windows() == []


def test_close_is_idempotent_on_unstarted():
    """从未启动过的实例，close() 必须幂等（不能因为清理而抛异常）。"""
    desk = wx_desktop.IsolatedDesktop("FlandoraTestNoOpen")
    desk.close()
    assert desk.pid == 0 and desk.handle is None


def test_process_probe_returns_list():
    """进程探测返回列表，不因腾讯系进程缺失而报错。"""
    assert isinstance(wx_desktop.weixin_pids(), list)


def test_default_desktop_check_uses_windows_not_processes():
    """「默认桌面上有没有微信」按窗口判断，不看全系统进程。

    本程序留在隔离桌面上的微信也算 Weixin.exe，按进程判断会把重启挡下来。
    """
    from flandora.win32.windows import enum_wechat_windows

    assert wx_desktop.default_desktop_has_wechat() == bool(enum_wechat_windows())


def test_already_running_is_desktop_error():
    """WeChatAlreadyRunning 必须是 DesktopError 的子类，否则调用方接不住。"""
    assert issubclass(wx_desktop.WeChatAlreadyRunning, wx_desktop.DesktopError)


# ===== 3. 配置接通（开关打开） =====


def test_switch_and_name_are_read_from_config(desktop_cfg):
    """config 里的开关与桌面名接通到 DesktopManager。"""
    desktop_cfg(ISOLATED_DESKTOP=True, ISOLATED_DESKTOP_NAME="FlandoraTestDesk")
    mgr = wx_desktop.DesktopManager()
    assert mgr.enabled() is True, "config 里开了隔离桌面，enabled() 应为 True"
    assert mgr.desktop_name() == "FlandoraTestDesk", mgr.desktop_name()


def test_status_semantics_before_start(desktop_cfg):
    """未启动时 status() 语义正确：absent、不报已登录、尺寸为 0。"""
    desktop_cfg(ISOLATED_DESKTOP=True, ISOLATED_DESKTOP_NAME="FlandoraTestDesk")
    st = wx_desktop.DesktopManager().status()
    assert st["enabled"] is True, st
    assert st["running"] is False, "没启动过实例，running 必须为 False"
    assert st["logged_in"] is False, st
    assert st["state"] == "absent", st
    assert st["width"] == 0 and st["height"] == 0, st
    assert st["desktop"] == "FlandoraTestDesk", st


def test_screenshot_and_click_fail_quietly_without_instance(desktop_cfg):
    """没有实例时，截图与点击都该安静地失败，不能抛异常或误报成功。"""
    desktop_cfg(ISOLATED_DESKTOP=True, ISOLATED_DESKTOP_NAME="FlandoraTestDesk")
    mgr = wx_desktop.DesktopManager()
    assert mgr.screenshot_png() is None, "没有实例不应截到图"
    assert mgr.click(10, 10) is False, "没有实例不应报告点击成功"
    assert mgr.click(-1, -1) is False, "越界坐标不应报告点击成功"


def test_stop_without_start_is_harmless(desktop_cfg):
    """没启动过就 stop() 必须无害。"""
    desktop_cfg(ISOLATED_DESKTOP=True, ISOLATED_DESKTOP_NAME="FlandoraTestDesk")
    wx_desktop.DesktopManager().stop()


# ===== 4. 关掉开关时不得报告隔离运行中 =====


def test_switch_off_does_not_claim_isolated_running(desktop_cfg):
    """关掉开关时按默认桌面上有没有微信来报 running，且与进程探测口径一致。"""
    desktop_cfg(ISOLATED_DESKTOP=False)
    mgr = wx_desktop.DesktopManager()
    assert mgr.enabled() is False, "config 里关了，enabled() 应为 False"
    st = mgr.status()
    assert st["enabled"] is False, st
    has_wx = bool(wx_desktop.weixin_pids())
    assert st["running"] == has_wx, st
    if not has_wx:
        assert st["state"] == "absent", st
        assert st["logged_in"] is False, st


# ===== 5. 进程级单例 =====


def test_manager_is_process_singleton():
    """manager() 必须是同一个对象：桌面句柄只能有一个持有者。"""
    assert wx_desktop.manager() is wx_desktop.manager()


def main() -> int:
    """脚本直跑：本文件的用例依赖 fixture 与 skipif，改委托给 pytest。"""
    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
