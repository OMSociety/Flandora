"""FastAPI 组合根：建应用、装鉴权中间件、挂静态前端、聚合各业务路由器。

端点清单不在这里维护：一个业务组一个模块，放在 ``flandora/webui/api/``，每组自带
``router`` 与 ``ROUTES`` 档位表。要看全部端点用 ``flandora.webui.api.ALL_ROUTES()``。

uvicorn 与 ``TestClient`` 都按模块级 ``app`` 取应用；``create_app()`` 给测试或
换一套挂载时用。新增端点不许把 ``@app.get`` 之类的装饰器写回本模块——路由装饰器
只出现在 ``api/`` 下的业务组里，守卫测试扫本文件的 AST。
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, config
from ..runtime import events
from . import auth, deps, monitor
from .api import ALL_ROUTERS

log = logging.getLogger("flandora")

VERSION = __version__
WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(WEBUI_DIR, "dist")


@asynccontextmanager
async def lifespan(_app):
    """启动时把事件循环交给核心侧总线，并挂上日志中继；退出时摘掉循环。"""
    events.EVENTS.set_loop(asyncio.get_running_loop())
    events.attach_log_relay()
    log.info(f"[WebUI] FastAPI 面板已就绪 (v{VERSION})")
    yield
    events.EVENTS.set_loop(None)


async def _require_login(request: Request, call_next):
    """开着密码时，除登录接口外所有 /api 都要会话；静态前端不挡，登录页才加载得出来。"""
    if request.url.path.startswith("/api/") and not auth.is_public_api(request.url.path):
        if auth.enabled() and not auth.sessions.valid(request.cookies.get(auth.COOKIE_NAME)):
            return JSONResponse({"ok": False, "error": "未登录", "auth_required": True}, 401)
    return await call_next(request)


def _no_build():
    """前端没构建时的兜底：静态目录不存在就直说怎么构建，不返回空白页。"""
    return JSONResponse(
        {
            "ok": False,
            "error": "前端尚未构建。请在 webui-frontend 目录执行 npm install && npm run build",
        },
        503,
    )


async def _validation_error(_request: Request, exc: RequestValidationError):
    """请求形状不对也回 ``{ok:false,error}``：全站错误只有这一种口径。"""
    errors = exc.errors()
    loc = ""
    if errors:
        parts = [str(part) for part in errors[0].get("loc", ()) if part != "body"]
        loc = ".".join(parts)
    where = f"：{loc}" if loc else ""
    return JSONResponse({"ok": False, "error": f"请求参数不正确{where}"}, 422)


def create_app() -> FastAPI:
    """按固定顺序装好中间件、业务路由与静态前端，返回可跑的应用。"""
    app = FastAPI(
        title="Flandora WebUI",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.middleware("http")(_require_login)
    app.add_exception_handler(RequestValidationError, _validation_error)
    for router in ALL_ROUTERS:
        app.include_router(router)
    app.add_api_websocket_route("/ws", monitor.ws_endpoint)
    if os.path.isdir(DIST_DIR):
        app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="webui")
    else:
        app.add_api_route("/", _no_build, methods=["GET"])
    return app


app = create_app()

# HOT 字段写完要动进程状态（微信窗口换绑）：订阅挂在组合根，进程内只挂一次。
config.subscribe(deps.on_config_changed)


def run(host: str, port: int):
    """由 main.py 调用，在主线程阻塞运行 uvicorn，直到 :func:`request_shutdown`。"""
    import uvicorn

    tls: dict = {}
    if config.WEBUI_TLS:
        # 证书不能加载时 main.py 已经拦下来了，不会走到这里
        tls = {
            "ssl_certfile": config.WEBUI_TLS_CERT_FILE,
            "ssl_keyfile": config.WEBUI_TLS_KEY_FILE,
        }
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            # 管理器用 pythonw 拉起桥接时没有标准输出，uvicorn 默认的彩色格式化器
            # 会在 sys.stdout 为 None 时抛异常；日志已经由 flandora 自己的 handler 写文件。
            log_config=None,
            **tls,
        )
    )
    # 停止请求线程拿不到 run() 的局部变量，server 挂在应用上让它够得着
    app.state.uvicorn_server = server
    try:
        server.run()
    finally:
        app.state.uvicorn_server = None


def request_shutdown() -> bool:
    """请 uvicorn 走正常退出流程（收尾线程调用）。返回是否找到了运行中的 server。"""
    server = getattr(app.state, "uvicorn_server", None)
    if server is None:
        return False
    server.should_exit = True
    return True
