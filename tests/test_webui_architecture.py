"""S4 架构守卫：反向依赖、组合根、路由登记与窗口能力的单向依赖。

四条不许回退的边界：

1. 核心层（``flandora/`` 下除 ``main.py`` 与 ``webui/`` 之外的模块）不得 import
   表现层 ``flandora.webui``。事件总线在 ``flandora/runtime/events.py``，核心向它
   发布，面板从它订阅；再出现 ``from .webui import monitor`` 就是依赖方向又反了。
2. ``flandora/webui/app.py`` 只是组合根：不许写路由装饰器，端点都在
   ``flandora/webui/api/`` 的业务组里。
3. 路由总数钉死，且每个业务组登记的 ``ROUTES`` 必须与它自己注册的路由逐条一致、
   ``defer`` 必须写原因。新增/删除端点时这条会红，逼着人确认档位。
4. ``uia_sender`` 与 ``wx_desktop`` 互不 import：窗口识别落在
   ``flandora/win32/windows.py``，两边单向依赖它。

两种跑法等价：

    python -m pytest tests/test_webui_architecture.py -q
    python tests/test_webui_architecture.py
"""

import ast
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLANDORA_DIR = os.path.join(REPO_ROOT, "flandora")
WEBUI_DIR = os.path.join(FLANDORA_DIR, "webui")
API_DIR = os.path.join(WEBUI_DIR, "api")
APP_PATH = os.path.join(WEBUI_DIR, "app.py")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# 面板的路由总数：44 条 HTTP + 1 条 WebSocket。改动端点时先改这里，再改业务组。
ROUTE_TOTAL = 45

# 路由装饰器的属性名；@app.get / @router.post 都在此列，@app.middleware / @asynccontextmanager 不在
ROUTE_DECORATORS = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "head",
    "options",
    "websocket",
    "api_route",
}


def _parse(path: str) -> ast.Module:
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _is_webui_import(node: ast.AST) -> bool:
    """这个 import 节点是否指向 ``flandora.webui``（相对与绝对两种写法）。"""
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level:
            return module == "webui" or module.startswith("webui.")
        return module == "flandora.webui" or module.startswith("flandora.webui.")
    if isinstance(node, ast.Import):
        return any(
            alias.name == "flandora.webui" or alias.name.startswith("flandora.webui.")
            for alias in node.names
        )
    return False


def _core_modules_without_webui() -> list[str]:
    """核心层源码：``flandora/`` 下排除组合根 ``main.py`` 与 ``webui/`` 整棵子树。"""
    found = []
    for root, _dirs, files in os.walk(FLANDORA_DIR):
        if os.path.abspath(root) == os.path.abspath(WEBUI_DIR) or os.path.abspath(root).startswith(
            os.path.abspath(WEBUI_DIR) + os.sep
        ):
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            if os.path.abspath(path) == os.path.abspath(os.path.join(FLANDORA_DIR, "main.py")):
                continue
            found.append(path)
    return sorted(found)


def test_core_never_imports_webui():
    """依赖方向只有面板 → 核心；核心再回头 import 面板就是接线反了。"""
    offenders = {}
    for path in _core_modules_without_webui():
        tree = _parse(path)
        hits = [ast.unparse(node) for node in ast.walk(tree) if _is_webui_import(node)]
        if hits:
            rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
            offenders[rel] = hits
    assert offenders == {}, (
        f"核心层不得 import flandora.webui（事件放 runtime/events.py）：{offenders}"
    )


def test_app_has_no_route_decorators():
    """组合根不写端点：``app.py`` 里出现路由装饰器说明有人绕开了业务组。"""
    hits = []
    for node in ast.walk(_parse(APP_PATH)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr in ROUTE_DECORATORS:
                hits.append(f"{node.name}: @{ast.unparse(decorator)}")
    assert hits == [], f"端点请写进 flandora/webui/api/ 的业务组，不要在 app.py 里注册：{hits}"


def _flatten(routes):
    """展开 include 进来的路由器。

    新版 FastAPI 的 ``include_router`` 在 ``app.routes`` 里留 ``_IncludedRouter``
    引用，真正的路由对象挂在它引用的路由器上；旧版直接摊平。
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _flatten(included.routes)
        else:
            yield route


def test_route_total_is_pinned():
    """路由总数写死：新增或删除端点都要人来确认一次。"""
    from flandora.webui import app as webui_app

    routes = list(_flatten(webui_app.app.routes))
    api = [r for r in routes if getattr(r, "path", "").startswith("/api/")]
    websocket = [r for r in routes if getattr(r, "path", "") == "/ws"]

    assert len(api) + len(websocket) == ROUTE_TOTAL, (
        f"面板路由应为 {ROUTE_TOTAL} 条（/api 44 + /ws 1），"
        f"实际 /api {len(api)} + /ws {len(websocket)}"
    )


def test_api_modules_are_aggregated_and_own_their_routes():
    """业务组清单 == 目录里的模块；每组登记的路由 == 它自己注册的路由。"""
    from flandora.webui.api import ALL_ROUTES, ROUTER_MODULES

    on_disk = sorted(
        name[:-3] for name in os.listdir(API_DIR) if name.endswith(".py") and name != "__init__.py"
    )
    aggregated = sorted(module.__name__.rsplit(".", 1)[-1] for module in ROUTER_MODULES)
    assert aggregated == on_disk, f"api/ 下的模块必须都聚合进 ROUTER_MODULES：{on_disk}"

    for module in ROUTER_MODULES:
        registered: dict[str, set[str]] = {}
        for route in module.router.routes:
            for method in getattr(route, "methods", None) or []:
                registered.setdefault(route.path, set()).add(method)
        declared = {path: set(methods) for path, methods in module.ROUTES.items()}
        assert registered == declared, f"{module.__name__} 的 ROUTES 与注册路由不一致"

    # 聚合表与各组自报的并集一致（ALL_ROUTES 已按路径合并方法）
    merged = {path: set(methods) for path, methods in ALL_ROUTES().items()}
    expected: dict[str, set[str]] = {}
    for module in ROUTER_MODULES:
        for path, methods in module.ROUTES.items():
            expected.setdefault(path, set()).update(methods)
    assert merged == expected


def test_every_deferred_route_declares_a_reason():
    """``defer`` 不是「先跳过」，是「需要真机条件」——每条都要写清是什么条件。"""
    from flandora.webui.api import ALL_DEFER_REASONS, ALL_ROUTES

    deferred = {
        f"{method} {path}"
        for path, methods in ALL_ROUTES().items()
        for method, tier in methods.items()
        if tier == "defer"
    }
    reasons = ALL_DEFER_REASONS()
    assert set(reasons) == deferred, "defer 原因表与实际 defer 路由不一致"
    assert all(reason.strip() for reason in reasons.values()), "defer 原因不能为空"


def _imported_names(path: str) -> set[str]:
    """模块里 import 进来的名字（含相对导入拼出的完整路径）。"""
    names: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            prefix = "." * node.level + module if node.level else module
            names.add(prefix)
            names.update(f"{prefix}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("flandora/uia_sender.py", "wx_desktop"),
        ("flandora/wx_desktop.py", "uia_sender"),
    ],
)
def test_window_senders_do_not_import_each_other(left, right):
    """发送器与隔离桌面互相 import 会成环；窗口识别已下沉到 flandora/win32/windows.py。"""
    names = _imported_names(os.path.join(REPO_ROOT, left))
    offenders = sorted(name for name in names if name.split(".")[-1] == right)
    assert offenders == [], f"{left} 不得 import {right}：{offenders}"


def test_win32_windows_is_the_single_window_capability_home():
    """窗口枚举/闪烁/状态判定只有一份实现；两边都从这里取。"""
    from flandora.win32 import windows as win32_windows

    for name in ("enum_wechat_windows", "flash_window", "state_from_handle", "state_of"):
        assert callable(getattr(win32_windows, name)), f"win32.windows 缺 {name}"

    for path in ("flandora/uia_sender.py", "flandora/wx_desktop.py"):
        names = _imported_names(os.path.join(REPO_ROOT, path))
        assert any(name.lstrip(".") == "win32.windows" for name in names), (
            f"{path} 应从 flandora.win32.windows 取窗口能力"
        )


def main() -> int:
    """脚本直跑：委托给 pytest，保证两条入口同一套断言。"""
    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
