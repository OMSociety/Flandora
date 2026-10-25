"""源码形状闸：一个类里不能有两个同名方法。

后一个定义会静默覆盖前一个，看起来「定义了」其实没生效。2026-10-26 在
`flandora/uia_sender.py` 上踩过一次：新加的类方法 `_window_alive(hwnd)` 盖掉了同名的
实例方法 `_window_alive(self)`，真机发送立刻报「missing 1 required positional
argument: 'hwnd'」。

property 的 setter / deleter 与 getter 同名是正常写法，靠装饰器区分，不算重名。
"""

import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

PACKAGES = ("flandora", "manager", "sdk", "mcp", "tools")
SKIP_DIRS = {"__pycache__", ".venv", "dist", "build"}
PROPERTY_HOOKS = {"setter", "deleter", "getter"}


def _decorator_names(node: ast.AST) -> set[str]:
    names = set()
    for dec in node.decorator_list:
        if isinstance(dec, ast.Attribute):
            names.add(dec.attr)
        elif isinstance(dec, ast.Name):
            names.add(dec.id)
    return names


def _sources():
    for package in PACKAGES:
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, package)):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def test_no_class_defines_the_same_method_twice():
    offenders: list[str] = []
    for path in _sources():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            seen: dict[str, int] = {}
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _decorator_names(item) & PROPERTY_HOOKS:
                    continue
                if item.name in seen:
                    rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
                    offenders.append(
                        f"{rel}:{node.name}.{item.name} 第 {seen[item.name]} 行与第 {item.lineno} 行"
                    )
                seen[item.name] = item.lineno
    assert not offenders, "同一个类里定义了两次同名方法（后一个会静默覆盖前一个）：\n" + "\n".join(
        offenders
    )
