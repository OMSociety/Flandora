"""
FastAPI WebUI 应用。

提供 REST API + WebSocket 实时推送 + 静态前端托管：
- GET  /api/status        运行状态
- POST /api/control       启停/重启/暂停/恢复 {"action": ...}
- POST /api/mode          切换群聊回复模式 {"mode": mention|all|batch}
- GET  /api/config        读取 config.json
- PUT  /api/config        合并保存 config.json（部分字段即时生效）
- GET  /api/messages      最近消息记录
- GET  /api/logs          最近日志（内存缓冲）
- GET  /api/stats         统计（计数器 + 24h 分布 + 热门联系人）
- GET  /api/contacts      已知联系人/群列表
- POST /api/send          测试发送 {"contact", "text"}
- WS   /ws                实时推送（log / msg / status 事件）
- GET  /                  前端静态页面（webui/dist）
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import state
import config
from ws_pool import _clients
from . import monitor
from .monitor import bus

log = logging.getLogger("ob11-bridge")

VERSION = "2.0.0"
WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(WEBUI_DIR, "dist")

# 保存后即时生效：写入 state 的运行时字段
HOT_STATE_KEYS = (
    "image_caption_provider", "image_caption_model", "image_caption_api_key",
    "image_caption_api_base", "image_caption_prompt", "ollama_base_url",
    "ollama_timeout",
)
# 保存后即时生效：写入 config 模块属性（bridge_core 等运行时实时读取）
HOT_CONFIG_ATTRS = {
    "bot_nicknames": "BOT_NICKNAMES",
    "bot_wxid": "BOT_WXID",
    "buffer_seconds": "BUFFER_SECONDS",
    "group_name_map": "GROUP_NAME_MAP",
    "self_name": "SELF_NAME",
    "context_messages": "CONTEXT_MESSAGES",
    "emoji_learn_enabled": "EMOJI_LEARN_ENABLED",
    "emoji_learn_threshold": "EMOJI_LEARN_THRESHOLD",
    "maibot_webui_url": "MAIBOT_WEBUI_URL",
}
# 需要重启桥接才生效的字段
RESTART_KEYS = (
    "weflow_base_url", "access_token", "ob_server_host", "ob_server_port",
    "ob_server_token", "web_port", "astrbot_ob_url", "astrbot_attachments",
)

VALID_MODES = ("mention", "all", "batch")


@asynccontextmanager
async def lifespan(_app):
    bus.set_loop(asyncio.get_running_loop())
    monitor.attach_log_relay()
    log.info(f"[WebUI] FastAPI 面板已就绪 (v{VERSION})")
    yield
    bus.set_loop(None)


app = FastAPI(title="MaiBot Bridge WebUI", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


# ============ 工具 ============


def get_status() -> dict:
    bi = state.bridge_instance
    weflow_ok = bi is not None and getattr(bi, "_sse_session", None) is not None
    uptime = None
    started = getattr(state, "bridge_started_at", None)
    if state.running and started:
        uptime = round(time.time() - started, 1)
    window = None
    sender = state.sender_instance
    if sender is not None and hasattr(sender, "current_window_info"):
        try:
            window = sender.current_window_info()
        except Exception:
            window = None
    with bus._lock:
        counters = dict(bus.counters)
    return {
        "running": state.running,
        "paused": state.paused.is_set(),
        "uptime_sec": uptime,
        "pid": os.getpid(),
        "version": VERSION,
        "web_port": config.WEB_PORT,
        "send_method": "uia",
        "ob": {
            "host": config.OB_SERVER_HOST,
            "port": config.OB_SERVER_PORT,
            "connected": len(_clients) > 0,
            "clients": len(_clients),
        },
        "weflow": {
            "base_url": config.WE_FLOW_BASE_URL,
            "connected": weflow_ok,
        },
        "group_reply_mode": state.group_reply_mode,
        "counters": counters,
        "window": window,
        "window_locked": bool(getattr(config, "WECHAT_HWND", 0)),
    }


def _read_config_file() -> dict:
    with open(config.CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _write_config_file(cfg: dict):
    with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
        f.write("\n")


# ============ 状态与控制 ============


@app.get("/api/status")
def api_status():
    return get_status()


@app.post("/api/control")
async def api_control(payload: dict):
    action = (payload or {}).get("action", "")
    from main import _start_bridge, _stop_bridge

    if action == "start":
        _start_bridge()
    elif action == "stop":
        _stop_bridge()
    elif action == "restart":
        _stop_bridge()
        await asyncio.sleep(1.5)
        _start_bridge()
    elif action == "pause":
        state.paused.set()
        log.info("[WebUI] 已暂停")
    elif action == "resume":
        state.paused.clear()
        log.info("[WebUI] 已恢复")
    else:
        return JSONResponse({"ok": False, "error": f"未知操作: {action}"}, 400)

    monitor.notify_status()
    return {"ok": True, **get_status()}


@app.post("/api/mode")
def api_mode(payload: dict):
    mode = (payload or {}).get("mode", "")
    if mode not in VALID_MODES:
        return JSONResponse({"ok": False, "error": f"无效模式: {mode}"}, 400)
    state.group_reply_mode = mode
    try:
        cfg = _read_config_file()
        cfg["group_reply_mode"] = mode
        _write_config_file(cfg)
    except Exception as e:
        log.error(f"[WebUI] 保存群聊模式失败: {e}")
    log.info(f"[WebUI] 群聊模式已切换为: {mode}")
    monitor.notify_status()
    return {"ok": True, "group_reply_mode": mode}


# ============ 配置 ============


@app.get("/api/config")
def api_get_config():
    try:
        return _read_config_file()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)


@app.put("/api/config")
def api_put_config(payload: dict):
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "请求体必须是 JSON 对象"}, 400)
    try:
        current = _read_config_file()
        current.update(payload)
        _write_config_file(current)

        # 即时生效：群聊模式
        if "group_reply_mode" in payload and payload["group_reply_mode"] in VALID_MODES:
            state.group_reply_mode = payload["group_reply_mode"]

        # 即时生效：state 运行时字段
        for key in HOT_STATE_KEYS:
            if key in payload:
                setattr(state, key, payload[key])

        # 即时生效：config 模块属性
        for key, attr in HOT_CONFIG_ATTRS.items():
            if key in payload:
                setattr(config, attr, payload[key])

        # 即时生效：微信窗口绑定
        if "wechat_hwnd" in payload:
            try:
                hwnd = int(payload["wechat_hwnd"] or 0)
                config.WECHAT_HWND = hwnd
                sender = state.sender_instance
                if sender is not None and hasattr(sender, "rebind_window"):
                    sender.rebind_window(hwnd)
            except (TypeError, ValueError):
                pass

        restart_required = [k for k in payload if k in RESTART_KEYS]
        log.info(f"[WebUI] 配置已保存 (字段: {', '.join(payload.keys())})")
        monitor.notify_status()
        return {"ok": True, "restart_required": restart_required}
    except Exception as e:
        log.error(f"[WebUI] 保存配置异常: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)


# ============ 消息 / 日志 / 统计 ============


@app.get("/api/messages")
def api_messages(limit: int = 100):
    limit = max(1, min(limit, 300))
    with bus._lock:
        msgs = list(bus.messages)[:limit]
    return {"messages": msgs}


@app.get("/api/logs")
def api_logs(limit: int = 200):
    limit = max(1, min(limit, 500))
    with bus._lock:
        logs = list(bus.logs)[-limit:]
    return {"logs": logs}


@app.get("/api/stats")
def api_stats():
    now = time.time()
    with bus._lock:
        msgs = list(bus.messages)
        counters = dict(bus.counters)

    # 最近 24 小时按小时分桶
    buckets = {}
    for m in msgs:
        if now - m.get("ts", 0) > 86400:
            continue
        h = time.strftime("%H", time.localtime(m["ts"]))
        b = buckets.setdefault(h, {"in": 0, "out": 0})
        key = "in" if m.get("dir") == "in" else "out"
        b[key] += 1

    hourly = []
    for i in range(23, -1, -1):
        h = time.strftime("%H", time.localtime(now - i * 3600))
        b = buckets.get(h, {"in": 0, "out": 0})
        hourly.append({"hour": h, "in": b["in"], "out": b["out"]})

    # 热门联系人（按接收消息数）
    contact_counts = {}
    for m in msgs:
        if m.get("dir") == "in" and m.get("contact"):
            c = m["contact"]
            contact_counts[c] = contact_counts.get(c, 0) + 1
    top = sorted(contact_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]

    return {
        "counters": counters,
        "hourly": hourly,
        "top_contacts": [{"contact": c, "count": n} for c, n in top],
    }


@app.get("/api/contacts")
def api_contacts():
    contacts = []
    seen = set()
    for gid, name in (config.GROUP_NAME_MAP or {}).items():
        contacts.append({"id": str(gid), "name": name, "kind": "group"})
        seen.add(str(gid))
    for oid, name in state._ob_id_to_contact.items():
        sid = str(oid)
        if sid in seen:
            continue
        kind = "group" if sid.endswith("@chatroom") else "private"
        contacts.append({"id": sid, "name": name, "kind": kind})
    return {"contacts": contacts}


# ============ 表情学习 ============


@app.get("/api/emoji-learning")
def api_emoji_learning():
    """表情学习状态：样本统计、热榜、最近学到的表达方式。"""
    try:
        import emoji_learner
        return {"ok": True, **emoji_learner.get_stats()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)


@app.post("/api/emoji-learning/run")
def api_emoji_learning_run():
    """手动触发一次「蒸馏 + 投喂麦麦」（后台线程执行，立即返回）。"""
    import emoji_learner
    import threading
    if emoji_learner._distill_running.is_set():
        return {"ok": False, "error": "已有学习任务在运行中"}
    threading.Thread(target=emoji_learner.distill_and_push,
                     kwargs={"trigger": "manual"}, daemon=True).start()
    return {"ok": True, "msg": "学习任务已启动，完成后见日志与结果"}


# ============ 测试发送 ============


@app.post("/api/send")
async def api_send(payload: dict):
    contact = ((payload or {}).get("contact") or "").strip()
    text = (payload or {}).get("text") or ""
    if not contact or not text:
        return JSONResponse({"ok": False, "error": "联系人和内容不能为空"}, 400)

    sender = state.sender_instance
    if not state.running or sender is None:
        return JSONResponse({"ok": False, "error": "桥接未运行，请先启动"}, 400)

    log.info(f"[WebUI] 测试发送 → {contact}: {text[:50]}")
    try:
        ok = await asyncio.to_thread(sender.send_text, contact, text)
    except Exception as e:
        log.error(f"[WebUI] 测试发送异常: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)

    if ok:
        monitor.record_outbound(contact, "manual", text)
        log.info(f"[WebUI] 测试发送成功 → {contact}")
    else:
        log.warning(f"[WebUI] 测试发送失败 → {contact}")
    return {"ok": bool(ok)}


# ============ 微信窗口识别（多开场景） ============


@app.get("/api/windows")
def api_windows():
    """枚举所有微信窗口，标记当前绑定/配置锁定的窗口。"""
    from uia_sender import enum_wechat_windows
    try:
        wins = enum_wechat_windows()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)

    cur_hwnd = 0
    sender = state.sender_instance
    if sender is not None:
        cur_hwnd = getattr(sender, "_hwnd", 0) or 0
    saved_hwnd = int(getattr(config, "WECHAT_HWND", 0) or 0)

    for w in wins:
        w["current"] = (w["hwnd"] == cur_hwnd)
        w["saved"] = (w["hwnd"] == saved_hwnd)
    return {"windows": wins, "current_hwnd": cur_hwnd, "saved_hwnd": saved_hwnd}


@app.post("/api/windows/select")
def api_select_window(payload: dict):
    """绑定指定微信窗口（hwnd=0 恢复自动）。保存到 config.json 并即时生效。"""
    try:
        hwnd = int((payload or {}).get("hwnd", 0) or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "hwnd 必须是整数"}, 400)

    sender = state.sender_instance
    if sender is None or not hasattr(sender, "rebind_window"):
        return JSONResponse({"ok": False, "error": "发送器未就绪（桥接未运行）"}, 400)

    if not sender.rebind_window(hwnd):
        return JSONResponse({"ok": False, "error": f"绑定失败，窗口句柄无效: {hwnd}"}, 400)

    # 持久化到 config.json
    config.WECHAT_HWND = hwnd
    try:
        cfg = _read_config_file()
        cfg["wechat_hwnd"] = hwnd
        _write_config_file(cfg)
    except Exception as e:
        log.error(f"[WebUI] 保存窗口绑定失败: {e}")

    log.info(f"[WebUI] 微信窗口已绑定 hwnd={hwnd or '自动'}")
    monitor.notify_status()
    return {"ok": True, "hwnd": hwnd}


@app.post("/api/windows/flash")
def api_flash_window(payload: dict):
    """闪烁指定窗口的任务栏按钮（不抢焦点），用于视觉辨认。"""
    from uia_sender import flash_window
    try:
        hwnd = int((payload or {}).get("hwnd", 0) or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "hwnd 必须是整数"}, 400)
    if not hwnd:
        return JSONResponse({"ok": False, "error": "hwnd 不能为 0"}, 400)
    ok = flash_window(hwnd)
    return {"ok": ok}


# ============ WebSocket 实时推送 ============


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    q = bus.subscribe()
    try:
        with bus._lock:
            snapshot = {
                "type": "snapshot",
                "logs": list(bus.logs),
                "messages": list(bus.messages),
            }
        await websocket.send_json(snapshot)
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
            except asyncio.TimeoutError:
                event = {"type": "ping"}
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bus.unsubscribe(q)


# ============ 静态前端 ============


if os.path.isdir(DIST_DIR):
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="webui")
else:
    @app.get("/")
    def _no_build():
        return JSONResponse({
            "ok": False,
            "error": "前端尚未构建。请在 webui/frontend 目录执行 npm install && npm run build",
        }, 503)


def run(host: str, port: int):
    """由 main.py 调用，在主线程阻塞运行 uvicorn。"""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
