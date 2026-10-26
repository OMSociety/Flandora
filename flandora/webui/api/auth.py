"""登录组：会话状态、登录、登出。

鉴权中间件（``flandora.webui.app._require_login``）装在组合根里，本组只做请求
编排：是否启用密码、当前会话是否有效、登录/登出的 cookie 与限流。密码校验、
限流计数与会话存储都在 ``flandora.webui.auth``。
"""

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import auth

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/auth": {"GET": "call"},
    "/api/login": {"POST": "call"},
    "/api/logout": {"POST": "call"},
}


@router.get("/api/auth")
def api_auth(request: Request):
    on = auth.enabled()
    return {
        "enabled": on,
        "authenticated": (not on) or auth.sessions.valid(request.cookies.get(auth.COOKIE_NAME)),
    }


@router.post("/api/login")
def api_login(payload: dict, response: Response, request: Request):
    if not auth.enabled():
        return {"ok": True, "enabled": False}
    if auth.throttle.blocked():
        return JSONResponse({"ok": False, "error": "尝试次数太多，请一分钟后再试"}, 429)
    password = str((payload or {}).get("password") or "")
    if not auth.check(password):
        auth.throttle.fail()
        return JSONResponse({"ok": False, "error": "密码不对"}, 401)
    auth.throttle.reset()
    token = auth.sessions.issue()
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=int(auth.SESSION_TTL),
        httponly=True,
        samesite="lax",
        path="/",
    )
    log.info("[面板] 面板登录成功")
    return {"ok": True, "enabled": True}


@router.post("/api/logout")
def api_logout(request: Request, response: Response):
    auth.sessions.drop(request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}
