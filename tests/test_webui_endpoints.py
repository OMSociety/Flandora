"""面板全部 /api 路由的冒烟：每条要么真打一次，要么登记为「要有外部条件才打」。

为什么要有这一层：单元测试与路由函数测试都只覆盖被人记得的那些路径，而
``/api/control`` 曾经因为一句错误的 ``from main import ...`` 整条 500，404 条
用例全绿也没发现。这里用真 HTTP 走一遍中间件与路由表，只断言「不是 500」——
它不关心业务结果，只保证接线是通的。

路由表分两档，来源是各业务组自己登记的表（``flandora.webui.api.ALL_ROUTES()``）：

- ``call``：本轮真打，无不可逆副作用。
- ``defer``：需要真实微信、UIA、外网或会删真实数据，登记原因后跳过；新增路由必须
  在所属组的 ``ROUTES`` 里登一档，否则本文件的用例失败，逼着人做决定。
"""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from flandora import config
from flandora import main as bridge_main
from flandora.runtime import LIFECYCLE, events
from flandora.webui import app as webui_app
from flandora.webui import auth, deps
from flandora.webui.api import ALL_ROUTES

# 路径 → {方法: 档位}；由各组路由器的 ROUTES 聚合而来
ROUTES = ALL_ROUTES()


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """把面板钉在临时目录上，不碰真实 data/、配置与微信目录。"""
    monkeypatch.setattr(deps.wechat_account, "current_account", lambda root=None: None)
    monkeypatch.setattr(events, "notify_status", lambda: None)
    monkeypatch.setattr(events, "attach_log_relay", lambda: None)
    monkeypatch.setattr("flandora.update_check.check_async", lambda: None)
    monkeypatch.setattr(
        "flandora.updater.latest_release", lambda: {"version": "1.0.0", "asset": "x.zip"}
    )
    auth.sessions.clear()
    with config.temporary(tmp_path / "config.json", overrides={"webui_password": ""}):
        with TestClient(webui_app.app) as c:
            yield c
    auth.sessions.clear()


def _flatten(routes):
    """展开 include 进来的路由器，拿到真正的路由对象。

    新版 FastAPI 的 ``include_router`` 在 ``app.routes`` 里只留一个
    ``_IncludedRouter`` 引用，真正的 ``APIRoute`` 挂在它引用的路由器上；
    直接摊平的形态里两者在同一层。两种形态都要能数清楚。
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _flatten(included.routes)
        else:
            yield route


def _registered():
    return {
        (route.path, method)
        for route in _flatten(webui_app.app.routes)
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

    def fake_start():
        calls.append("start")
        LIFECYCLE.begin_start()

    def fake_stop():
        calls.append("stop")
        LIFECYCLE.begin_stop()
        LIFECYCLE.mark_stopped()

    monkeypatch.setattr(bridge_main, "start_bridge", fake_start)
    monkeypatch.setattr(bridge_main, "stop_bridge", fake_stop)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    seen = {}
    for action in ("start", "stop", "restart", "pause", "resume"):
        resp = client.post("/api/control", json={"action": action})
        assert resp.status_code == 200, f"{action}: {resp.text}"
        seen[action] = resp.json()

    assert calls == ["start", "stop", "stop", "start"]
    # 暂停只影响在线口径：running 仍是 True，paused 翻转，恢复后回到原样
    assert seen["stop"]["running"] is False
    assert seen["pause"]["running"] is True
    assert seen["pause"]["paused"] is True
    assert seen["resume"]["paused"] is False
    assert LIFECYCLE.snapshot().paused is False

    unknown = client.post("/api/control", json={"action": "nope"})
    assert unknown.status_code == 400


def test_internal_errors_do_not_leak_exception_text(monkeypatch, client, caplog):
    """B-06：面板的 500 只给能照着做的原因，异常原文只进日志。

    以前的写法是 ``str(e)`` 或 ``f"{type(e).__name__}: {e}"``：内部路径、SQL、
    连接池的重试栈这些文本会直接发到浏览器，而排障需要它们待在日志里。
    """
    detail = "RuntimeError: 打不开 C:\\data\\config.json（SQLITE_BUSY）"

    class Boom:
        def read_raw(self):
            raise RuntimeError(detail)

    monkeypatch.setattr(deps, "config_store", lambda: Boom())
    with caplog.at_level(logging.ERROR, logger="flandora"):
        resp = client.get("/api/config")

    assert resp.status_code == 500
    assert detail not in resp.text
    assert "详细原因见运行日志" in resp.json()["error"]
    assert detail in caplog.text


def test_reset_defer_reason_matches_the_implementation():
    """B-08：档位表里的理由要与 reset 的实际行为一致。

    ``Wizard.reset()`` 只重置步骤状态、取消在跑的任务，不动已存的口令与图片密钥
    文件；理由里就别写成「会清空已存的密钥」——冒烟表是按这句话决定跳过哪些路由的。
    """
    import inspect

    from flandora.webui.api.wizard import DEFER_REASONS
    from flandora.wizard import Wizard

    source = inspect.getsource(Wizard.reset)
    assert "os.remove" not in source and "unlink" not in source
    reason = DEFER_REASONS["POST /api/wizard/reset"]
    assert "不删" in reason
