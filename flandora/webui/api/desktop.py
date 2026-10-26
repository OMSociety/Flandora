"""隔离桌面组：状态、起停、结束默认桌面上的微信、实况截图、点击转发。

微信跑在独立桌面 ``FlandoraSandbox`` 上，登录靠截图、点击靠投递客户区坐标；这些
能力全在 ``flandora.wx_desktop``，本组只做 HTTP 编排。``wx_desktop`` 在函数内
import：缺桌面能力的环境（非 Windows）只让这几条端点报错，不影响面板其余部分。
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/desktop/status": {"GET": "call"},
    "/api/desktop/start": {"POST": "defer"},
    "/api/desktop/stop": {"POST": "defer"},
    "/api/desktop/quit-default-wechat": {"POST": "defer"},
    "/api/desktop/screenshot": {"GET": "defer"},
    "/api/desktop/click": {"POST": "defer"},
}

DEFER_REASONS: dict[str, str] = {
    "POST /api/desktop/start": "会在隔离桌面上真的启动一份微信",
    "POST /api/desktop/stop": "会结束隔离桌面上的微信",
    "POST /api/desktop/quit-default-wechat": "会结束用户自己那个桌面上的微信",
    "GET /api/desktop/screenshot": "会截取真实桌面上的窗口画面",
    "POST /api/desktop/click": "会向真实窗口注入鼠标点击",
}


def _module_missing(exc: ImportError) -> JSONResponse:
    """隔离桌面模块导入失败：异常原文只进日志，响应只给原因。"""
    log.error(f"[WebUI] 隔离桌面模块不可用: {exc}")
    return JSONResponse(
        {"ok": False, "error": "隔离桌面模块不可用（缺依赖或导入失败），详细原因见运行日志"},
        500,
    )


@router.get("/api/desktop/status")
def api_desktop_status():
    """隔离桌面的运行与登录状态。"""
    try:
        from ... import wx_desktop
    except ImportError as e:
        return _module_missing(e)
    try:
        return {"ok": True, **wx_desktop.manager().status()}
    except Exception as e:
        log.error(f"[WebUI] 读取隔离桌面状态失败: {e}")
        return JSONResponse({"ok": False, "error": "读取隔离桌面状态失败，详细原因见运行日志"}, 500)


@router.post("/api/desktop/start")
def api_desktop_start():
    """在隔离桌面上启动微信（已启动时是空操作）。"""
    try:
        from ... import wx_desktop
    except ImportError as e:
        return _module_missing(e)
    mgr = wx_desktop.manager()
    if not mgr.enabled():
        return JSONResponse(
            {
                "ok": False,
                "error": "隔离桌面模式未开启。请先在配置里把 isolated_desktop 设为 true 并保存",
            },
            400,
        )
    if not mgr.start():
        return JSONResponse({"ok": False, "error": mgr.status().get("error", "启动失败")}, 500)
    return {"ok": True, **mgr.status()}


@router.post("/api/desktop/stop")
def api_desktop_stop():
    """结束隔离桌面上的微信并释放桌面（只动本程序启动或接管的那个）。"""
    try:
        from ... import wx_desktop
    except ImportError as e:
        return _module_missing(e)
    wx_desktop.manager().stop()
    return {"ok": True, **wx_desktop.manager().status()}


@router.post("/api/desktop/quit-default-wechat")
def api_desktop_quit_default_wechat():
    """结束默认桌面上（用户自己开着）的微信。

    隔离模式要求微信由本程序启动，但用户桌面上那个微信不会被动——只有点了
    这个接口才结束它，避免静默杀掉用户正在用的微信。
    """
    try:
        from ... import wx_desktop
    except ImportError as e:
        return _module_missing(e)
    killed = wx_desktop.quit_default_desktop_wechat()
    if not killed:
        return JSONResponse({"ok": False, "error": "没找到默认桌面上的微信窗口"}, 404)
    return {"ok": True, "killed": killed}


@router.get("/api/desktop/screenshot")
def api_desktop_screenshot():
    """隔离桌面上微信窗口的实况截图（PNG）。

    隔离桌面用户看不见，登录必须靠这个画面；二维码与「需在手机上完成登录」
    这类界面都在这里显示。
    """
    try:
        from ... import wx_desktop
    except ImportError as e:
        return _module_missing(e)
    mgr = wx_desktop.manager()
    if not mgr.enabled():
        return JSONResponse({"ok": False, "error": "隔离桌面模式未开启"}, 400)
    png = mgr.screenshot_png()
    if png is None:
        return JSONResponse({"ok": False, "error": "没取到画面：微信窗口不在或尚未出现"}, 404)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/api/desktop/click")
def api_desktop_click(payload: dict):
    """把画面上的点击转发到隔离桌面上的微信窗口。

    坐标是**客户区坐标**（相对窗口左上角），与 /api/desktop/screenshot 的像素
    一一对应——前端按图片显示尺寸等比换算即可。
    """
    try:
        from ... import wx_desktop
    except ImportError as e:
        return _module_missing(e)
    try:
        x = int((payload or {}).get("x", -1))
        y = int((payload or {}).get("y", -1))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "x/y 必须是整数"}, 400)
    if not wx_desktop.manager().click(x, y):
        return JSONResponse(
            {"ok": False, "error": f"点击未送达（窗口不在，或坐标越界: {x},{y}）"}, 400
        )
    return {"ok": True}
