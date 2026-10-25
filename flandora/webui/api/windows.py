"""微信窗口组（多开场景）：枚举、绑定、闪烁。

窗口识别与闪烁的实现在 ``flandora.win32.windows``——发送器与隔离桌面都要用同一
套判定，放在那里才不会让 ``uia_sender`` 与 ``wx_desktop`` 互相 import。本组只做
请求编排：绑定要落配置（HOT 字段），闪烁只做视觉辨认、不抢焦点。
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ... import config
from ...runtime import events
from ...win32 import windows as win32_windows
from .. import deps

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/windows": {"GET": "call"},
    "/api/windows/select": {"POST": "defer"},
    "/api/windows/flash": {"POST": "defer"},
}

DEFER_REASONS: dict[str, str] = {
    "POST /api/windows/select": "会在真实微信窗口上换绑发送目标并写配置",
    "POST /api/windows/flash": "会让真实窗口的任务栏按钮闪烁",
}


@router.get("/api/windows")
def api_windows():
    """枚举所有微信窗口，标记当前绑定/配置锁定的窗口。"""
    try:
        wins = win32_windows.enum_wechat_windows()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

    cur_hwnd = 0
    sender = deps.sender()
    if sender is not None:
        cur_hwnd = sender.window_handle() or 0
    saved_hwnd = int(getattr(config, "WECHAT_HWND", 0) or 0)

    for w in wins:
        w["current"] = w["hwnd"] == cur_hwnd
        w["saved"] = w["hwnd"] == saved_hwnd
    return {"windows": wins, "current_hwnd": cur_hwnd, "saved_hwnd": saved_hwnd}


@router.post("/api/windows/select")
def api_select_window(payload: dict):
    """绑定指定微信窗口（hwnd=0 恢复自动）。保存到 config.json 并即时生效。"""
    try:
        hwnd = int((payload or {}).get("hwnd", 0) or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "hwnd 必须是整数"}, 400)

    sender = deps.sender()
    if sender is None:
        return JSONResponse({"ok": False, "error": "发送器未就绪（桥接未运行）"}, 400)

    if not sender.rebind_window(hwnd):
        return JSONResponse({"ok": False, "error": f"绑定失败，窗口句柄无效: {hwnd}"}, 400)

    # 持久化到 config.json（HOT 字段，订阅者会同步重绑窗口）
    try:
        deps.config_store().update({"wechat_hwnd": hwnd})
    except Exception as e:
        log.error(f"[WebUI] 保存窗口绑定失败: {e}")

    log.info(f"[WebUI] 微信窗口已绑定 hwnd={hwnd or '自动'}")
    events.notify_status()
    return {"ok": True, "hwnd": hwnd}


@router.post("/api/windows/flash")
def api_flash_window(payload: dict):
    """闪烁指定窗口的任务栏按钮（不抢焦点），用于视觉辨认。"""
    try:
        hwnd = int((payload or {}).get("hwnd", 0) or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "hwnd 必须是整数"}, 400)
    if not hwnd:
        return JSONResponse({"ok": False, "error": "hwnd 不能为 0"}, 400)
    ok = win32_windows.flash_window(hwnd)
    return {"ok": ok}
