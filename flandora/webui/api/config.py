"""配置组：读取、合并保存、导出备份、从备份恢复。

取值校验统一在 ``flandora.config.fields``（文件加载与面板保存共用同一份规则），
本组只做请求编排：密码换哈希、PEM 落文件、绑全网卡前必须有密码。

保存与恢复都会即时生效一部分字段（HOT），并由 ``deps.on_config_changed`` 看窗口
换绑；面板在这里补一次状态推送，让别的页面立刻看到新配置。
"""

import logging
import os
import time

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from ... import __version__, config, ob_network
from ...config import fields as config_fields
from ...runtime import events
from .. import auth, deps

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/config": {"GET": "call", "PUT": "call"},
    "/api/config/schema": {"GET": "call"},
    "/api/config/backup": {"GET": "call"},
    "/api/config/restore": {"POST": "call"},
}

# 面板提交的 PEM 内容：落成文件，不进 config.json。
# 「哪些键写进文件后立刻生效」「哪些算凭据不能进备份」都由字段表派生，
# 见 flandora/config/fields.py 的 kind。
PEM_FIELDS = ("webui_tls_cert", "webui_tls_key")


def _clean_context() -> dict:
    """严格校验要参照的取值（ob_networks 兜底入口与证书默认落点都靠它）。"""
    settings = deps.config_store().settings
    return {
        "data_dir": config.data_dir(),
        "ob_server_host": settings.OB_SERVER_HOST,
        "ob_server_port": settings.OB_SERVER_PORT,
        "ob_server_token": settings.OB_SERVER_TOKEN,
    }


def _strict(key: str, value, context: dict):
    """按字段表严格校验一项；非法值抛 ConfigError（消息直接回给浏览器）。"""
    return config_fields.clean_field(key, value, True, context=context).value


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
        log.error(f"[面板] 证书或私钥没法解析: {e}")
        return "证书或私钥没法解析，详细原因见运行日志"
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
    log.info(f"[面板] 已安装面板证书：{config.WEBUI_TLS_CERT_FILE}")
    return ""


@router.get("/api/config")
def api_get_config():
    try:
        cfg = dict(deps.config_store().read_raw())
    except Exception as e:
        log.error(f"[面板] 读配置失败: {e}")
        return JSONResponse({"ok": False, "error": "读不到配置文件，详细原因见运行日志"}, 500)
    # 哈希本身不算凭据，但也没必要发给浏览器；面板只需要知道「设没设」
    cfg["webui_password_set"] = bool(str(cfg.pop("webui_password", "") or ""))
    # 这几个键可能是新加的、还没写进 config.json（按默认值在跑），面板要按生效值显示
    cfg.setdefault("web_bind_host", config.WEB_BIND_HOST)
    cfg.setdefault("webui_tls", bool(config.WEBUI_TLS))
    cfg.setdefault("log_max_mb", config.LOG_MAX_MB)
    cfg.setdefault("log_backup_count", config.LOG_BACKUP_COUNT)
    return cfg


@router.get("/api/config/schema")
def api_config_schema():
    """面板「高级」页的说明来源：每个键的类型、默认值、生效方式与一句话说明。"""
    schema = config_fields.schema()
    return {
        "ok": True,
        "fields": schema,
        "restart_keys": [item["key"] for item in schema if item["restart"]],
    }


@router.put("/api/config")
def api_put_config(payload: dict, response: Response):
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "请求体必须是 JSON 对象"}, 400)
    # 登录密码只以哈希落盘，明文不出后端：面板传上来的明文在这里就地换掉
    payload.pop("webui_password_set", None)
    hashed: str | None = None
    if "webui_password" in payload or "webui_password_clear" in payload:
        given = "webui_password" in payload
        plain = str(payload.pop("webui_password", "") or "")
        clear = bool(payload.pop("webui_password_clear", False))
        # 显式提交空串就是「清掉密码」；要留着旧密码就别带 webui_password 这个键。
        if given and not plain.strip():
            clear = True
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
    context = _clean_context()
    try:
        # 监听地址：只认回环与全网卡两种；对外之前必须有密码，否则等于把面板公开。
        # 清密码是同一件事的另一面：只带 webui_password 时也要按现行绑定复查，
        # 否则盘上会留下「0.0.0.0 + 无密码」，下次启动面板直接拒绝启动。
        if "web_bind_host" in payload or "webui_password" in payload:
            bind = _strict(
                "web_bind_host", payload.get("web_bind_host", config.WEB_BIND_HOST), context
            )
            pending = payload.get("webui_password", config.WEBUI_PASSWORD)
            if bind == "0.0.0.0" and not auth.is_hashed(pending):
                raise config.ConfigError(
                    "绑所有网卡之前先设面板密码：没密码时局域网里谁都能改配置"
                    "（要清密码，就把「绑定地址」一并改回 127.0.0.1）"
                )
            if "web_bind_host" in payload:
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
                os.path.isfile(config.WEBUI_TLS_CERT_FILE)
                and os.path.isfile(config.WEBUI_TLS_KEY_FILE)
            ):
                raise config.ConfigError("要先装好证书与私钥才能启用 HTTPS")
            payload = dict(payload, webui_tls=tls_on)
        # 枚举、网络入口、心跳、日志策略：与文件加载共用同一份校验规则
        for key in ("ob_message_format", "ob_heartbeat_seconds"):
            if key in payload:
                payload = dict(payload, **{key: _strict(key, payload[key], context)})
        if "ob_networks" in payload:
            # 只校验；落盘保留用户写的内容（空数组就是空数组），兜底入口由配置层算
            _strict("ob_networks", payload["ob_networks"], context)
            payload = dict(payload, ob_networks=ob_network.normalize_nodes(payload["ob_networks"]))
        if "log_max_mb" in payload or "log_backup_count" in payload:
            payload = dict(
                payload,
                log_max_mb=_strict(
                    "log_max_mb", payload.get("log_max_mb", config.LOG_MAX_MB), context
                ),
                log_backup_count=_strict(
                    "log_backup_count",
                    payload.get("log_backup_count", config.LOG_BACKUP_COUNT),
                    context,
                ),
            )
    except config.ConfigError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)
    try:
        result = deps.config_store().update(payload)
        log.info(f"[面板] 配置已保存 (字段: {', '.join(payload.keys())})")
        if "webui_password" in payload:
            # 落盘成功才作废旧会话：保存被拒时用户留在原会话里，不会被莫名登出。
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
        events.notify_status()
        return {
            "ok": True,
            "restart_required": list(result.restart_required),
            "ignored_keys": list(result.ignored_keys),
        }
    except config.ConfigError as e:
        # 落盘前的严格校验（与文件加载共用）在这一层抛出：值不合法是用户输入问题，
        # 回 400 并原样给出原因，不能混进「保存失败，见日志」的 500 里。
        return JSONResponse({"ok": False, "error": str(e)}, 400)
    except Exception as e:
        log.error(f"[面板] 保存配置异常: {e}")
        return JSONResponse({"ok": False, "error": "保存失败，详细原因见运行日志"}, 500)


@router.get("/api/config/backup")
def api_config_backup(include_sensitive: int = 0):
    """导出 config.json，交给面板存成文件。默认不带凭据。"""
    try:
        cfg = dict(deps.config_store().read_raw())
    except Exception as e:
        log.error(f"[面板] 导出配置失败: {e}")
        return JSONResponse({"ok": False, "error": "导出配置失败，详细原因见运行日志"}, 500)
    excluded = []
    if not include_sensitive:
        for key in config_fields.secret_keys():
            if key in cfg:
                cfg.pop(key)
                excluded.append(key)
    return {
        "kind": "flandora-config-backup",
        "version": __version__,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "include_sensitive": bool(include_sensitive),
        "excluded_sensitive": excluded,
        "config": cfg,
    }


@router.post("/api/config/restore")
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
        for key in config_fields.secret_keys():
            incoming.pop(key, None)
        if not incoming:
            # 剔完只剩敏感项：什么都没恢复，不能回 ok 让调用方以为恢复成功
            return JSONResponse(
                {"ok": False, "error": "备份里只有凭据类配置，勾上「包含敏感项」再恢复"}, 400
            )
    store = deps.config_store()
    try:
        previous = store.read_raw()
        store.replace(incoming)
    except config.ConfigError as e:
        # 备份里的值过不了校验：当成请求数据的问题，别写进文件
        log.error(f"[面板] 恢复配置被拒绝: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, 400)
    except Exception as e:
        log.error(f"[面板] 恢复配置失败: {e}")
        return JSONResponse({"ok": False, "error": "恢复配置失败，详细原因见运行日志"}, 500)
    # 替换成功之后才落备份：被拒的恢复不该在数据目录里留下含密码哈希的副本
    backup_name = ""
    try:
        backup_name = f"config.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        config.write_json(os.path.join(store.data_dir(), backup_name), previous)
    except OSError as e:
        log.error(f"[面板] 恢复配置后写备份失败: {e}")
    # 能即时生效的（HOT）已经由订阅者套上；密码哈希换了要连会话一起作废
    password_restored = "webui_password" in incoming
    if password_restored:
        auth.sessions.clear()
    log.info(
        f"[面板] 已从备份恢复配置 (字段: {', '.join(sorted(incoming))})，"
        f"恢复前的文件存为 {backup_name or '（写备份失败，见错误日志）'}"
    )
    events.notify_status()
    return {
        "ok": True,
        "restored": sorted(incoming),
        "backup_name": backup_name,
        "password_restored": password_restored,
        "restart_required": sorted(key for key in incoming if config_fields.is_restart(key)),
    }
