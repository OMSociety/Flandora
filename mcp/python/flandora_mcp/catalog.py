"""动作目录快照的读取侧。

快照由仓库根的 `tools/gen_sdk_catalog.py` 从 `flandora/action_catalog.py` 生成：
MCP 服务要能在没装 Flandora 运行时的机器上跑（装上 frida / uiautomation 才 import
得动桥接本体），所以只能读这份 JSON。改动动作之后重新生成一次，`tests/test_mcp_catalog.py`
会核对它与服务端目录一致。
"""

import json
import os
from typing import Any

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_catalog.json")

with open(_PATH, encoding="utf-8") as _fh:
    _DATA = json.load(_fh)

CATEGORIES: list[str] = list(_DATA.get("categories") or [])
ACTIONS: list[dict[str, Any]] = list(_DATA.get("actions") or [])

_BY_NAME: dict[str, dict[str, Any]] = {a["name"]: a for a in ACTIONS}


def get(name: str) -> dict[str, Any] | None:
    """按名字取动作。"""
    return _BY_NAME.get(name)


def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """在名字、摘要、分类里找子串，按「名字命中」优先排。"""
    needle = (query or "").strip().lower()
    if not needle:
        return []
    hits: list[tuple[int, dict[str, Any]]] = []
    for action in ACTIONS:
        name = action["name"].lower()
        summary = (action.get("summary") or "").lower()
        category = (action.get("category") or "").lower()
        if needle in name:
            hits.append((0, action))
        elif needle in summary:
            hits.append((2, action))
        elif needle in category:
            hits.append((3, action))
    hits.sort(key=lambda pair: (pair[0], pair[1]["name"]))
    return [action for _, action in hits[: max(1, limit)]]


def brief(action: dict[str, Any]) -> dict[str, Any]:
    """给 MCP 返回用的精简形状：省掉 inputSchema 这类大块。"""
    return {
        "name": action["name"],
        "category": action.get("category", ""),
        "summary": action.get("summary", ""),
        "readOnly": bool(action.get("readOnly")),
        "implemented": bool(action.get("implemented")),
        "wip": action.get("wip", ""),
        "params": action.get("params") or [],
    }
