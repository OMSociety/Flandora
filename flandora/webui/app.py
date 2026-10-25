"""
FastAPI WebUI 应用。

提供 REST API + WebSocket 实时推送 + 静态前端托管：
- GET  /api/status        运行状态
- POST /api/control       启停/重启/暂停/恢复 {"action": ...}
- GET  /api/config        读取 config.json
- PUT  /api/config        合并保存 config.json（部分字段即时生效）
- GET  /api/config/backup  导出 config.json（默认不含凭据）
- POST /api/config/restore 从备份恢复配置（先备份现有文件）
- GET  /api/messages      最近消息记录
- GET  /api/logs          最近日志（内存缓冲）
- GET  /api/stats         统计（计数器 + 24h 分布 + 热门联系人）
- GET  /api/contacts      已知联系人/群列表
- POST /api/send          测试发送 {"contact", "text"}
- GET  /api/debug/actions  动作目录（面板生成参数表单）
- POST /api/debug/invoke   在本进程里执行一个动作 {"action", "params"}
- GET  /api/debug/events   最近推送出去的事件
- GET  /api/update/status  更新状态（安装形态、待安装包、上次查到的版本）
- POST /api/update/check   现查 GitHub 发布页
- POST /api/update/download 下载并暂存安装包
- POST /api/update/install  拉起替换脚本并退出（仅打包安装）
- POST /api/update/cancel   放弃待安装的包
- GET  /api/desktop/status     隔离桌面运行/登录状态
- POST /api/desktop/start      在隔离桌面上启动微信
- POST /api/desktop/stop       结束隔离桌面上的微信
- POST /api/desktop/quit-default-wechat 结束默认桌面上用户自己开着的微信
- GET  /api/desktop/screenshot 隔离桌面上微信窗口的实况截图（PNG）
- POST /api/desktop/click      把画面点击转发到隔离窗口 {"x", "y"}
- WS   /ws                实时推送（log / msg / status 事件）
- GET  /                  前端静态页面（webui/dist）
"""

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__, config, ob_network, state, storage, wechat_account
from ..ob_network import manager as ob_networks
from . import auth, monitor
from .monitor import bus

log = logging.getLogger("flandora")

VERSION = __version__
WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(WEBUI_DIR, "dist")

# 保存后即时生效：写入 config 模块属性（bridge_core 等运行时实时读取）
HOT_CONFIG_ATTRS = {
    "group_name_map": "GROUP_NAME_MAP",
}
# 需要重启桥接才生效的字段
RESTART_KEYS = (
    "ob_server_host",
    "ob_server_port",
    "ob_server_token",
    "ob_networks",
    "ob_heartbeat_seconds",
    "web_port",
    "web_bind_host",
    "webui_tls",
    "client_attachments",
    # 隔离桌面相关的三个改动都要重启才成立：桌面要重建、微信要重开、窗口要重绑
    "isolated_desktop",
    "isolated_desktop_name",
    "wechat_exe",
)
# 备份文件里算凭据的键：默认不导出，恢复时也不覆盖本地现值
SENSITIVE_KEYS = ("webui_password", "ob_server_token")
# 面板提交的 PEM 内容：落成文件，不进 config.json
PEM_FIELDS = ("webui_tls_cert", "webui_tls_key")
ALLOWED_BIND_HOSTS = ("127.0.0.1", "0.0.0.0")


@asynccontextmanager
async def lifespan(_app):
    bus.set_loop(asyncio.get_running_loop())
    monitor.attach_log_relay()
    log.info(f"[WebUI] FastAPI 面板已就绪 (v{VERSION})")
    yield
    bus.set_loop(None)


app = FastAPI(
    title="Flandora WebUI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)


# ============ 工具 ============


def _self_identity() -> dict:
    """机器人身份（全自动识别）：wxid 取自当前登录账号，昵称优先取自本地库自己的联系人。"""
    acct = wechat_account.current_account(config.WECHAT_FILES_ROOT or None)
    nickname = ""
    bi = state.bridge_instance
    store = getattr(getattr(bi, "_source", None), "store", None)
    if store is not None:
        try:
            nickname = store.self_nickname() or ""
        except Exception:  # noqa: BLE001
            nickname = ""
    return {"wxid": acct.wxid if acct is not None else "", "nickname": nickname}


def get_status() -> dict:
    bi = state.bridge_instance
    source_ready = bi is not None and getattr(bi, "_source", None) is not None
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
        "webui": {
            "host": config.WEB_BIND_HOST,
            "port": config.WEB_PORT,
            "tls": bool(config.WEBUI_TLS),
            "tls_cert_set": os.path.isfile(config.WEBUI_TLS_CERT_FILE),
            "tls_key_set": os.path.isfile(config.WEBUI_TLS_KEY_FILE),
            "tls_cert_path": config.WEBUI_TLS_CERT_FILE,
            "tls_key_path": config.WEBUI_TLS_KEY_FILE,
            "password_set": auth.enabled(),
        },
        "send_method": "uia",
        "ob": {
            "host": config.OB_SERVER_HOST,
            "port": config.OB_SERVER_PORT,
            "connected": sum(n["connections"] for n in ob_networks().status()) > 0,
            "clients": sum(n["connections"] for n in ob_networks().status()),
            "nodes": ob_networks().status(),
        },
        "inbound": {
            "source": "local",
            "ready": source_ready,
        },
        "counters": counters,
        "window": window,
        "window_locked": bool(getattr(config, "WECHAT_HWND", 0)),
        "update": state.update_available,
        "self": _self_identity(),
    }


def _read_config_file() -> dict:
    with open(config.CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _write_config_file(cfg: dict):
    with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
        f.write("\n")


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _write_text(path: str, text: str, mode: int):
    """按指定权限写文本文件。

    私钥先建文件再写内容，不给它「先是 0644、写完再改」的窗口。注意 Windows 上
    chmod 只管只读位，真正的访问控制落在数据目录的 ACL 上；这里保持 0600 是为了
    数据目录被同步到 Linux 机器上时权限也是对的。
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    os.chmod(path, mode)


def _install_tls_pem(payload: dict) -> str:
    """把面板提交的证书/私钥 PEM 落成文件；校验不通过时一个字都不写。

    只提交其中一个也允许——另一半用之前装好的。返回错误信息，空串表示成功。
    """
    import ssl
    import tempfile

    new_cert = str(payload.get("webui_tls_cert") or "")
    new_key = str(payload.get("webui_tls_key") or "")
    if not new_cert and not new_key:
        return ""
    cert_text = new_cert or _read_text(config.WEBUI_TLS_CERT_FILE)
    key_text = new_key or _read_text(config.WEBUI_TLS_KEY_FILE)
    if not cert_text.strip() or not key_text.strip():
        return "证书与私钥要一起装：现在只填了其中一个"

    temps: list[str] = []
    try:
        for text in (cert_text, key_text):
            fd, path = tempfile.mkstemp(prefix="flandora-tls-", suffix=".pem")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            temps.append(path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(temps[0], temps[1])
    except Exception as e:  # noqa: BLE001
        return f"证书或私钥没法解析：{e}"
    finally:
        for path in temps:
            try:
                os.remove(path)
            except OSError:
                pass

    # 验过了才动真文件，避免把一对能用的证书换成写坏的
    _write_text(config.WEBUI_TLS_CERT_FILE, cert_text, 0o644)
    if new_key:
        _write_text(config.WEBUI_TLS_KEY_FILE, key_text, 0o600)
    log.info(f"[WebUI] 已安装面板证书：{config.WEBUI_TLS_CERT_FILE}")
    return ""


# ============ 登录 ============


@app.middleware("http")
async def _require_login(request: Request, call_next):
    """开着密码时，除登录接口外所有 /api 都要会话；静态前端不挡，登录页才加载得出来。"""
    if request.url.path.startswith("/api/") and not auth.is_public_api(request.url.path):
        if auth.enabled() and not auth.sessions.valid(request.cookies.get(auth.COOKIE_NAME)):
            return JSONResponse({"ok": False, "error": "未登录", "auth_required": True}, 401)
    return await call_next(request)


@app.get("/api/auth")
def api_auth(request: Request):
    on = auth.enabled()
    return {
        "enabled": on,
        "authenticated": (not on) or auth.sessions.valid(request.cookies.get(auth.COOKIE_NAME)),
    }


@app.post("/api/login")
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
    log.info("[WebUI] 面板登录成功")
    return {"ok": True, "enabled": True}


@app.post("/api/logout")
def api_logout(request: Request, response: Response):
    auth.sessions.drop(request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


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


# ============ 更新 ============


@app.get("/api/update/status")
def api_update_status():
    """面板要的更新状态：当前版本、安装形态、待安装包、上次查到的版本。"""
    from .. import updater

    return {
        "ok": True,
        "mode": updater.install_mode(),
        "current": VERSION,
        "app_dir": updater.app_dir(),
        "app_dir_writable": updater.can_write_app_dir(),
        "latest": state.update_available,
        "pending": updater.pending(),
    }


@app.post("/api/update/check")
def api_update_check():
    """现查一次 GitHub 发布页。查不到时把原因原样给出来，不假装是最新。"""
    from .. import update_check, updater

    try:
        info = updater.latest_release()
    except updater.UpdateError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 502)
    newer = update_check.is_newer(info["version"], VERSION)
    return {"ok": True, "latest": info, "newer": newer, "current": VERSION}


def _download_and_stage() -> dict:
    from .. import update_check, updater

    info = updater.latest_release()
    if not update_check.is_newer(info["version"], VERSION):
        raise updater.UpdateError(f"当前已经是 {VERSION}，没有更新的版本")
    archive = os.path.join(updater.updates_dir(), f"Flandora-v{info['version']}.zip")
    updater.download(info["asset"], archive)
    return updater.stage(archive, info["version"])


@app.post("/api/update/download")
async def api_update_download():
    """下载并解压到 data/updates，登记为待安装；不碰正在运行的程序目录。"""
    from .. import updater

    try:
        info = await asyncio.to_thread(_download_and_stage)
    except updater.UpdateError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 502)
    return {"ok": True, "pending": info}


@app.post("/api/update/install")
async def api_update_install():
    """写好替换脚本、拉起它，然后退出本进程让脚本接手。"""
    import subprocess

    from .. import updater

    info = updater.pending()
    if not info:
        return JSONResponse({"ok": False, "error": "还没有下载好的安装包"}, 400)
    if updater.install_mode() != "frozen":
        return JSONResponse(
            {
                "ok": False,
                "error": "源码模式没有可替换的安装目录：请在仓库里 git pull 后重装依赖",
            },
            400,
        )
    if not updater.can_write_app_dir():
        return JSONResponse(
            {
                "ok": False,
                "error": "程序目录不可写（常见于 Program Files）：请以管理员身份运行脚本",
            },
            400,
        )
    script = updater.write_swap_script(info)
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
    threading.Timer(1.5, lambda: os._exit(0)).start()
    log.info("[WebUI] 已拉起更新脚本，准备退出：%s", script)
    return {"ok": True, "script": script, "message": "正在退出并替换，完成后会自动重启"}


@app.post("/api/update/cancel")
def api_update_cancel():
    """放弃待安装的包（暂存目录与登记一起删掉）。"""
    import shutil

    from .. import updater

    info = updater.pending()
    if info and info.get("staging"):
        shutil.rmtree(info["staging"], ignore_errors=True)
    updater.clear_pending()
    return {"ok": True}


# ============ 配置 ============


@app.get("/api/config")
def api_get_config():
    try:
        cfg = _read_config_file()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    # 哈希本身不算凭据，但也没必要发给浏览器；面板只需要知道「设没设」
    cfg["webui_password_set"] = bool(str(cfg.pop("webui_password", "") or ""))
    # 这两个键可能是新加的、还没写进 config.json（按默认值在跑），面板要按生效值显示
    cfg.setdefault("web_bind_host", config.WEB_BIND_HOST)
    cfg.setdefault("webui_tls", bool(config.WEBUI_TLS))
    cfg.setdefault("log_max_mb", config.LOG_MAX_MB)
    cfg.setdefault("log_backup_count", config.LOG_BACKUP_COUNT)
    return cfg


@app.put("/api/config")
def api_put_config(payload: dict, response: Response):
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "请求体必须是 JSON 对象"}, 400)
    # 登录密码只以哈希落盘，明文不出后端：面板传上来的明文在这里就地换掉
    payload.pop("webui_password_set", None)
    if "webui_password" in payload or "webui_password_clear" in payload:
        plain = str(payload.pop("webui_password", "") or "")
        clear = bool(payload.pop("webui_password_clear", False))
        if clear:
            hashed = ""
        elif plain:
            try:
                hashed = auth.hash_password(plain)
            except ValueError as e:
                return JSONResponse({"ok": False, "error": str(e)}, 400)
        else:
            hashed = None  # 两者都没给实质内容：不动现有密码
        if hashed is not None:
            payload["webui_password"] = hashed
            config.WEBUI_PASSWORD = hashed
            # 密码变了，之前发出去的会话一律作废；当前这个浏览器立刻换一张新的
            auth.sessions.clear()
            if hashed:
                response.set_cookie(
                    auth.COOKIE_NAME,
                    auth.sessions.issue(),
                    max_age=int(auth.SESSION_TTL),
                    httponly=True,
                    samesite="lax",
                    path="/",
                )
    # 监听地址：只认回环与全网卡两种；对外之前必须有密码，否则等于把面板公开
    if "web_bind_host" in payload:
        bind = str(payload.get("web_bind_host") or "").strip()
        if bind not in ALLOWED_BIND_HOSTS:
            return JSONResponse(
                {"ok": False, "error": "web_bind_host 只能是 127.0.0.1 或 0.0.0.0"}, 400
            )
        if bind == "0.0.0.0" and not auth.enabled():
            return JSONResponse(
                {"ok": False, "error": "绑所有网卡之前先设面板密码：没密码时局域网里谁都能改配置"},
                400,
            )
        payload = dict(payload, web_bind_host=bind)
    # 证书/私钥：校验成对可解析后再落文件，内容不进 config.json
    if any(field in payload for field in PEM_FIELDS):
        problem = _install_tls_pem(payload)
        if problem:
            return JSONResponse({"ok": False, "error": problem}, 400)
        payload = {k: v for k, v in payload.items() if k not in PEM_FIELDS}
    if "webui_tls" in payload:
        tls_on = bool(payload["webui_tls"])
        if tls_on and not (
            os.path.isfile(config.WEBUI_TLS_CERT_FILE) and os.path.isfile(config.WEBUI_TLS_KEY_FILE)
        ):
            return JSONResponse({"ok": False, "error": "要先装好证书与私钥才能启用 HTTPS"}, 400)
        payload = dict(payload, webui_tls=tls_on)
    # 枚举字段先校验再落盘：写进去再静默回退，用户改错了也看不出来
    if "ob_message_format" in payload:
        fmt = str(payload.get("ob_message_format") or "").strip().lower()
        if fmt not in ("array", "string"):
            return JSONResponse(
                {"ok": False, "error": "ob_message_format 只能是 array 或 string"}, 400
            )
        payload = dict(payload, ob_message_format=fmt)
        config.OB_MESSAGE_FORMAT = fmt
    if "ob_networks" in payload:
        try:
            nodes = ob_network.normalize_nodes(payload.get("ob_networks"))
        except ob_network.NetworkConfigError as e:
            return JSONResponse({"ok": False, "error": f"网络入口配置不合法：{e}"}, 400)
        payload = dict(payload, ob_networks=nodes)
        # 入口清空等于「什么都不监听」，退回通用设置那一个入口，避免面板上配空了就连不上
        config.OB_NETWORKS = nodes or [
            ob_network.default_node(
                config.OB_SERVER_HOST, config.OB_SERVER_PORT, config.OB_SERVER_TOKEN
            )
        ]
    if "ob_heartbeat_seconds" in payload:
        try:
            seconds = int(payload.get("ob_heartbeat_seconds"))
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "ob_heartbeat_seconds 必须是整数"}, 400)
        if seconds != 0 and not 5 <= seconds <= 3600:
            return JSONResponse(
                {"ok": False, "error": "ob_heartbeat_seconds 取 0（不发心跳）或 5–3600 秒"}, 400
            )
        payload = dict(payload, ob_heartbeat_seconds=seconds)
        config.OB_HEARTBEAT_SECONDS = seconds
    # 日志策略：范围先校验，落盘之后再改运行中的 handler（见下方即时生效段）
    log_policy = None
    if "log_max_mb" in payload or "log_backup_count" in payload:
        try:
            max_mb = int(payload.get("log_max_mb", config.LOG_MAX_MB))
            backups = int(payload.get("log_backup_count", config.LOG_BACKUP_COUNT))
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "日志上限与保留份数必须是整数"}, 400)
        if not 1 <= max_mb <= 1024:
            return JSONResponse({"ok": False, "error": "日志单份上限取 1–1024（MB）"}, 400)
        if not 1 <= backups <= 20:
            return JSONResponse({"ok": False, "error": "日志保留份数取 1–20"}, 400)
        payload = dict(payload, log_max_mb=max_mb, log_backup_count=backups)
        log_policy = (max_mb, backups)
    try:
        current = _read_config_file()
        current.update(payload)
        _write_config_file(current)

        # 即时生效：config 模块属性
        for key, attr in HOT_CONFIG_ATTRS.items():
            if key in payload:
                setattr(config, attr, payload[key])

        # 面板如实反映刚保存的意图：真正换绑与换协议仍要重启
        if "web_bind_host" in payload:
            config.WEB_BIND_HOST = payload["web_bind_host"]
        if "webui_tls" in payload:
            config.WEBUI_TLS = bool(payload["webui_tls"])
        # 日志策略即时生效：上限与保留份数直接改到运行中的 handler 上
        if log_policy is not None:
            config.apply_log_policy(*log_policy)

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


# ============ 配置备份 / 恢复 ============


@app.get("/api/config/backup")
def api_config_backup(include_sensitive: int = 0):
    """导出 config.json，交给面板存成文件。默认不带凭据。"""
    try:
        cfg = _read_config_file()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    excluded = []
    if not include_sensitive:
        for key in SENSITIVE_KEYS:
            if key in cfg:
                cfg.pop(key)
                excluded.append(key)
    return {
        "kind": "flandora-config-backup",
        "version": VERSION,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "include_sensitive": bool(include_sensitive),
        "excluded_sensitive": excluded,
        "config": cfg,
    }


@app.post("/api/config/restore")
def api_config_restore(payload: dict):
    """从备份恢复配置：先给现有的 config.json 存一份，再按键合并写回。"""
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "请求体必须是 JSON 对象"}, 400)
    incoming = payload.get("config")
    if not isinstance(incoming, dict) or not incoming:
        return JSONResponse(
            {"ok": False, "error": "这个文件里没有 config 对象，不像是本程序的备份"}, 400
        )
    incoming = {k: v for k, v in incoming.items() if not str(k).startswith("_")}
    if not incoming:
        return JSONResponse({"ok": False, "error": "备份里没有可恢复的配置项"}, 400)
    if not payload.get("include_sensitive", False):
        # 不带敏感项的备份本来就没有这些键；万一有也不覆盖本地现值
        for key in SENSITIVE_KEYS:
            incoming.pop(key, None)
    try:
        current = _read_config_file()
        backup_path = f"{config.CONFIG_FILE}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=4)
            f.write("\n")
        current.update(incoming)
        _write_config_file(current)
    except Exception as e:
        log.error(f"[WebUI] 恢复配置失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    # 能即时生效的立刻生效，其余等重启；密码哈希换了要连会话一起作废
    password_restored = False
    applied_now = set()
    for key, attr in HOT_CONFIG_ATTRS.items():
        if key in incoming:
            setattr(config, attr, incoming[key])
            applied_now.add(key)
    if "webui_password" in incoming:
        config.WEBUI_PASSWORD = incoming["webui_password"]
        auth.sessions.clear()
        password_restored = True
    if "log_max_mb" in incoming or "log_backup_count" in incoming:
        try:
            max_mb = int(incoming.get("log_max_mb", config.LOG_MAX_MB))
            backups = int(incoming.get("log_backup_count", config.LOG_BACKUP_COUNT))
        except (TypeError, ValueError):
            max_mb = backups = 0
        if 1 <= max_mb <= 1024 and 1 <= backups <= 20:
            config.apply_log_policy(max_mb, backups)
            applied_now.update({"log_max_mb", "log_backup_count"})
    log.info(
        f"[WebUI] 已从备份恢复配置 (字段: {', '.join(sorted(incoming))})，"
        f"原文件已备份为 {os.path.basename(backup_path)}"
    )
    monitor.notify_status()
    return {
        "ok": True,
        "restored": sorted(incoming),
        "backup_path": backup_path,
        "password_restored": password_restored,
        "restart_required": sorted(
            k for k in incoming if k not in applied_now and k != "webui_password"
        ),
    }


# ============ 存储 ============


@app.get("/api/storage")
def api_storage():
    """数据目录占用一览（只读，不建库、不动任何文件）。"""
    try:
        return {"ok": True, **storage.overview()}
    except Exception as e:  # noqa: BLE001
        log.error(f"[WebUI] 统计存储占用失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)


STORAGE_TARGETS = {
    "images": storage.clean_images,
    "wx_cache": storage.clean_wx_cache,
    "updates": storage.clean_updates,
    "old_logs": storage.clean_old_logs,
    "rotate_log": storage.rotate_log,
}


@app.post("/api/storage/clean")
def api_storage_clean(payload: dict):
    """按目标清理数据目录的一类内容。删什么是固定的几类，不接受任意路径。"""
    target = str((payload or {}).get("target") or "").strip()
    func = STORAGE_TARGETS.get(target)
    if func is None:
        return JSONResponse({"ok": False, "error": f"不认识的清理目标：{target or '(空)'}"}, 400)
    try:
        result = func()
    except Exception as e:  # noqa: BLE001
        log.error(f"[WebUI] 清理 {target} 失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    log.info(f"[WebUI] 存储清理 {target}: {result}")
    return {"ok": True, "target": target, **result}


@app.post("/api/storage/messages")
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
        log.error(f"[WebUI] 消息存档操作 {action} 失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    return JSONResponse({"ok": False, "error": f"不认识的动作：{action or '(空)'}"}, 400)


# ============ 消息 / 日志 / 统计 ============


@app.get("/api/messages")
def api_messages(limit: int = 100):
    limit = max(1, min(limit, 300))
    with bus._lock:
        msgs = list(bus.messages)[:limit]
    return {"messages": msgs}


@app.get("/api/networks")
def api_networks():
    """各个 OneBot 网络入口的运行态。"""
    return {"networks": ob_networks().status()}


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
    """测试发送的候选联系人：配置里的群名映射 + 微信库名册 + 已见过的会话。

    名册来自本地库（好友与群），所以新装的环境在还没收到任何消息时也能选到人；
    文件传输助手不在名册里（它不算好友），单独补一条，它是最安全的发送自测对象。
    """
    contacts = []
    seen_ids = set()
    seen_names = set()

    def add(cid, name, kind):
        name = str(name or "").strip()
        cid = str(cid or "").strip()
        if not name or name in seen_names:
            return
        if cid and cid in seen_ids:
            return
        contacts.append({"id": cid, "name": name, "kind": kind})
        seen_names.add(name)
        if cid:
            seen_ids.add(cid)

    for gid, name in (config.GROUP_NAME_MAP or {}).items():
        add(str(gid), name, "group")

    store = getattr(getattr(state.bridge_instance, "_source", None), "store", None)
    if store is not None:
        try:
            for group in store.groups():
                add(group.get("username"), group.get("name"), "group")
        except Exception as e:  # noqa: BLE001 - 名册读不到不影响配置里的条目
            log.warning(f"[WebUI] 读取群名册失败: {e}")
        try:
            for person in store.contacts():
                add(person.get("username"), person.get("name"), "private")
        except Exception as e:  # noqa: BLE001
            log.warning(f"[WebUI] 读取好友名册失败: {e}")
    add("filehelper", "文件传输助手", "private")

    for oid, name in state._ob_id_to_contact.items():
        add(str(oid), name, "group" if str(oid).endswith("@chatroom") else "private")
    return {"contacts": contacts}


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

    log.info(f"[WebUI] 测试发送 -> {contact}: {text[:50]}")
    try:
        ok = await asyncio.to_thread(sender.send_text, contact, text)
    except Exception as e:
        log.error(f"[WebUI] 测试发送异常: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)

    if ok:
        monitor.record_outbound(contact, "manual", text)
        log.info(f"[WebUI] 测试发送成功 -> {contact}")
        return {"ok": True}
    log.warning(f"[WebUI] 测试发送失败 -> {contact}")
    return {"ok": False, "error": getattr(sender, "_last_error", "") or "发送失败，详见运行日志"}


# ============ 调试台 ============


@app.get("/api/debug/actions")
def api_debug_actions():
    """动作目录。

    面板据此生成参数表单——与桥接派发用的是同一张表，所以这里列出来的东西
    一定是真的会被执行的东西，不存在「面板里有、协议端没有」的漂移。
    """
    from .. import action_catalog

    catalog = action_catalog.catalog_dict()
    return {
        "categories": catalog["categories"],
        "actions": catalog["actions"],
    }


@app.post("/api/debug/invoke")
async def api_debug_invoke(payload: dict):
    """在本进程里直接跑一遍动作链路。

    走的是客户端连进来时的那条同一条路（`_handle_ob_api`），返回真正的响应体，
    所以这里调得通的东西，OneBot 客户端也调得通；不经过网络，省去连一条 WS。
    """
    from .. import ob_protocol

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


@app.get("/api/debug/events")
def api_debug_events(limit: int = 30):
    """最近推送出去的事件（读消息存档，与客户端收到的是同一份）。"""
    from .. import ob_store

    try:
        events = ob_store.message_store().recent(max(1, min(int(limit or 30), 200)))
    except Exception as e:  # noqa: BLE001
        log.error(f"[WebUI] 读取事件存档失败: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 500)
    return {"events": events}


# ============ 微信窗口识别（多开场景） ============


@app.get("/api/windows")
def api_windows():
    """枚举所有微信窗口，标记当前绑定/配置锁定的窗口。"""
    from ..uia_sender import enum_wechat_windows

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
        w["current"] = w["hwnd"] == cur_hwnd
        w["saved"] = w["hwnd"] == saved_hwnd
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
    from ..uia_sender import flash_window

    try:
        hwnd = int((payload or {}).get("hwnd", 0) or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "hwnd 必须是整数"}, 400)
    if not hwnd:
        return JSONResponse({"ok": False, "error": "hwnd 不能为 0"}, 400)
    ok = flash_window(hwnd)
    return {"ok": ok}


# ============ 隔离桌面（微信跑在独立桌面上，登录靠截图） ============


@app.get("/api/desktop/status")
def api_desktop_status():
    """隔离桌面的运行与登录状态。"""
    try:
        from .. import wx_desktop
    except ImportError as e:
        return JSONResponse({"ok": False, "error": f"隔离桌面模块不可用: {e}"}, 500)
    try:
        return {"ok": True, **wx_desktop.manager().status()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)


@app.post("/api/desktop/start")
def api_desktop_start():
    """在隔离桌面上启动微信（已启动时是空操作）。"""
    try:
        from .. import wx_desktop
    except ImportError as e:
        return JSONResponse({"ok": False, "error": f"隔离桌面模块不可用: {e}"}, 500)
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


@app.post("/api/desktop/stop")
def api_desktop_stop():
    """结束隔离桌面上的微信并释放桌面（只动本程序启动或接管的那个）。"""
    try:
        from .. import wx_desktop
    except ImportError as e:
        return JSONResponse({"ok": False, "error": f"隔离桌面模块不可用: {e}"}, 500)
    wx_desktop.manager().stop()
    return {"ok": True, **wx_desktop.manager().status()}


@app.post("/api/desktop/quit-default-wechat")
def api_desktop_quit_default_wechat():
    """结束默认桌面上（用户自己开着）的微信。

    隔离模式要求微信由本程序启动，但用户桌面上那个微信不会被动——只有点了
    这个接口才结束它，避免静默杀掉用户正在用的微信。
    """
    try:
        from .. import wx_desktop
    except ImportError as e:
        return JSONResponse({"ok": False, "error": f"隔离桌面模块不可用: {e}"}, 500)
    killed = wx_desktop.quit_default_desktop_wechat()
    if not killed:
        return JSONResponse({"ok": False, "error": "没找到默认桌面上的微信窗口"}, 404)
    return {"ok": True, "killed": killed}


@app.get("/api/desktop/screenshot")
def api_desktop_screenshot():
    """隔离桌面上微信窗口的实况截图（PNG）。

    隔离桌面用户看不见，登录必须靠这个画面；二维码与「需在手机上完成登录」
    这类界面都在这里显示。
    """
    try:
        from .. import wx_desktop
    except ImportError as e:
        return JSONResponse({"ok": False, "error": f"隔离桌面模块不可用: {e}"}, 500)
    mgr = wx_desktop.manager()
    if not mgr.enabled():
        return JSONResponse({"ok": False, "error": "隔离桌面模式未开启"}, 400)
    png = mgr.screenshot_png()
    if png is None:
        return JSONResponse({"ok": False, "error": "没取到画面：微信窗口不在或尚未出现"}, 404)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/desktop/click")
def api_desktop_click(payload: dict):
    """把画面上的点击转发到隔离桌面上的微信窗口。

    坐标是**客户区坐标**（相对窗口左上角），与 /api/desktop/screenshot 的像素
    一一对应——前端按图片显示尺寸等比换算即可。
    """
    try:
        from .. import wx_desktop
    except ImportError as e:
        return JSONResponse({"ok": False, "error": f"隔离桌面模块不可用: {e}"}, 500)
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


# ============ 取钥向导（四步：免责声明 → 数据库口令 → 图片密钥 → 校验） ============


def _wizard_or_error():
    """取向导单例；模块缺失或不满足前置条件时返回 (None, 错误响应)。"""
    try:
        from .. import wizard as wizard_mod
    except ImportError as e:
        return None, JSONResponse({"ok": False, "error": f"取钥向导模块不可用: {e}"}, 500)
    return wizard_mod.wizard(), None


@app.get("/api/wizard/status")
def api_wizard_status():
    """向导四步的进度、口令与图片密钥的持有情况。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.status()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 500)


@app.post("/api/wizard/disclaimer")
def api_wizard_disclaimer():
    """确认免责声明。界面上的勾选框不打勾就不该走到这里。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.accept_disclaimer()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)


@app.post("/api/wizard/dbkey/start")
def api_wizard_dbkey_start():
    """调起取钥器，等用户在微信里重新登录一次。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.start_dbkey()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)


@app.post("/api/wizard/dbkey/cancel")
def api_wizard_dbkey_cancel():
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    return {"ok": True, **wiz.cancel_dbkey()}


@app.post("/api/wizard/dbkey/manual")
def api_wizard_dbkey_manual(payload: dict):
    """手动填入口令。会先拿当前账号的真库验一遍，过了才落盘。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.save_manual_passphrase((payload or {}).get("passphrase", ""))}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)


@app.post("/api/wizard/imagekey/start")
def api_wizard_imagekey_start():
    """开始扫内存找图片密钥。跑起来之后要在实况画面里点开一张没看过的图。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.start_imagekey()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)


@app.post("/api/wizard/imagekey/cancel")
def api_wizard_imagekey_cancel():
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    return {"ok": True, **wiz.cancel_imagekey()}


@app.post("/api/wizard/verify")
def api_wizard_verify():
    """拿当前口令逐个库过页 1 HMAC，结果异步写回状态里。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    try:
        return {"ok": True, **wiz.verify()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)


@app.post("/api/wizard/reset")
def api_wizard_reset():
    """把四步清回起点（免责声明的确认保留）。"""
    wiz, err = _wizard_or_error()
    if err is not None:
        return err
    return {"ok": True, **wiz.reset()}


# ============ WebSocket 实时推送 ============


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    if auth.enabled() and not auth.sessions.valid(websocket.cookies.get(auth.COOKIE_NAME)):
        # 先关再收：没登录的连接拿不到任何事件
        await websocket.close(code=4401)
        return
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
        return JSONResponse(
            {
                "ok": False,
                "error": "前端尚未构建。请在 webui-frontend 目录执行 npm install && npm run build",
            },
            503,
        )


def run(host: str, port: int):
    """由 main.py 调用，在主线程阻塞运行 uvicorn。"""
    import uvicorn

    tls: dict = {}
    if config.WEBUI_TLS:
        # 证书不能加载时 main.py 已经拦下来了，不会走到这里
        tls = {
            "ssl_certfile": config.WEBUI_TLS_CERT_FILE,
            "ssl_keyfile": config.WEBUI_TLS_KEY_FILE,
        }
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False, **tls)
