"""状态与控制组：运行状态快照、起停/重启/暂停/恢复。

状态快照的唯一实现在 ``flandora.webui.deps.status_payload()``——控制组执行完
动作后复用它回包，保证两个端点对「运行中/已暂停」的说法来自同一处。

``/api/control`` 登记为 ``defer``：起停会真的拉起/停掉桥接线程与 OneBot 端口，
由 ``tests/test_webui_endpoints.py`` 的控制用例单独覆盖。
"""

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...runtime import LIFECYCLE, events
from .. import deps

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/status": {"GET": "call"},
    "/api/control": {"POST": "defer"},
}

DEFER_REASONS: dict[str, str] = {
    "POST /api/control": "起停会真的拉起/停掉桥接线程与 OneBot 端口，由控制用例单独覆盖",
}


@router.get("/api/status")
def api_status():
    return deps.status_payload()


@router.post("/api/control")
async def api_control(payload: dict):
    action = (payload or {}).get("action", "")
    from ... import main as bridge_main

    # 起停要等桥接线程退出与 OneBot 端口释放，最坏十秒量级：放到线程里做，
    # 否则整个面板（含状态推送与轮询）会被这一次请求一起卡住。
    if action == "start":
        await asyncio.to_thread(bridge_main.start_bridge)
    elif action == "stop":
        await asyncio.to_thread(bridge_main.stop_bridge)
    elif action == "restart":
        await asyncio.to_thread(bridge_main.stop_bridge)
        await asyncio.sleep(1.5)
        await asyncio.to_thread(bridge_main.start_bridge)
    elif action == "pause":
        log.info("[面板] 已暂停" if LIFECYCLE.pause() else "[面板] 桥接未运行，暂停未生效")
    elif action == "resume":
        log.info("[面板] 已恢复" if LIFECYCLE.resume() else "[面板] 桥接未运行，恢复未生效")
    else:
        return JSONResponse({"ok": False, "error": f"未知操作: {action}"}, 400)

    events.notify_status()
    return {"ok": True, **deps.status_payload()}
