"""面板路由共用的取用层：进程级状态、配置仓库与状态响应。

各路由只从这里取运行期对象，不自己去摸 ``RUNTIME`` 之外的内部字段（例如
``RUNTIME.bridge._source``）——那些不是面板的契约，换了实现就会静默读到 None。

``status_payload()`` 是 ``/api/status`` 的唯一实现：状态组直接返回它，控制组在
执行完启停动作后复用它回包，两处不会各算一份「运行中」。
"""

import logging
import os
import time

from .. import __version__, config, wechat_account
from ..ob_network import manager as ob_networks
from ..runtime import LIFECYCLE, RUNTIME, events
from . import auth

log = logging.getLogger("flandora")


def store():
    """当前本地库只读视图；收件源未就绪时为 None。"""
    return RUNTIME.store


def sender():
    """当前发送器；桥接未起或还没初始化完时为 None。"""
    return RUNTIME.sender


def config_store():
    """当前的配置仓库。每次现取：测试会用 config.temporary() 整份换掉。"""
    return config.get_store()


def self_identity() -> dict:
    """机器人身份（全自动识别）：wxid 取自当前登录账号，昵称优先取自本地库自己的联系人。"""
    acct = wechat_account.current_account(config.WECHAT_FILES_ROOT or None)
    nickname = ""
    store_view = store()
    if store_view is not None:
        try:
            nickname = store_view.self_nickname() or ""
        except Exception:  # noqa: BLE001
            nickname = ""
    return {"wxid": acct.wxid if acct is not None else "", "nickname": nickname}


def status_payload() -> dict:
    """面板要的完整运行状态：启停与暂停、端口与 TLS、OneBot 入口、计数、窗口与身份。"""
    snap = LIFECYCLE.snapshot()
    source_ready = store() is not None
    uptime = None
    if snap.is_active and snap.started_at:
        uptime = round(time.time() - snap.started_at, 1)
    window = None
    sender_view = sender()
    if sender_view is not None:
        try:
            window = sender_view.current_window_info()
        except Exception:
            window = None
    counters = events.EVENTS.counters_copy()
    nodes = ob_networks().status()
    clients = sum(n["connections"] for n in nodes)
    return {
        "running": snap.is_active,
        "paused": snap.paused,
        "uptime_sec": uptime,
        "pid": os.getpid(),
        "version": __version__,
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
            "connected": clients > 0,
            "clients": clients,
            "nodes": nodes,
        },
        "inbound": {
            "source": "local",
            "ready": source_ready,
        },
        "counters": counters,
        "window": window,
        "window_locked": bool(getattr(config, "WECHAT_HWND", 0)),
        "update": RUNTIME.update_available,
        "self": self_identity(),
    }


def on_config_changed(settings, changed: frozenset) -> None:
    """HOT 字段写完要动进程状态的那部分：微信窗口换绑。"""
    if "wechat_hwnd" not in changed:
        return
    sender_view = sender()
    if sender_view is None:
        return
    hwnd = int(settings.WECHAT_HWND or 0)
    if sender_view.window_handle() == hwnd:
        return
    try:
        sender_view.rebind_window(hwnd)
    except Exception as exc:  # 窗口没了不该让保存失败，绑没绑上看下次发送
        log.error(f"[WebUI] 重绑微信窗口失败: {exc}")
