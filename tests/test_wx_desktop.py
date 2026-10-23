"""验证：隔离桌面的配置接通、状态判定与安全边界。

不启动真微信、不建真桌面——只验证不用真微信就能判定的部分：可执行文件定位、
配置开关的读取、状态字典的字段语义、以及「没有实例时各操作不得抛异常或误报」。

真实启动/截图/点击的链路另在带真微信的环境上验证过，不在这个文件里重复。
"""
import json
import importlib
import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO_ROOT, "flandora")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def with_cfg(**overrides):
    """在临时目录里造 config.json，并让 config 只认这一份。

    用 ``FLANDORA_DATA_DIR`` 把默认数据目录钉到临时目录上：否则源码目录里
    一旦真的存在 ``data/config.json``（从源码跑过一次就会有），它会被优先选中，
    测试就会拿到真实环境那份配置。
    """
    d = tempfile.mkdtemp(prefix="wwb-desk-")
    shutil.copy(os.path.join(PKG, "config.example.json"),
                os.path.join(d, "config.json"))
    path = os.path.join(d, "config.json")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.update(overrides)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=4)
    old_env = os.environ.get("FLANDORA_DATA_DIR")
    os.environ["FLANDORA_DATA_DIR"] = d
    try:
        from flandora import config
        importlib.reload(config)   # 同一个进程里换配置必须重载，否则拿到上一份
        return d, config
    finally:
        if old_env is None:
            os.environ.pop("FLANDORA_DATA_DIR", None)
        else:
            os.environ["FLANDORA_DATA_DIR"] = old_env


def main() -> int:
    checks = 0

    # ---------- 1. 可执行文件定位 ----------
    from flandora import wx_desktop

    configured = r"D:\Program Files\Tencent\Weixin\Weixin.exe"
    got = wx_desktop.find_weixin_exe(configured)
    if os.path.isfile(configured):
        assert got == configured, f"配置里的路径应优先返回，实际 {got!r}"
        print("  ok   配置指定路径有效时优先采用")
        checks += 1
    else:
        print("  skip 本机没有默认路径的微信，跳过「配置优先」")
    # 不管本机如何，返回的东西要么是空串，要么必须真实存在
    auto = wx_desktop.find_weixin_exe("")
    assert auto == "" or os.path.isfile(auto), f"自动定位返回了不存在的路径 {auto!r}"
    print(f"  ok   自动定位返回可用路径或空串（{auto or '未找到'}）")
    checks += 1

    # 配置指向不存在的文件时不得抛异常，应退回自动定位
    bogus = wx_desktop.find_weixin_exe(r"C:\definitely\not\here\Weixin.exe")
    assert bogus == "" or os.path.isfile(bogus), bogus
    print("  ok   配置路径失效时退回自动定位，不抛异常")
    checks += 1

    # ---------- 2. 没有实例时的安全边界 ----------
    desk = wx_desktop.IsolatedDesktop("WeBotTestNoOpen")
    assert desk.windows() == [], "未 open 时不应有窗口，也不应报错"
    print("  ok   未打开的桌面 windows() 返回空")
    checks += 1

    desk.close()          # 从未启动过 → 清理必须幂等
    assert desk.pid == 0 and desk.handle is None
    print("  ok   未启动实例 close() 幂等")
    checks += 1

    pids = wx_desktop.weixin_pids()
    assert isinstance(pids, list), pids
    # 「默认桌面上有没有微信」按窗口判断，不看全系统进程：本程序留在隔离桌面上的
    # 微信也算 Weixin.exe，按进程判断会把重启挡下来。
    from flandora.uia_sender import enum_wechat_windows
    assert (wx_desktop.default_desktop_has_wechat()
            == bool(enum_wechat_windows())), "判定口径应只看默认桌面上的窗口"
    print(f"  ok   进程探测返回列表（当前 {len(pids)} 个），默认桌面判定按窗口")
    checks += 1

    assert issubclass(wx_desktop.WeChatAlreadyRunning, wx_desktop.DesktopError)
    print("  ok   WeChatAlreadyRunning 是 DesktopError 的子类")
    checks += 1

    # ---------- 3. 配置接通 ----------
    d, config = with_cfg(isolated_desktop=True,
                         isolated_desktop_name="WeBotTestDesk")
    try:
        mgr = wx_desktop.DesktopManager()
        assert mgr.enabled() is True, "config 里开了隔离桌面，enabled() 应为 True"
        assert mgr.desktop_name() == "WeBotTestDesk", mgr.desktop_name()
        print("  ok   配置开关与桌面名接通")
        checks += 1

        st = mgr.status()
        assert st["enabled"] is True, st
        assert st["running"] is False, "没启动过实例，running 必须为 False"
        assert st["logged_in"] is False, st
        assert st["state"] == "absent", st
        assert st["width"] == 0 and st["height"] == 0, st
        assert st["desktop"] == "WeBotTestDesk", st
        print("  ok   未启动时 status() 语义正确（absent / 不报已登录）")
        checks += 1

        # 没有实例时，截图与点击都该安静地失败，不能抛异常
        assert mgr.screenshot_png() is None, "没有实例不应截到图"
        assert mgr.click(10, 10) is False, "没有实例不应报告点击成功"
        assert mgr.click(-1, -1) is False, "越界坐标不应报告点击成功"
        print("  ok   无实例时截图/点击安静失败，不抛异常")
        checks += 1

        mgr.stop()        # 没启动过 → 必须无害
        print("  ok   未启动时 stop() 无害")
        checks += 1
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---------- 4. 关掉开关时不得报告隔离运行中 ----------
    d, config = with_cfg(isolated_desktop=False)
    try:
        mgr = wx_desktop.DesktopManager()
        assert mgr.enabled() is False, "config 里关了，enabled() 应为 False"
        st = mgr.status()
        assert st["enabled"] is False, st
        # 关掉时按默认桌面上有没有微信来报 running，只要与进程探测一致即可
        has_wx = bool(wx_desktop.weixin_pids())
        assert st["running"] == has_wx, st
        if not has_wx:
            assert st["state"] == "absent", st
            assert st["logged_in"] is False, st
        print("  ok   关闭开关时不虚报隔离运行中")
        checks += 1
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---------- 5. 进程级单例 ----------
    assert wx_desktop.manager() is wx_desktop.manager(), "manager() 必须是同一个对象"
    print("  ok   manager() 返回进程级单例")
    checks += 1

    print(f"{checks}/{checks} 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
