"""
入口与生命周期管理模块。

负责桥接的启动、停止、主循环重连逻辑，以及命令行入口。
"""

import logging
import os
import sys
import threading
import time

from . import __version__, config, ob_ids, ob_network, stop_request, update_check, wechat_account
from .bridge_core import Flandora
from .ob_server import ONEBOT_SERVER
from .runtime import LIFECYCLE, RUNTIME, events
from .senders import create_sender
from .single_instance import BRIDGE_MUTEX_NAME, acquire_mutex

log = logging.getLogger("flandora")


# ============ 启动 / 停止 ============


def start_bridge():
    if not LIFECYCLE.begin_start():
        return

    # 先把 OB11 服务端拉起来——发消息可能依赖微信窗口搜索，先保证能收消息。
    # 已在跑时 start() 自己返回 False，这里不需要另设标志。
    ONEBOT_SERVER.start()

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
                    log.error("[Web] 隔离桌面启动失败：%s", mgr.status().get("error", ""))
        except Exception as exc:  # noqa: BLE001
            log.error(f"隔离桌面启动异常: {exc}")
        try:
            RUNTIME.sender = create_sender()
        except Exception as exc:  # noqa: BLE001
            log.error(f"创建 UIA 发送器失败: {exc}")

    threading.Thread(target=_create_sender_async, daemon=True, name="sender-init").start()

    thread = threading.Thread(target=_bridge_loop, daemon=True, name="bridge")
    LIFECYCLE.attach_thread(thread)
    thread.start()
    log.info("[Web] 已启动")


def stop_bridge():
    was_active = LIFECYCLE.begin_stop()

    # 先等收件循环退出，再关 OneBot 入口：反过来的话，收件线程还会往已停的网络推事件
    if was_active and not LIFECYCLE.join(timeout=5.0):
        log.warning("[Web] 桥接线程 5 秒内未退出，继续关闭 OneBot 入口")
    ONEBOT_SERVER.stop()
    LIFECYCLE.mark_stopped()
    log.info("[Web] 已停止")


def watch_stop_signal(shutdown: threading.Event, pid_file: str | None = None):
    """等管理器的停止请求，收到就收尾退出（收件、OneBot、隔离桌面、面板）。

    ``shutdown`` 由 main() 持有：面板起不来时主线程正等它，收尾完了才放行。
    收尾全部做完后直接收进程：UIA/COM 在冻结形态下留着不走解释器清理的线程，
    交给正常退出会让管理器多等满 10 秒再强杀——收尾都完成了，强杀没有意义。
    """
    if not stop_request.wait():
        return
    log.info("[停止] 收到管理器的停止请求，开始收尾")
    try:
        stop_bridge()
    except Exception as exc:  # noqa: BLE001
        log.error(f"[停止] 停止桥接时出错：{exc}")
    try:
        from . import wx_desktop

        wx_desktop.manager().stop()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[停止] 关闭隔离桌面时出错：{exc}")
    try:
        from .webui.app import request_shutdown

        request_shutdown()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[停止] 关闭面板时出错：{exc}")
    log.info("[停止] 已收尾，进程退出")
    shutdown.set()
    if pid_file:
        try:
            os.remove(pid_file)
        except OSError:
            pass
    logging.shutdown()
    os._exit(0)


def _bridge_loop():
    import ctypes

    ctypes.windll.ole32.CoInitialize(None)

    log.info(
        f"Bridge | 收件: local | OB11: ws://{config.OB_SERVER_HOST}:{config.OB_SERVER_PORT} | 发送: uia"
    )

    LIFECYCLE.mark_running()
    bridge = Flandora(RUNTIME.sender)
    RUNTIME.bridge = bridge

    # 本地库收件自己轮询、自己重试，起不来时（还没登录、口令没取到）
    # 隔一会儿再试，避免把失败当成功直接退出。
    try:
        while LIFECYCLE.should_continue():
            try:
                bridge.listen_local_db()
            except Exception as e:  # noqa: BLE001
                log.error(f"本地库收件异常：{e}")
            if not LIFECYCLE.should_continue():
                break
            log.warning("本地库收件未就绪，10s 后重试")
            for _ in range(10):
                if not LIFECYCLE.should_continue():
                    break
                time.sleep(1)
    finally:
        RUNTIME.bridge = None


def start_web():
    """起面板。地址与 HTTPS 都在这里定，拦下来的两种情况也只在这里报。"""
    from .webui import auth
    from .webui.app import run as run_webui

    if config.WEB_BIND_HOST == "0.0.0.0" and not auth.enabled():
        # 手里有 config.json 就能改回来，所以这里宁可不起也不能裸奔。
        log.error("[WebUI] 面板绑在所有网卡上却没设登录密码，拒绝启动（局域网里等于把面板公开）")
        log.error("[WebUI] 先在面板里设密码，或把 config.json 的 web_bind_host 改回 127.0.0.1")
        return False
    problem = config.webui_tls_problem()
    if problem:
        # 不静默降级成 HTTP：那样界面写着 HTTPS，实际明文。
        log.error(f"[WebUI] 已启用 HTTPS 但证书不可用：{problem}")
        log.error("[WebUI] 修正证书路径，或把 config.json 的 webui_tls 改成 false 后重启")
        return False
    if config.WEB_BIND_HOST_ERROR:
        log.warning(f"[WebUI] {config.WEB_BIND_HOST_ERROR}")
    scheme = "https" if config.WEBUI_TLS else "http"
    scope = "监听所有网卡" if config.WEB_BIND_HOST == "0.0.0.0" else "仅本机"
    log.info(f"WebUI: {scheme}://{config.WEB_BIND_HOST}:{config.WEB_PORT}（{scope}）")
    run_webui(config.WEB_BIND_HOST, config.WEB_PORT)
    return True


# ============ 账号切换兜底 ============

_account_watcher = None


def _rebuild_for_account(old, new):
    """作废并重建只对「某个登录账号」成立的状态。

    密钥、联系人映射、机器人身份都绑定在登录账号上。切换账号后继续沿用旧值，
    消息会从错的身份发给错的会话，因此这里刻意清空映射——下游拿不到映射会
    如实报发送失败（ob_protocol 返回真实结果），而不是退回上一个账号的
    联系人。
    """
    old_wxid = old.wxid if old else "（未识别）"
    ob_ids.clear_names()
    ob_ids.SELF.set_wxid(new.wxid)  # user_id 重新分配，昵称缓存同时作废
    # 反向 WS 的 X-Self-ID 是握手那一刻定下的：不重连就还顶着上一个账号的身份
    ob_network.manager().refresh_identity()
    log.warning("=" * 64)
    log.warning(f"[账号] 微信登录账号已切换：{old_wxid} -> {new.wxid}")
    log.warning(f"[账号] 新账号数据目录：{new.data_dir or '（未找到）'}")
    log.warning("[账号] 已作废 OneBot 会话映射（联系人 / 群名缓存）")
    log.warning(f"[账号] 机器人身份已切换为 {new.wxid}（self_id={ob_ids.SELF.user_id()}）")
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
    _account_watcher = wechat_account.AccountWatcher(root=root, on_change=_rebuild_for_account)
    _account_watcher.start()
    return _account_watcher


# ============ 入口 ============


def main() -> int:
    # 进主流程先加载配置并装配日志：从这里往后读到的 config.* 都是校验过的值，
    # 回退告警也在这里打出来（导入期不碰磁盘）
    config.bootstrap()
    # 机器人身份全自动：取当前登录的微信账号，无需任何配置。
    # 检测不到（微信没登录/没运行）时用 wechat_bot 占位，微信登录后重启桥接即自动识别。
    acct = wechat_account.current_account(config.WECHAT_FILES_ROOT or None)
    ob_ids.SELF.set_wxid(acct.wxid if acct is not None else "wechat_bot")
    if acct is not None:
        log.info(f"[身份] 机器人账号：{acct.wxid}")
    else:
        log.warning("[身份] 未检测到登录的微信账号，机器人身份暂用 wechat_bot 占位")

    PID_FILE = os.path.join(config.data_dir(), "flandora.pid")

    # 判重交给内核对象；句柄存模块级变量，进程活着期间不能释放
    _instance_mutex, _already_running = acquire_mutex(BRIDGE_MUTEX_NAME)
    if _already_running:
        log.error("已有一个 bridge 在运行")
        sys.exit(1)

    # 上一个桥接留下的停止请求不能带到这一条命里，否则一起来就自己退出
    stop_request.clear()

    # 管理器要停桥接时置位这个事件，后台线程等到它就收尾退出
    shutdown = threading.Event()

    # pid 文件只作人工排查用（谁在跑、pid 是多少）
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # 面板实时日志转发（尽早挂上，捕获启动日志）
    events.attach_log_relay()

    try:
        log.info("=" * 50)
        log.info(" Flandora 微信桥接 (OneBot v11)")
        log.info("=" * 50)
        log.info("Bridge 版本: %s", __version__)
        if config.CONFIG_GENERATED:
            log.info(
                "首次启动：已按默认值生成 %s，请到 WebUI 里把示例值改成你的真实值",
                config.CONFIG_FILE,
            )
        # 后台查一次新版，失败静默（见 update_check）
        update_check.check_async()
        start_account_watch()
        start_bridge()
        threading.Thread(
            target=watch_stop_signal,
            args=(shutdown, PID_FILE),
            daemon=True,
            name="stop-watch",
        ).start()
        if start_web() is False:
            # 面板被安全闸拦下来了：原因已写进日志，但 OneBot 服务得继续跑。
            shutdown.wait()
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
            f"{config.OB_SERVER_HOST}:{config.OB_SERVER_PORT}，已连接的客户端不受影响"
        )
        log.error(
            f"[WebUI] 请检查端口 {config.WEB_PORT} 是否被占用；"
            f"若报缺少 webui 模块，说明发布包不完整，请用 build.py 从源码重新打包"
        )
        shutdown.wait()
    finally:
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
