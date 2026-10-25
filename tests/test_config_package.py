"""配置包：导入零副作用、单一校验、写路径收敛与临时切换。

这里守住的是重构后最容易回退的几条约束：

* 导入 ``flandora.config`` 不读盘、不建目录、不装 handler、不拉网络层；
* 文件加载与面板保存共用 ``fields.clean_field()``，差别只在「回退」还是「拒绝」；
* ``ConfigStore.update()`` 只把 HOT 字段当成即时生效（通知订阅者），其余进
  ``restart_required``；
* ``config.temporary()`` 进出必须把 store 与现场还回去。

两种跑法等价：

    python -m pytest tests/test_config_package.py -q
    python tests/test_config_package.py
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import config  # noqa: E402
from flandora.config import fields  # noqa: E402

# HOT 字段就是这几项：改完立刻生效，不需要重启桥接
HOT_KEYS = {
    "wechat_hwnd",
    "group_name_map",
    "send_key",
    "webui_password",
    "log_max_mb",
    "log_backup_count",
    "allow_foreground_steal",
    "wechat_files_root",
}


def test_importing_the_package_touches_nothing(tmp_path):
    """子进程里干净导入：不建目录、不读 config.json、不装 handler、不 import 网络层。"""
    data_dir = tmp_path / "data"
    env = dict(os.environ)
    env["FLANDORA_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = os.pathsep.join([REPO_ROOT, env.get("PYTHONPATH", "")]).strip(os.pathsep)
    code = (
        "import logging, os, sys\n"
        "import flandora.config\n"
        "assert 'flandora.ob_network' not in sys.modules, 'ob_network 被拉起来了'\n"
        "assert logging.getLogger().handlers == [], '导入期就装了 handler'\n"
        "assert not os.path.exists(os.environ['FLANDORA_DATA_DIR']), '导入期就建了数据目录'\n"
        "print('clean')\n"
    )

    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=120,
    )

    assert out.returncode == 0, out.stderr
    assert "clean" in out.stdout
    assert not data_dir.exists()


def test_loose_validation_falls_back_and_collects_warnings(tmp_path):
    """文件加载：非法值按既定规则回退，原因收进 Settings.warnings。"""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"web_bind_host": "10.0.0.1", "log_max_mb": 0, "ob_heartbeat_seconds": 3}),
        encoding="utf-8",
    )

    with config.temporary(path):
        assert config.WEB_BIND_HOST == "127.0.0.1"
        assert config.WEB_BIND_HOST_ERROR.startswith("web_bind_host='10.0.0.1'")
        assert config.LOG_MAX_MB == 8
        assert config.OB_HEARTBEAT_SECONDS == 30
        assert len(config.warnings) == 3


def test_strict_validation_refuses_the_same_values():
    """面板保存：同一份规则，非法值一律拒绝并给出人读的原因。"""
    cases = [
        ("web_bind_host", "10.0.0.1", "web_bind_host 只能是 127.0.0.1 或 0.0.0.0"),
        ("ob_message_format", "json", "ob_message_format 只能是 array 或 string"),
        ("send_key", "alt_enter", "send_key 只能是 auto、enter 或 button"),
        ("ob_heartbeat_seconds", 3, "ob_heartbeat_seconds 取 0（不发心跳）或 5–3600 秒"),
        ("log_max_mb", 2048, "日志单份上限取 1–1024（MB）"),
        ("log_backup_count", 99, "日志保留份数取 1–20"),
    ]

    for key, value, message in cases:
        with pytest.raises(config.ConfigError) as caught:
            fields.clean_field(key, value, True)
        assert str(caught.value) == message


def test_field_table_declares_the_strategy():
    """HOT / SECRET 由字段表说话，面板不再各抄一份。"""
    assert set(fields.hot_keys()) == HOT_KEYS
    assert set(fields.secret_keys()) == {"webui_password", "ob_server_token"}
    for key in HOT_KEYS:
        assert not fields.is_restart(key), f"{key} 既 HOT 又 RESTART"
    assert fields.is_restart("web_port")
    assert fields.is_restart("webui_tls")


def test_update_only_applies_hot_keys(tmp_path):
    """只有 HOT 字段会通知订阅者；RESTART 字段落盘但不即时生效。"""
    with config.temporary(tmp_path / "config.json") as store:
        seen = []
        store.subscribe(lambda settings, changed: seen.append(changed))

        result = store.update({"log_max_mb": 4, "wechat_exe": "C:/Weixin/Weixin.exe", "custom": 1})

        assert result.settings.LOG_MAX_MB == 4
        assert seen == [frozenset({"log_max_mb"})]
        # 未声明的键不参与配置，也不写进文件
        assert store.read_raw() == {
            "log_max_mb": 4,
            "wechat_exe": "C:/Weixin/Weixin.exe",
        }
        assert result.restart_required == ("wechat_exe",)


def test_restart_required_is_derived_from_the_kind(tmp_path):
    with config.temporary(tmp_path / "config.json") as store:
        result = store.update(
            {
                "web_port": 5000,
                "ob_heartbeat_seconds": 0,
                "group_name_map": {"a@chatroom": "新群名"},
            }
        )

    assert result.restart_required == ("web_port", "ob_heartbeat_seconds")


def test_strict_update_leaves_the_file_alone(tmp_path):
    """严格校验失败不能把半份配置写进文件。"""
    with config.temporary(tmp_path / "config.json") as store:
        with pytest.raises(config.ConfigError):
            store.update({"web_port": 5000, "web_bind_host": "10.0.0.1"})

        assert store.read_raw() == {}
        assert config.WEB_PORT == 4950


def test_update_only_holds_submitted_keys_to_strict_validation(tmp_path):
    """盘上原有的坏值不挡对别的键的保存：严格只针对这次提交的键。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"log_max_mb": 0}), encoding="utf-8")

    with config.temporary(path) as store:
        result = store.update({"web_port": 5001})

        assert result.settings.WEB_PORT == 5001
        # 坏值按回退规则生效，但文件里仍是用户写的原样（面板要如实显示）
        assert config.LOG_MAX_MB == 8
        assert any("log_max_mb" in message for message in result.settings.warnings)
        assert store.read_raw()["log_max_mb"] == 0


def test_written_file_is_readable_and_newline_terminated(tmp_path):
    path = tmp_path / "config.json"

    with config.temporary(path) as store:
        store.update({"group_name_map": {"a@chatroom": "群名"}})

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "群名" in text  # ensure_ascii=False：中文照原样落盘
    assert json.loads(text) == {"group_name_map": {"a@chatroom": "群名"}}


def test_temporary_restores_the_previous_store(tmp_path):
    before = config.get_store()
    before_port = config.WEB_PORT

    with config.temporary(tmp_path / "config.json", overrides={"web_port": 5001}) as store:
        assert config.get_store() is store
        assert config.WEB_PORT == 5001
        store.update({"web_port": 5002})
        assert config.WEB_PORT == 5002

    assert config.get_store() is before
    assert config.WEB_PORT == before_port


def test_flag_import_and_its_module_attribute(tmp_path):
    """``config.LOG_HANDLER`` 之类派生值仍按模块属性取，外部引用不必改。"""
    with config.temporary(tmp_path / "config.json"):
        assert config.CONFIG_FILE == str(tmp_path / "config.json")
        assert config.LOG_FILE.endswith("flandora.log")
        assert config.LOG_HANDLER is not None
        assert config.LOG_FILE.startswith(os.path.dirname(config.CONFIG_FILE))


def test_first_run_generates_config_from_example(tmp_path):
    """正式 store 首次加载按示例生成 config.json；临时 store 不生成。"""
    path = tmp_path / "config.json"
    store = config.ConfigStore(str(path))

    assert store.generated is True
    assert path.is_file()
    generated = json.loads(path.read_text(encoding="utf-8"))
    assert set(generated) == set(fields.KEYS)
    assert generated["web_port"] == 4950
    assert [key for key in generated if key.startswith("_comment")] == []

    temp_path = tmp_path / "temp.json"
    with config.temporary(temp_path) as temp_store:
        assert temp_store.generated is False
    assert not temp_path.exists()


def test_bootstrap_reports_fallbacks(tmp_path, caplog):
    """bootstrap() 把文件加载期的回退原因打进日志。"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"web_bind_host": "10.0.0.1"}), encoding="utf-8")

    with config.temporary(path):
        with caplog.at_level(logging.WARNING, logger="flandora"):
            settings = config.bootstrap()

    assert settings.WEB_BIND_HOST == "127.0.0.1"
    assert any("web_bind_host" in record.getMessage() for record in caplog.records)


def test_field_patch_lives_only_inside_the_temporary(tmp_path):
    """字段级补丁只生效于当前临时配置，且不会在模块字典里留死值。"""
    with config.temporary(tmp_path / "config.json", overrides={"web_port": 5001}):
        assert config.WEB_PORT == 5001
        config.WEB_PORT = 1234
        assert config.WEB_PORT == 1234
        assert "WEB_PORT" not in vars(config)

    assert config.WEB_PORT == config.get_store().settings.WEB_PORT


def main() -> int:
    """直接跑：``python tests/test_config_package.py``。"""
    return int(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    sys.exit(main())
