"""面板 API 路由器聚合：一个业务组一个模块，各自带 ``router`` 与 ``ROUTES``。

``ROUTES`` 是冒烟用的档位表：``call`` = 无不可逆副作用、冒烟里真打一次；
``defer`` = 需要真机条件（真微信、真桌面、外网或会删真实数据），必须在该模块的
``DEFER_REASONS`` 里写明原因。

新增端点的完整动作：处理函数写进对应的组 → 在该组 ``ROUTES`` 登一档 →
``tests/test_webui_endpoints.py`` 与 ``tests/test_webui_architecture.py`` 会核对
「注册的路由 == 登记的路由」与「每条 defer 都有原因」，漏了就红。
"""

from fastapi import APIRouter

from . import (
    auth,
    autostart,
    config,
    contacts,
    control,
    debug,
    desktop,
    network,
    storage,
    update,
    windows,
    wizard,
)

# 组合根按这个顺序 include；也是守卫测试核对「有没有漏聚合」的依据。
ROUTER_MODULES = (
    auth,
    autostart,
    config,
    contacts,
    control,
    debug,
    desktop,
    network,
    storage,
    update,
    windows,
    wizard,
)

ALL_ROUTERS: tuple[APIRouter, ...] = tuple(module.router for module in ROUTER_MODULES)


def ALL_ROUTES() -> dict[str, dict[str, str]]:
    """合并各组的档位表。

    同一路径同一方法被两组登记时直接失败：那说明两条路由抢一个入口，冒烟会
    只测到其中一条。
    """
    merged: dict[str, dict[str, str]] = {}
    for module in ROUTER_MODULES:
        for path, methods in module.ROUTES.items():
            bucket = merged.setdefault(path, {})
            for method, tier in methods.items():
                if method in bucket:
                    raise ValueError(f"路由重复登记：{method} {path}")
                bucket[method] = tier
    return merged


def ALL_DEFER_REASONS() -> dict[str, str]:
    """合并各组的 defer 原因表（键是 ``METHOD 路径``），供守卫测试核对。"""
    merged: dict[str, str] = {}
    for module in ROUTER_MODULES:
        for key, reason in getattr(module, "DEFER_REASONS", {}).items():
            if key in merged:
                raise ValueError(f"defer 原因重复登记：{key}")
            merged[key] = reason
    return merged


__all__ = ["ALL_DEFER_REASONS", "ALL_ROUTES", "ALL_ROUTERS", "ROUTER_MODULES"]
