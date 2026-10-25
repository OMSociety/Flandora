"""网络入口配置的保存校验。

`/api/config` 收到 ob_networks 时要先规范化：写进文件后再静默回退，用户改错了也看不出来。
"""

import pytest
from fastapi.responses import JSONResponse

from flandora import config, ob_network
from flandora.webui import app as webui_app

NODE = {
    "type": "ws_client",
    "name": "astrbot",
    "url": "ws://127.0.0.1:6199/ws",
    "role": "universal",
}


@pytest.fixture()
def saved(monkeypatch):
    """拦住磁盘读写，只留内存里的那一份配置。"""
    box = {"cfg": {}}
    monkeypatch.setattr(webui_app, "_read_config_file", lambda: dict(box["cfg"]))
    monkeypatch.setattr(webui_app, "_write_config_file", lambda cfg: box.update(cfg=dict(cfg)))
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)
    monkeypatch.setattr(config, "OB_SERVER_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "OB_SERVER_PORT", 7999)
    monkeypatch.setattr(config, "OB_SERVER_TOKEN", "")
    monkeypatch.setattr(config, "OB_NETWORKS", [])
    return box


def test_valid_nodes_are_normalized_and_live(saved):
    rest = webui_app.api_put_config({"ob_networks": [NODE]}, webui_app.Response())

    assert rest["ok"] is True
    assert "ob_networks" in rest["restart_required"]
    assert saved["cfg"]["ob_networks"][0]["type"] == "ws_client"
    # 规范化会补上默认值，落盘的是补全后的整项
    assert saved["cfg"]["ob_networks"][0]["reconnect_interval_ms"] == 5000
    assert config.OB_NETWORKS[0]["url"] == "ws://127.0.0.1:6199/ws"


def test_bad_node_is_refused_before_writing(saved):
    rest = webui_app.api_put_config(
        {"ob_networks": [{"type": "carrier-pigeon"}]}, webui_app.Response()
    )

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert saved["cfg"] == {}
    assert config.OB_NETWORKS == []


def test_empty_list_falls_back_to_the_general_settings_entry(saved):
    rest = webui_app.api_put_config({"ob_networks": []}, webui_app.Response())

    assert rest["ok"] is True
    assert saved["cfg"]["ob_networks"] == []
    assert len(config.OB_NETWORKS) == 1
    assert config.OB_NETWORKS[0]["type"] == "ws_server"
    assert config.OB_NETWORKS[0]["port"] == 7999


def test_bad_entry_keeps_previous_nodes(saved, monkeypatch):
    monkeypatch.setattr(config, "OB_NETWORKS", [ob_network.default_node("127.0.0.1", 7999, "")])

    rest = webui_app.api_put_config(
        {"ob_networks": [dict(NODE, role="god-mode")]}, webui_app.Response()
    )

    assert isinstance(rest, JSONResponse)
    assert config.OB_NETWORKS[0]["type"] == "ws_server"
