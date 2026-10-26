"""验证：管理器按 data/config.json 里的端口走，不写死 7999/4950。

端口可以在面板里改，而管理器要靠它判断桥接在不在跑、要打开哪个地址的网页面板：
写死就会盯错地方。这里分两头验：读配置的函数本身的行为，以及源码里不出现写死的
端口字面量。

两种跑法等价：

    python -m pytest tests/test_manager_ports.py -q
    python tests/test_manager_ports.py
"""

import ast
import json
import os
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MANAGER_DIR = Path(REPO_ROOT) / "manager"
# manager/ 不是包（没有 __init__.py）：管理器自己也是这么 import 的，
# 测试照同一条路子来，才验得到真实的模块关系。
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))

import bridge  # noqa: E402
import paths  # noqa: E402


def _write_config(config: Path, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    config.write_text(text, encoding="utf-8")


def test_reads_ports_from_config(tmp_path, monkeypatch):
    """配置写了端口就用配置里的。"""
    config = tmp_path / "config.json"
    monkeypatch.setattr(paths, "CONFIG_FILE", config)
    _write_config(config, {"ob_server_port": 8001, "web_port": 5001})

    assert paths.read_config_ports() == (8001, 5001)


def test_falls_back_to_defaults(tmp_path, monkeypatch):
    """文件不在、不是 JSON、值不是合法端口：一律回默认值。"""
    config = tmp_path / "config.json"
    monkeypatch.setattr(paths, "CONFIG_FILE", config)
    defaults = (paths.DEFAULT_OB_PORT, paths.DEFAULT_WEB_PORT)

    assert paths.read_config_ports() == defaults  # 文件不存在

    _write_config(config, "{ 这不是 JSON")
    assert paths.read_config_ports() == defaults

    _write_config(config, ["不是字典"])
    assert paths.read_config_ports() == defaults

    # 字符串、布尔、越界都不是端口：不许 int("8001")，也不许把 True 当 1
    _write_config(config, {"ob_server_port": "8001", "web_port": True})
    assert paths.read_config_ports() == defaults

    _write_config(config, {"ob_server_port": 0, "web_port": 70000})
    assert paths.read_config_ports() == defaults


def test_bridge_takes_ports_from_config(tmp_path, monkeypatch):
    """桥接进程对象构造时就把端口读进来：查监听、找 PID 都按它走。"""
    config = tmp_path / "config.json"
    monkeypatch.setattr(paths, "CONFIG_FILE", config)
    _write_config(config, {"ob_server_port": 8123, "web_port": 5123})

    proc = bridge.BridgeProcess()

    assert (proc.ob_port, proc.web_port) == (8123, 5123)


def test_port_literals_stay_in_the_defaults():
    """7999/4950 只该出现在 paths.py 的 DEFAULT_* 上，别处一律用读回来的端口。"""
    named: dict[str, int] = {}
    for path in sorted(MANAGER_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                value = node.value.value
                if value in (7999, 4950):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            named[f"{path.name}:{target.id}"] = value
    assert named == {
        "paths.py:DEFAULT_OB_PORT": 7999,
        "paths.py:DEFAULT_WEB_PORT": 4950,
    }, named

    bridge_src = (MANAGER_DIR / "bridge.py").read_text(encoding="utf-8")
    assert "read_config_ports" in bridge_src
    # 打开网页面板要按读回来的端口拼地址，不能写死默认端口
    tray_src = (MANAGER_DIR / "tray.py").read_text(encoding="utf-8")
    assert "127.0.0.1" in tray_src
    for hardcoded in ("7999", "4950"):
        assert hardcoded not in tray_src, hardcoded


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
