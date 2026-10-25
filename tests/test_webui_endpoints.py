"""面板 45 条 /api 路由的冒烟：每条要么真打一次，要么登记为「要有外部条件才打」。

为什么要有这一层：单元测试与路由函数测试都只覆盖被人记得的那些路径，而
``/api/control`` 曾经因为一句错误的 ``from main import ...`` 整条 500，404 条
用例全绿也没发现。这里用真 HTTP 走一遍中间件与路由表，只断言「不是 500」——
它不关心业务结果，只保证接线是通的。

路由表分两档：

- ``call``：本轮真打，无不可逆副作用。
- ``defer``：需要真实微信、UIA、外网或会删真实数据，登记原因后跳过；新增路由必须
  在这里登一档，否则用例失败，逼着人做决定。
"""

import pytest
from fastapi.testclient import TestClient

from flandora import config, state
from flandora import main as bridge_main
from flandora.webui import app as webui_app
from flandora.webui import auth

# 路径 → {方法: 档位}
ROUTES = {
    "/api/auth": {"GET": "call"},
    "/api/login": {"POST": "call"},
    "/api/logout": {"POST": "call"},
    "/api/status": {"GET": "call"},
    "/api/control": {"POST": "defer"},  # 由 test_control_* 专门覆盖
    "/api/update/status": {"GET": "call"},
    "/api/update/check": {"POST": "call"},
    "/api/update/download": {"POST": "defer"},  # 会真的下载安装包
    "/api/update/install": {"POST": "defer"},  # 会替换 exe 并重启进程
    "/api/update/cancel": {"POST": "call"},
    "/api/config": {"GET": "call", "PUT": "call"},
    "/api/config/backup": {"GET": "call"},
    "/api/config/restore": {"POST": "call"},
    "/api/storage": {"GET": "call"},
    "/api/storage/clean": {"POST": "defer"},  # 会删真实文件
    "/api/storage/messages": {"POST": "defer"},  # 会删真实聊天记录
    "/api/messages": {"GET": "call"},
    "/api/networks": {"GET": "call"},
    "/api/logs": {"GET": "call"},
    "/api/stats": {"GET": "call"},
    "/api/contacts": {"GET": "call"},
    "/api/send": {"POST": "call"},  # 无桥接时如实失败，不碰窗口
    "/api/debug/actions": {"GET": "call"},
    "/api/debug/invoke": {"POST": "call"},  # 空动作 → 失败响应
    "/api/debug/events": {"GET": "call"},
    "/api/windows": {"GET": "call"},
    "/api/windows/select": {"POST": "defer"},  # 会在真实窗口上绑发送目标
    "/api/windows/flash": {"POST": "defer"},  # 会闪烁真实窗口
    "/api/desktop/status": {"GET": "call"},
    "/api/desktop/start": {"POST": "defer"},  # 会在隔离桌面上真开微信
    "/api/desktop/stop": {"POST": "defer"},
    "/api/desktop/quit-default-wechat": {"POST": "defer"},  # 会关掉用户正在用的微信
    "/api/desktop/screenshot": {"GET": "defer"},  # 会截真实屏幕
    "/api/desktop/click": {"POST": "defer"},  # 会向真实桌面注入点击
    "/api/wizard/status": {"GET": "call"},
    "/api/wizard/disclaimer": {"POST": "call"},
    "/api/wizard/dbkey/start": {"POST": "defer"},  # 会启动 Frida 注入微信进程
    "/api/wizard/dbkey/cancel": {"POST": "call"},
    "/api/wizard/dbkey/manual": {"POST": "defer"},  # 会用给定口令真去解真实库
    "/api/wizard/imagekey/start": {"POST": "defer"},  # 会启动取图钥线程
    "/api/wizard/imagekey/cancel": {"POST": "call"},
    "/api/wizard/verify": {"POST": "call"},
    "/api/wizard/reset": {"POST": "defer"},  # 会清空向导状态与已存密钥
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """把面板钉在临时目录上，不碰真实 data/、配置与微信目录。"""
    monkeypatch.setattr(webui_app.wechat_account, "current_account", lambda root=None: None)
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)
    monkeypatch.setattr(webui_app.monitor, "attach_log_relay", lambda: None)
    monkeypatch.setattr("flandora.update_check.check_async", lambda: None)
    monkeypatch.setattr(
        "flandora.updater.latest_release", lambda: {"version": "1.0.0", "asset": "x.zip"}
    )
    auth.sessions.clear()
    with config.temporary(tmp_path / "config.json", overrides={"webui_password": ""}):
        with TestClient(webui_app.app) as c:
            yield c
    auth.sessions.clear()


def _registered():
    return {
        (route.path, method)
        for route in webui_app.app.routes
        for method in (getattr(route, "methods", None) or [])
        if route.path.startswith("/api/")
    }


def test_route_table_matches_the_app():
    """双向对账：少了是路由被删；多了是有人加了端点却没决定它归哪一档。"""
    expected = {(path, method) for path, methods in ROUTES.items() for method in methods}
    registered = _registered()

    assert sorted(expected - registered) == [], "路由表里有面板已不存在的端点"
    assert sorted(registered - expected) == [], "新增端点必须登记到 ROUTES 并选一档"


def test_every_endpoint_declares_a_tier():
    for path, methods in ROUTES.items():
        for method, tier in methods.items():
            assert tier in ("call", "defer"), f"{method} {path} 档位写错：{tier}"


def test_safe_endpoints_never_500(client):
    """真打一遍：任何 5xx 都说明接线断了，而不是业务拒绝。"""
    failures = []
    for path, methods in ROUTES.items():
        for method, tier in methods.items():
            if tier != "call":
                continue
            if method == "GET":
                resp = client.get(path)
            else:
                resp = client.request(method, path, json={})
            if resp.status_code >= 500:
                failures.append(f"{method} {path} -> {resp.status_code} {resp.text[:300]}")

    assert failures == []


def test_control_endpoints_reach_the_bridge_module(monkeypatch, client):
    """回归：控制端点必须打到 flandora.main 的起停函数，而不是一个不存在的模块。"""
    calls = []

    monkeypatch.setattr(bridge_main, "start_bridge", lambda: calls.append("start"))
    monkeypatch.setattr(bridge_main, "stop_bridge", lambda: calls.append("stop"))

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(webui_app.asyncio, "sleep", _no_sleep)
    state.paused.clear()

    for action in ("start", "stop", "restart", "pause", "resume"):
        resp = client.post("/api/control", json={"action": action})
        assert resp.status_code == 200, f"{action}: {resp.text}"

    assert calls == ["start", "stop", "stop", "start"]
    assert state.paused.is_set() is False

    unknown = client.post("/api/control", json={"action": "nope"})
    assert unknown.status_code == 400
