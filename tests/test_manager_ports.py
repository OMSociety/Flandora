"""验证：管理器按 data/config.json 里的端口走，不写死 7999/4950。

端口可以在面板里改，管理器却要显示「客户端连接数」、要打开 WebUI——照旧写死就会
盯错地方（审计 B-09）。这里分两头验：读配置的函数本身的行为，以及源码里不再出现
写死的端口字面量。

两种跑法等价：

    python -m pytest tests/test_manager_ports.py -q
    python tests/test_manager_ports.py
"""

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MANAGER_PATH = Path(REPO_ROOT) / "manager" / "bridge_manager.py"


def _load_manager():
    """按路径加载管理器模块（manager/ 不是包，没有 __init__.py）。

    import 期它会取一次管理器单实例锁：在测试进程里这一次取锁是无害的，
    「已有实例」的判定发生在 main()，不影响读端口的函数。
    """
    spec = importlib.util.spec_from_file_location("bridge_manager_under_test", MANAGER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path: Path, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (tmp_path / "config.json").write_text(text, encoding="utf-8")


def test_reads_ports_from_config(tmp_path, monkeypatch):
    """配置写了端口就用配置里的。"""
    module = _load_manager()
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    _write_config(tmp_path, {"ob_server_port": 8001, "web_port": 5001})

    assert module.read_config_ports() == (8001, 5001)


def test_falls_back_to_defaults(tmp_path, monkeypatch):
    """文件不在、不是 JSON、值不是合法端口：一律回默认值。"""
    module = _load_manager()
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    defaults = (module.DEFAULT_OB_PORT, module.DEFAULT_WEB_PORT)

    assert module.read_config_ports() == defaults  # 文件不存在

    _write_config(tmp_path, "{ 这不是 JSON")
    assert module.read_config_ports() == defaults

    _write_config(tmp_path, ["不是字典"])
    assert module.read_config_ports() == defaults

    # 字符串、布尔、越界都不是端口：不许 int("8001")，也不许把 True 当 1
    _write_config(tmp_path, {"ob_server_port": "8001", "web_port": True})
    assert module.read_config_ports() == defaults

    _write_config(tmp_path, {"ob_server_port": 0, "web_port": 70000})
    assert module.read_config_ports() == defaults


def test_controller_takes_ports_from_config(tmp_path, monkeypatch):
    """控制器构造时就把端口读进来：查监听、数连接数、开 WebUI 都按它走。"""
    module = _load_manager()
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    _write_config(tmp_path, {"ob_server_port": 8123, "web_port": 5123})

    controller = module.BridgeController()

    assert (controller.ob_port, controller.web_port) == (8123, 5123)


def test_port_literals_stay_in_the_defaults():
    """源码里只该在 DEFAULT_* 上出现 7999/4950，调用点必须用 self.ob_port / self.web_port。"""
    src = MANAGER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    named: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            value = node.value.value
            if value in (7999, 4950):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        named[target.id] = value
    assert named == {"DEFAULT_OB_PORT": 7999, "DEFAULT_WEB_PORT": 4950}, named

    # 审计点名的三处写死位置：前两处直接查字面量，第三处（打开 WebUI）查它按端口拼
    for hardcoded in ("_find_listening_pid(7999)", "-LocalPort 7999"):
        assert hardcoded not in src, hardcoded
    assert "http://127.0.0.1:{self.controller.web_port}" in src
    assert "read_config_ports" in src


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
