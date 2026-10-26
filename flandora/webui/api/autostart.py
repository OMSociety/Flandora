# -*- coding: utf-8 -*-
"""自启组：查询与设置「开机自动启动管理器」。

读写的是当前用户 ``Run`` 项下的 ``Flandora`` 值（实现见 ``flandora.autostart``），
安装器写的是同一位置 —— 面板里看到的就是开机时真正会执行的那一条。

``POST /api/autostart`` 登记为 ``defer``：它会改当前用户的注册表，在冒烟机上留下
真实痕迹，由本模块的用例单独覆盖。
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ... import autostart

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/autostart": {"GET": "call", "POST": "defer"},
}

DEFER_REASONS: dict[str, str] = {
    "POST /api/autostart": "会写或删当前用户 Run 项下的 Flandora 值，由本模块用例单独覆盖",
}


@router.get("/api/autostart")
def api_autostart_get():
    return {"ok": True, **autostart.status()}


@router.post("/api/autostart")
def api_autostart_set(payload: dict):
    enabled = bool((payload or {}).get("enabled"))
    if enabled and not autostart.status()["command"]:
        # 找不到管理器入口时拒绝，而不是写一条开机执行空气的注册表值
        return JSONResponse({"ok": False, "error": autostart.status()["reason"]}, 400)
    ok, message = autostart.enable() if enabled else autostart.disable()
    if not ok:
        log.error(f"[面板] 设置开机自启失败: {message}")
        return JSONResponse({"ok": False, "error": message}, 400)
    log.info(f"[面板] {message}")
    return {"ok": True, **autostart.status()}
