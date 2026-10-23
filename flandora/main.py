"""
入口与生命周期管理模块。

负责桥接的启动、停止、主循环重连逻辑，以及命令行入口。
"""

import json
import logging
import os
import sys
import threading
import time


from . import state
from . import config
from . import update_check
from . import wechat_account
from .single_instance import acquire_mutex
from .senders import create_sender
from .ob_server import _run_ob_server
from .bridge_core import Flandora
from . import __version__

log = logging.getLogger("ob11-bridge")


# ============ 启动 / 停止 ============


def _start_bridge():
    with state.run_lock:
        if state.running:
            return
        state.running = True
    state.bridge_started_at = time.time()
    state.paused.clear()

    # 先把 OB11 服务端拉起来——发消息可能依赖微信窗口搜索，先保证能收消息
    if not state.ob_server_started:
        t = threading.Thread(target=_run_ob_server, daemon=True, name="ob11-server")
        t.start()
        state.ob_server_started = True

    # create_sender() 会做 UIA 窗口枚举，可能阻塞（微信最小化/锁屏时尤甚），
    # 放到独立线程避免卡住 _bridge_loop（SSE 收消息）的启动。
    def _create_sender_async():
        # 隔离桌面模式下微信由本程序启动，必须先把它拉起来，否则下面找不到窗口
        try:
            from . import wx_desktop
            mgr = wx_desktop.manager()
            if mgr.enabled():
                log.info("[Web] 隔离桌面模式：正在其上启动微信")
                if not mgr.start():
                    log.error("[Web] 隔离桌面启动失败：%s",
                              mgr.status().get("error", ""))
        except Exception as exc:  # noqa: BLE001
            log.error(f"隔离桌面启动异常: {exc}")
        try:
            state.sender_instance = create_sender()
        except Exception as exc:  # noqa: BLE001
            log.error(f"创建 UIA 发送器失败: {exc}")

    threading.Thread(target=_create_sender_async, daemon=True, name="sender-init").start()

    state.bridge_thread = threading.Thread(target=_bridge_loop, daemon=True, name="bridge")
    state.bridge_thread.start()
    log.info("[Web] 已启动")


def _stop_bridge():
    with state.run_lock:
        state.running = False

    # 关闭 WebSocket 服务端连接
    _ws = state._ob_ws
    _loop = state._ob_ws_loop
    if _ws:
        try:
            if _loop and _loop.is_running():
                import asyncio
                asyncio.run_coroutine_threadsafe(
                    _ws.close(), _loop
                )
                log.info("[Web] WebSocket 连接已关闭")
        except Exception as e:
            log.warning(f"[Web] 关闭 WebSocket 异常: {e}")

    state._ob_ws_ready.clear()

    # 重置启动标记，让下次 start 能重新拉起 WebSocket 服务端线程
    state.ob_server_started = False
    state._ob_ws_loop = None

    log.info("[Web] 已停止")


def _bridge_loop():
    import ctypes
    ctypes.windll.ole32.CoInitialize(None)

    log.info(f"Bridge | 收件: local | OB11: {config.ASTRBOT_OB_URL} | 发送: uia")

    bridge = Flandora(state.sender_instance)
    with state.bridge_lock:
        state.bridge_instance = bridge

    # 本地库收件自己轮询、自己重试，起不来时（还没登录、口令没取到）
    # 隔一会儿再试，避免把失败当成功直接退出。
    while state.running:
        try:
            bridge.listen_local_db()
        except Exception as e:  # noqa: BLE001
            log.error(f"本地库收件异常：{e}")
        if not state.running:
            break
        log.warning("本地库收件未就绪，10s 后重试")
        for _ in range(10):
            if not state.running:
                break
            time.sleep(1)

    with state.bridge_lock:
        state.bridge_instance = None


def start_web():
    from .webui.app import run as run_webui
    log.info(f"WebUI: http://127.0.0.1:{config.WEB_PORT}")
    run_webui("127.0.0.1", config.WEB_PORT)


def _auto_open_webui():
    """启动后自动打开 WebUI（延迟 2 秒等 uvicorn 就绪）。"""
    time.sleep(2)
    try:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{config.WEB_PORT}")
        log.info("[WebUI] 已自动打开浏览器")
    except Exception as e:
        log.warning(f"[WebUI] 自动打开浏览器失败: {e}")


# ============ 账号切换兜底 ============

_account_watcher = None


def _rebuild_for_account(old, new):
    """作废并重建只对「某个登录账号」成立的状态。

    密钥、联系人映射、机器人身份都绑定在登录账号上。切换账号后继续沿用旧值，
    消息会从错的身份发给错的会话，因此这里刻意清空映射——下游拿不到映射会
    如实报发送失败（ob_protocol 已不再返回假成功），而不是退回上一个账号的
    联系人。
    """
    old_wxid = old.wxid if old else "（未识别）"
    state._ob_id_to_contact.clear()
    state._self_id_int = state._wxid_demouser10001(new.wxid)
    state._self_name = config.SELF_NAME or new.wxid
    log.warning("=" * 64)
    log.warning(f"[账号] 微信登录账号已切换：{old_wxid} -> {new.wxid}")
    log.warning(f"[账号] 新账号数据目录：{new.data_dir or '（未找到）'}")
    log.warning("[账号] 已作废 OneBot 会话映射（联系人 / 群名缓存）")
    log.warning(f"[账号] 机器人身份已切换为 {new.wxid}（self_id={state._self_id_int}）")
    log.warning("[账号] 本地库密钥只对上一个账号有效，新账号的库需重新提取密钥后才能读取")
    log.warning("[账号] 图片密钥同样是账号级的，需对新账号重取；在此之前图片消息只给占位符")
    log.warning("[账号] 给新账号发消息前请重新确认目标，映射已清空的会话会如实报失败")
    log.warning("=" * 64)


def start_account_watch():
    """启动登录账号监视。识别不出当前账号时只记一条日志，不影响 bridge 启动。"""
    global _account_watcher
    root = config.WECHAT_FILES_ROOT or wechat_account.default_root()
    current = wechat_account.current_account(root)
    if current is None:
        log.warning(f"[账号] 未能在 {root} 下识别当前登录账号，账号切换兜底不生效")
        return None
    log.info(f"[账号] {wechat_account.describe(root)}")
    _account_watcher = wechat_account.AccountWatcher(
        root=root, on_change=_rebuild_for_account)
    _account_watcher.start()
    return _account_watcher


# ============ 入口 ============


def main() -> int:
    # 从 config 初始化 state 中需要计算的值
    state._self_id_int = state._wxid_demouser10001(config.BOT_WXID or "wechat_bot")
    state._self_name = config.SELF_NAME

    # 初始化运行时可变的 FunASR 配置
    state.fun_asr_workspace_id = config.FUN_ASR_WORKSPACE_ID
    state.fun_asr_region = config.FUN_ASR_REGION
    state.fun_asr_api_key = config.FUN_ASR_API_KEY
    state.fun_asr_model = config.FUN_ASR_MODEL

    PID_FILE = os.path.join(config.data_dir(), "flandora.pid")

    # 判重交给内核对象；句柄存模块级变量，进程活着期间不能释放
    _instance_mutex, _already_running = acquire_mutex()
    if _already_running:
        log.error("已有一个 bridge 在运行")
        sys.exit(1)

    # pid 文件只作人工排查用（谁在跑、pid 是多少）
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # WebUI 实时日志转发（尽早挂上，捕获启动日志）
    try:
        from .webui import monitor
        monitor.attach_log_relay()
    except Exception:
        pass

    try:
        log.info("=" * 50)
        log.info(" Flandora 微信桥接 (OneBot v11)")
        log.info("=" * 50)
        log.info("Bridge 版本: %s", __version__)
        if config.CONFIG_GENERATED:
            log.info("首次启动：已按默认值生成 %s，请到 WebUI 里把示例值改成你的真实值", config.CONFIG_FILE)
        # 后台查一次新版，失败静默（见 update_check）
        update_check.check_async()
        start_account_watch()
        _start_bridge()
        if getattr(config, "WEBUI_AUTO_OPEN", True):
            threading.Thread(target=_auto_open_webui, daemon=True).start()
        start_web()
    except (Exception, SystemExit) as e:
        # 主线程靠 uvicorn 常驻。WebUI 起不来（端口被占、发布包缺少 webui 模块等）时
        # 不能让进程退出：OneBot 服务端跑在后台线程里，主线程一退，桥梁就断了。
        # 注意 uvicorn 绑定端口失败时走的是 sys.exit()，SystemExit 不是 Exception 的子类，
        # 必须显式接住。
        code = getattr(e, "code", None)
        if code is not None:
            log.error(f"[WebUI] 启动失败: uvicorn 中止进程（exit code {code}）")
        else:
            log.error(f"[WebUI] 启动失败: {type(e).__name__}: {e}")
        log.error(
            f"[WebUI] WebUI 不可用，但 OneBot 服务仍在监听 "
            f"{config.OB_SERVER_HOST}:{config.OB_SERVER_PORT}，MaiBot 侧不受影响"
        )
        log.error(
            f"[WebUI] 请检查端口 {config.WEB_PORT} 是否被占用；"
            f"若报缺少 webui 模块，说明发布包不完整，请用 build.py 从源码重新打包"
        )
        threading.Event().wait()
    finally:
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
