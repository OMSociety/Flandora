"""配置字段说明与示例生成物的守卫。

三条约束：

* ``fields.schema()`` 覆盖全部字段、没有重复，``desc`` 是可用的说明；
* ``write_json()`` 只写声明过的键、按声明顺序落盘（配置里没有 ``_comment*``）；
* 旧配置文件里的 ``_comment*`` 与未知键照常加载，只是不再回写。

两种跑法等价：

    python -m pytest tests/test_config_schema.py -q
    python tests/test_config_schema.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora.config import fields  # noqa: E402
from flandora.config.store import ConfigStore, write_json  # noqa: E402

EXAMPLE_PATH = os.path.join(REPO_ROOT, "flandora", "config.example.json")
GENERATOR = os.path.join(REPO_ROOT, "tools", "gen_config_example.py")


def test_schema_covers_every_declared_field_in_order():
    schema = fields.schema()

    assert [item["key"] for item in schema] == list(fields.KEY_ORDER)
    assert {item["key"] for item in schema} == fields.KEYS
    assert len(schema) == len(fields.FIELDS)


def test_every_field_has_a_usable_desc():
    for item in fields.schema():
        desc = item["desc"]
        assert isinstance(desc, str) and desc.strip(), f"{item['key']} 没有说明"
        assert "_comment" not in desc, f"{item['key']} 的说明混进了键名"
        assert desc == desc.strip(), f"{item['key']} 的说明首尾有空白"


def test_schema_reports_the_declared_strategy():
    for item in fields.schema():
        key = item["key"]
        assert item["hot"] == fields.is_hot(key), key
        assert item["restart"] == fields.is_restart(key), key
        assert item["secret"] == (key in fields.secret_keys()), key


def test_schema_type_derives_from_the_default():
    """类型从默认值推断：bool 先于 int（bool 是 int 的子类）。"""
    for item in fields.schema():
        default = item["default"]
        if isinstance(default, bool):
            expected = "bool"
        elif isinstance(default, int):
            expected = "int"
        elif isinstance(default, str):
            expected = "str"
        else:
            expected = "json"
        assert item["type"] == expected, item["key"]


def test_write_json_drops_unknown_keys_and_follows_declaration_order(tmp_path):
    path = tmp_path / "config.json"
    payload = {
        "_comment": "旧模板的头说明",
        "_comment_web_port": "面板端口",
        "log_max_mb": 4,
        "custom": 1,
        "web_port": 5000,
    }

    write_json(str(path), payload)

    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert list(data) == [key for key in fields.KEY_ORDER if key in payload]
    assert [key for key in data if key.startswith("_comment")] == []
    assert "custom" not in data
    assert data["web_port"] == 5000
    assert text.endswith("\n")


def test_update_never_writes_unknown_keys_back(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(str(path), generate=False)

    store.update({"web_port": 5001, "custom": 1, "_comment": "旧模板"})

    assert store.read_raw() == {"web_port": 5001}
    assert json.loads(path.read_text(encoding="utf-8")) == {"web_port": 5001}


def test_legacy_file_with_comments_still_loads(tmp_path):
    """带 ``_comment*`` 的旧配置照常加载：不报错、值一致、未知键被丢弃。"""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "_comment": "Flandora 配置。首次启动会把这份示例复制成 config.json",
                "_comment_web_port": "面板端口",
                "web_port": 5002,
                "ob_server_port": 8100,
                "legacy_key": {"nested": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = ConfigStore(str(path), generate=False)

    assert store.settings.WEB_PORT == 5002
    assert store.settings.OB_SERVER_PORT == 8100
    assert store.read_raw() == {"web_port": 5002, "ob_server_port": 8100}


def test_generated_example_matches_the_field_table():
    result = subprocess.run(
        [sys.executable, GENERATOR, "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_example_lists_every_field_with_its_comment():
    """示例的排版契约：每个字段紧跟自己的 ``_comment_<key>``，值就是声明默认值。"""
    with open(EXAMPLE_PATH, encoding="utf-8") as handle:
        example = json.load(handle)

    order = list(example)
    for spec in fields.FIELDS:
        assert example[spec.key] == spec.default, spec.key
        index = order.index(spec.key)
        assert order[index + 1] == f"_comment_{spec.key}", spec.key
        assert example[f"_comment_{spec.key}"] == spec.desc, spec.key


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
