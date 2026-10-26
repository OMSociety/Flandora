"""面板登录：密码哈希、会话表、登录接口与 /api 中间件。"""

import asyncio
import types

import pytest
from fastapi.responses import JSONResponse, Response

from flandora import config
from flandora.runtime import events
from flandora.webui import app as webui_app
from flandora.webui import auth
from flandora.webui.api import auth as auth_api
from flandora.webui.api import config as config_api


class FakeRequest:
    """中间件与登录接口只用到 url.path 和 cookies。"""

    def __init__(self, path="/api/status", cookies=None):
        self.url = types.SimpleNamespace(path=path)
        self.cookies = dict(cookies or {})


async def _ok(_request):
    return JSONResponse({"ok": True})


def run_login_middleware(request):
    return asyncio.run(webui_app._require_login(request, _ok))


@pytest.fixture(autouse=True)
def clean_auth(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", "")
    auth.sessions.clear()
    auth.throttle.reset()
    yield
    auth.sessions.clear()
    auth.throttle.reset()


# ---------- 哈希 ----------


def test_hash_roundtrip():
    stored = auth.hash_password("correct horse")

    assert auth.is_hashed(stored)
    assert auth.verify_password("correct horse", stored) is True
    assert auth.verify_password("correct horsE", stored) is False
    assert auth.verify_password("", stored) is False


def test_short_password_is_refused():
    with pytest.raises(ValueError):
        auth.hash_password("12345")


def test_malformed_hash_never_matches():
    assert auth.verify_password("x", "plain-text") is False
    assert auth.verify_password("x", "pbkdf2_sha256$notanint$a$b") is False
    assert auth.is_hashed("pbkdf2_sha256$1$a$b$c") is False


def test_salt_makes_each_hash_unique():
    assert auth.hash_password("same-password") != auth.hash_password("same-password")


# ---------- 会话与节流 ----------


def test_session_lifecycle():
    token = auth.sessions.issue()

    assert auth.sessions.valid(token) is True
    assert auth.sessions.valid("nope") is False
    assert auth.sessions.valid(None) is False
    auth.sessions.drop(token)
    assert auth.sessions.valid(token) is False


def test_expired_session_is_dropped():
    sessions = auth.Sessions(ttl=-1)

    assert sessions.valid(sessions.issue()) is False
    assert len(sessions) == 0


def test_throttle_blocks_after_failures():
    throttle = auth.LoginThrottle(window=60, limit=3)
    for _ in range(2):
        throttle.fail()
    assert throttle.blocked() is False

    throttle.fail()
    assert throttle.blocked() is True

    throttle.reset()
    assert throttle.blocked() is False


def test_throttle_forgets_old_failures():
    # 窗口为 0：上一次失败立刻过期，不该累积成永久封锁
    throttle = auth.LoginThrottle(window=0, limit=2)
    throttle.fail()
    throttle.fail()

    assert throttle.blocked() is False


# ---------- 中间件 ----------


def test_api_is_open_when_password_is_off():
    assert run_login_middleware(FakeRequest()).status_code == 200


def test_api_needs_session_when_password_is_on(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))

    blocked = run_login_middleware(FakeRequest())
    assert blocked.status_code == 401
    assert b"auth_required" in blocked.body

    token = auth.sessions.issue()
    allowed = run_login_middleware(FakeRequest(cookies={auth.COOKIE_NAME: token}))
    assert allowed.status_code == 200


def test_login_endpoints_stay_public(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))

    for path in ("/api/login", "/api/auth", "/api/logout"):
        assert run_login_middleware(FakeRequest(path=path)).status_code == 200


def test_static_pages_are_not_gated(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))

    assert run_login_middleware(FakeRequest(path="/")).status_code == 200


# ---------- 登录接口 ----------


def test_login_with_right_password_sets_cookie(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))
    response = Response()

    rest = auth_api.api_login({"password": "secret123"}, response, FakeRequest())

    assert rest["ok"] is True
    cookie = response.headers["set-cookie"]
    assert auth.COOKIE_NAME in cookie and "HttpOnly" in cookie
    token = cookie.split(auth.COOKIE_NAME + "=", 1)[1].split(";", 1)[0]
    assert auth.sessions.valid(token)


def test_login_with_wrong_password_is_refused(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))
    response = Response()

    rest = auth_api.api_login({"password": "nope"}, response, FakeRequest())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 401
    assert "set-cookie" not in response.headers


def test_login_throttles_repeated_failures(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))

    for _ in range(auth.LOGIN_MAX_FAILURES):
        auth_api.api_login({"password": "nope"}, Response(), FakeRequest())
    rest = auth_api.api_login({"password": "secret123"}, Response(), FakeRequest())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 429


def test_logout_drops_the_session(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))
    token = auth.sessions.issue()

    rest = auth_api.api_logout(FakeRequest(cookies={auth.COOKIE_NAME: token}), Response())

    assert rest["ok"] is True
    assert auth.sessions.valid(token) is False


def test_auth_endpoint_reports_state(monkeypatch):
    assert auth_api.api_auth(FakeRequest()) == {"enabled": False, "authenticated": True}

    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))
    assert auth_api.api_auth(FakeRequest()) == {"enabled": True, "authenticated": False}

    token = auth.sessions.issue()
    assert auth_api.api_auth(FakeRequest(cookies={auth.COOKIE_NAME: token})) == {
        "enabled": True,
        "authenticated": True,
    }


# ---------- 保存配置 ----------


@pytest.fixture()
def saved(monkeypatch, tmp_path):
    monkeypatch.setattr(events, "notify_status", lambda: None)
    with config.temporary(tmp_path / "config.json") as store:
        yield store


def test_setting_password_stores_only_a_hash(saved):
    response = Response()

    rest = config_api.api_put_config({"webui_password": "secret123"}, response)

    assert rest["ok"] is True
    stored = saved.read_raw()["webui_password"]
    assert auth.is_hashed(stored)
    assert "secret123" not in stored
    assert config.WEBUI_PASSWORD == stored
    assert "set-cookie" in response.headers


def test_clearing_password_disables_login(saved):
    config_api.api_put_config({"webui_password": "secret123"}, Response())
    token = auth.sessions.issue()
    assert auth.enabled() is True

    config_api.api_put_config({"webui_password_clear": True}, Response())

    assert saved.read_raw()["webui_password"] == ""
    assert config.WEBUI_PASSWORD == ""
    assert auth.enabled() is False
    assert auth.sessions.valid(token) is False


def test_clearing_password_is_refused_while_bound_to_all_interfaces(saved, monkeypatch):
    """绑在所有网卡上时不许把密码留空。

    盘上若出现「0.0.0.0 + 无密码」，下次启动面板会直接拒绝启动（main.start_web），
    而不是悄悄退回本机——所以清密码这一步必须同时把绑定收回来。
    """
    config_api.api_put_config({"webui_password": "secret123"}, Response())
    config_api.api_put_config({"web_bind_host": "0.0.0.0"}, Response())
    monkeypatch.setattr(config, "WEB_BIND_HOST", "0.0.0.0")

    rest = config_api.api_put_config({"webui_password_clear": True}, Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert saved.read_raw()["webui_password"] != ""
    assert config.WEBUI_PASSWORD != ""


def test_clearing_password_with_loopback_bind_in_one_request(saved):
    """面板的「清密码」是一步做完的：同一份请求里把绑定改回 127.0.0.1 就放行。"""
    config_api.api_put_config({"webui_password": "secret123"}, Response())
    config_api.api_put_config({"web_bind_host": "0.0.0.0"}, Response())

    rest = config_api.api_put_config(
        {"webui_password_clear": True, "web_bind_host": "127.0.0.1"}, Response()
    )

    assert not isinstance(rest, JSONResponse)
    raw = saved.read_raw()
    assert raw["webui_password"] == ""
    assert raw["web_bind_host"] == "127.0.0.1"


def test_rejected_save_keeps_the_existing_sessions(saved, monkeypatch):
    """保存被拒时用户不该被登出：会话只在配置真的落盘后才作废。"""
    config_api.api_put_config({"webui_password": "secret123"}, Response())
    config_api.api_put_config({"web_bind_host": "0.0.0.0"}, Response())
    token = auth.sessions.issue()
    monkeypatch.setattr(config, "WEB_BIND_HOST", "0.0.0.0")

    rest = config_api.api_put_config({"webui_password_clear": True}, Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert auth.sessions.valid(token) is True, "保存被拒却把会话清了"


def test_restore_of_only_sensitive_keys_is_refused(saved):
    """备份里只有凭据类配置时什么都没恢复，不能回 200 装作成功。"""
    rest = config_api.api_config_restore(
        {"config": {"webui_password": "x", "ob_server_token": "y"}}
    )

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    body = rest.body.decode("utf-8")
    assert "凭据" in body or "敏感" in body


def test_bad_password_length_is_refused(saved):
    rest = config_api.api_put_config({"webui_password": "123"}, Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert saved.read_raw() == {}


def test_password_set_flag_from_browser_is_ignored(saved, monkeypatch):
    hashed = auth.hash_password("secret123")
    monkeypatch.setattr(config, "WEBUI_PASSWORD", hashed)

    config_api.api_put_config(
        {"webui_password_set": True, "group_name_map": {"g@chatroom": "群名"}}, Response()
    )

    # 面板只报「设没设」，不该把哈希当配置项写回去
    assert "webui_password" not in saved.read_raw()
    assert config.WEBUI_PASSWORD == hashed
    assert saved.read_raw()["group_name_map"] == {"g@chatroom": "群名"}


def test_empty_password_string_clears_it(saved):
    """API 客户端显式提交空串 = 清掉密码；不带这个键才是不改。"""
    config_api.api_put_config({"webui_password": "secret123"}, Response())
    assert auth.enabled() is True

    rest = config_api.api_put_config({"webui_password": ""}, Response())

    assert not isinstance(rest, JSONResponse)
    assert saved.read_raw()["webui_password"] == ""
    assert auth.enabled() is False
