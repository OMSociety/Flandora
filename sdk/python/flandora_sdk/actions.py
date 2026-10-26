"""动作目录的只读视图。

快照由 `tools/gen_sdk_catalog.py` 从 `flandora/action_catalog.py` 生成；这里只读它，
所以不依赖 frida / uiautomation 这些只在桥接机上装得起来的包。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from .errors import ActionNotFound

_CATALOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_catalog.json")


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    with open(_CATALOG_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def categories() -> list[str]:
    return list(catalog()["categories"])


def list_actions(category: str | None = None) -> list[dict[str, Any]]:
    actions = catalog()["actions"]
    if category:
        return [a for a in actions if a["category"] == category]
    return list(actions)


def get_action(name: str) -> dict[str, Any]:
    for action in catalog()["actions"]:
        if action["name"] == name:
            return action
    raise ActionNotFound(name)


def implemented_actions() -> list[dict[str, Any]]:
    return [a for a in catalog()["actions"] if a["implemented"]]
