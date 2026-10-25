"""面板登录：密码哈希、会话表、登录接口与 /api 中间件。"""

import asyncio
import types

import pytest
from fastapi.responses import JSONResponse

from flandora import config
from flandora.webui import app as webui_app
from flandora.webui import auth


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
    response = webui_app.Response()

    rest = webui_app.api_login({"password": "secret123"}, response, FakeRequest())

    assert rest["ok"] is True
    cookie = response.headers["set-cookie"]
    assert auth.COOKIE_NAME in cookie and "HttpOnly" in cookie
    token = cookie.split(auth.COOKIE_NAME + "=", 1)[1].split(";", 1)[0]
    assert auth.sessions.valid(token)


def test_login_with_wrong_password_is_refused(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))
    response = webui_app.Response()

    rest = webui_app.api_login({"password": "nope"}, response, FakeRequest())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 401
    assert "set-cookie" not in response.headers


def test_login_throttles_repeated_failures(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))

    for _ in range(auth.LOGIN_MAX_FAILURES):
        webui_app.api_login({"password": "nope"}, webui_app.Response(), FakeRequest())
    rest = webui_app.api_login({"password": "secret123"}, webui_app.Response(), FakeRequest())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 429


def test_logout_drops_the_session(monkeypatch):
    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))
    token = auth.sessions.issue()

    rest = webui_app.api_logout(
        FakeRequest(cookies={auth.COOKIE_NAME: token}), webui_app.Response()
    )

    assert rest["ok"] is True
    assert auth.sessions.valid(token) is False


def test_auth_endpoint_reports_state(monkeypatch):
    assert webui_app.api_auth(FakeRequest()) == {"enabled": False, "authenticated": True}

    monkeypatch.setattr(config, "WEBUI_PASSWORD", auth.hash_password("secret123"))
    assert webui_app.api_auth(FakeRequest()) == {"enabled": True, "authenticated": False}

    token = auth.sessions.issue()
    assert webui_app.api_auth(FakeRequest(cookies={auth.COOKIE_NAME: token})) == {
        "enabled": True,
        "authenticated": True,
    }


# ---------- 保存配置 ----------


@pytest.fixture()
def saved(monkeypatch):
    box = {"cfg": {}}
    monkeypatch.setattr(webui_app, "_read_config_file", lambda: dict(box["cfg"]))
    monkeypatch.setattr(webui_app, "_write_config_file", lambda cfg: box.update(cfg=dict(cfg)))
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)
    return box


def test_setting_password_stores_only_a_hash(saved):
    response = webui_app.Response()

    rest = webui_app.api_put_config({"webui_password": "secret123"}, response)

    assert rest["ok"] is True
    stored = saved["cfg"]["webui_password"]
    assert auth.is_hashed(stored)
    assert "secret123" not in stored
    assert config.WEBUI_PASSWORD == stored
    assert "set-cookie" in response.headers


def test_clearing_password_disables_login(saved):
    webui_app.api_put_config({"webui_password": "secret123"}, webui_app.Response())
    token = auth.sessions.issue()
    assert auth.enabled() is True

    webui_app.api_put_config({"webui_password_clear": True}, webui_app.Response())

    assert saved["cfg"]["webui_password"] == ""
    assert config.WEBUI_PASSWORD == ""
    assert auth.enabled() is False
    assert auth.sessions.valid(token) is False


def test_bad_password_length_is_refused(saved):
    rest = webui_app.api_put_config({"webui_password": "123"}, webui_app.Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert saved["cfg"] == {}


def test_password_set_flag_from_browser_is_ignored(saved, monkeypatch):
    hashed = auth.hash_password("secret123")
    monkeypatch.setattr(config, "WEBUI_PASSWORD", hashed)

    webui_app.api_put_config(
        {"webui_password_set": True, "group_name_map": {"g@chatroom": "群名"}}, webui_app.Response()
    )

    # 面板只报「设没设」，不该把哈希当配置项写回去
    assert "webui_password" not in saved["cfg"]
    assert config.WEBUI_PASSWORD == hashed
    assert saved["cfg"]["group_name_map"] == {"g@chatroom": "群名"}
