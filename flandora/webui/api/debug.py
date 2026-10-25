"""调试组：动作目录、在本进程里执行动作、最近推送出去的事件。

``/api/debug/invoke`` 走的是 OneBot 客户端连进来时的同一条派发路径
（``ob_protocol._handle_ob_api``），所以这里调得通的东西客户端也调得通；
动作目录同样来自 ``action_catalog``，不会出现「面板里有、协议端没有」的漂移。
"""

import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/debug/actions": {"GET": "call"},
    "/api/debug/invoke": {"POST": "call"},
    "/api/debug/events": {"GET": "call"},
}


@router.get("/api/debug/actions")
def api_debug_actions():
    """动作目录。

    面板据此生成参数表单——与桥接派发用的是同一张表，所以这里列出来的东西
    一定是真的会被执行的东西，不存在「面板里有、协议端没有」的漂移。
    """
    from ... import action_catalog

    catalog = action_catalog.catalog_dict()
    return {
        "categories": catalog["categories"],
        "actions": catalog["actions"],
    }


@router.post("/api/debug/invoke")
async def api_debug_invoke(payload: dict):
    """在本进程里直接跑一遍动作链路。

    走的是客户端连进来时的那条同一条路（`_handle_ob_api`），返回真正的响应体，
    所以这里调得通的东西，OneBot 客户端也调得通；不经过网络，省去连一条 WS。
    """
    from ... import ob_protocol

    data = payload or {}
    action = str(data.get("action") or "").strip()
    if not action:
        return JSONResponse({"ok": False, "error": "action 不能为空"}, 400)
    params = data.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse({"ok": False, "error": "params 必须是对象"}, 400)

    started = time.time()
    try:
        resp = await ob_protocol._handle_ob_api(
            {"action": action, "params": params, "echo": "webui"}
        )
    except Exception as e:  # noqa: BLE001
        log.error(f"[WebUI] 调试调用异常 {action}: {e}")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    return {
        "ok": resp.get("status") == "ok",
        "elapsed_ms": int((time.time() - started) * 1000),
        "response": resp,
    }


@router.get("/api/debug/events")
def api_debug_events(limit: int = 30):
    """最近推送出去的事件（读消息存档，与客户端收到的是同一份）。"""
    from ... import ob_store

    try:
        events = ob_store.message_store().recent(max(1, min(int(limit or 30), 200)))
    except Exception as e:  # noqa: BLE001
        log.error(f"[WebUI] 读取事件存档失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    return {"events": events}
