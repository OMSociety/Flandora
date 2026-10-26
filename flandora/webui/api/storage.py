"""存储组：数据目录占用、按类清理、消息存档的显式动作、最近消息缓冲。

清理与存档操作会删真实文件/记录，两条登记为 ``defer``（原因见 ``DEFER_REASONS``）；
``/api/messages`` 只读事件总线里的缓冲，不改任何东西，登记为 ``call``。
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ... import storage
from ...runtime import events

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/storage": {"GET": "call"},
    "/api/storage/clean": {"POST": "defer"},
    "/api/storage/messages": {"POST": "defer"},
    "/api/messages": {"GET": "call"},
}

DEFER_REASONS: dict[str, str] = {
    "POST /api/storage/clean": "会真的删数据目录里的文件（图片缓存、旧日志等）",
    "POST /api/storage/messages": "会真的删本地库里的聊天记录存档",
}

STORAGE_TARGETS = {
    "images": storage.clean_images,
    "wx_cache": storage.clean_wx_cache,
    "updates": storage.clean_updates,
    "old_logs": storage.clean_old_logs,
    "rotate_log": storage.rotate_log,
}


@router.get("/api/storage")
def api_storage():
    """数据目录占用一览（只读，不建库、不动任何文件）。"""
    try:
        return {"ok": True, **storage.overview()}
    except Exception as e:  # noqa: BLE001
        log.error(f"[面板] 统计存储占用失败: {e}")
        return JSONResponse({"ok": False, "error": "统计存储占用失败，详细原因见运行日志"}, 500)


@router.post("/api/storage/clean")
def api_storage_clean(payload: dict):
    """按目标清理数据目录的一类内容。删什么是固定的几类，不接受任意路径。"""
    target = str((payload or {}).get("target") or "").strip()
    func = STORAGE_TARGETS.get(target)
    if func is None:
        return JSONResponse({"ok": False, "error": f"不认识的清理目标：{target or '(空)'}"}, 400)
    try:
        result = func()
    except Exception as e:  # noqa: BLE001
        log.error(f"[面板] 清理 {target} 失败: {e}")
        return JSONResponse({"ok": False, "error": f"清理 {target} 失败，详细原因见运行日志"}, 500)
    log.info(f"[面板] 存储清理 {target}: {result}")
    return {"ok": True, "target": target, **result}


@router.post("/api/storage/messages")
def api_storage_messages(payload: dict):
    """消息存档的三种显式动作：prune（按天）/ clear / vacuum。"""
    data = payload if isinstance(payload, dict) else {}
    action = str(data.get("action") or "").strip()
    try:
        if action == "prune":
            try:
                days = int(data.get("days"))
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": "保留天数必须是整数"}, 400)
            if not 1 <= days <= 3650:
                return JSONResponse({"ok": False, "error": "保留天数取 1–3650"}, 400)
            return {"ok": True, "action": action, **storage.messages_prune(days)}
        if action == "clear":
            return {"ok": True, "action": action, **storage.messages_clear()}
        if action == "vacuum":
            return {"ok": True, "action": action, **storage.messages_vacuum()}
    except Exception as e:  # noqa: BLE001
        log.error(f"[面板] 消息存档操作 {action} 失败: {e}")
        return JSONResponse(
            {"ok": False, "error": f"消息存档操作 {action} 失败，详细原因见运行日志"}, 500
        )
    return JSONResponse({"ok": False, "error": f"不认识的动作：{action or '(空)'}"}, 400)


@router.get("/api/messages")
def api_messages(limit: int = 100):
    limit = max(1, min(limit, 300))
    return {"messages": events.EVENTS.messages_copy()[:limit]}
