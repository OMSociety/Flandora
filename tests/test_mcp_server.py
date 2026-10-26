"""MCP 服务的协议层：模式闸门、工具清单、stdio 传输。

不连真桥接：`query_action` / `invoke_action` 的成功路径由 `tests/test_mcp_client.py`
用本地 HTTP 假服务端覆盖，这里只测「该拒的拒」与「该给的给」。
"""

import io
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_ROOT = os.path.join(REPO_ROOT, "mcp", "python")
if MCP_ROOT not in sys.path:
    sys.path.insert(0, MCP_ROOT)

from flandora_mcp import server


def make_config(mode="docs", endpoint="", token="", timeout_ms=30000):
    config = server.Config.__new__(server.Config)
    config.mode = mode
    config.endpoint = endpoint
    config.token = token
    config.timeout = max(1.0, timeout_ms / 1000.0)
    return config


def rpc(config, method, params=None):
    request = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    return server.handle(config, request)


def tool_names(config):
    response = rpc(config, "tools/list")
    return [tool["name"] for tool in response["result"]["tools"]]


def test_initialize_reports_mode_and_version():
    response = rpc(make_config("docs"), "initialize", {"protocolVersion": "2024-11-05"})
    result = response["result"]
    assert result["protocolVersion"] == server.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "flandora-mcp"
    assert "docs 模式" in result["instructions"]


def test_initialized_notification_has_no_response():
    assert (
        server.handle(make_config(), {"jsonrpc": "2.0", "method": "notifications/initialized"})
        is None
    )


def test_docs_mode_hides_actions_that_touch_the_bridge():
    names = tool_names(make_config("docs"))
    assert names == ["list_actions", "get_action", "search_actions", "list_categories"]


def test_read_mode_adds_query_but_not_invoke():
    names = tool_names(make_config("read"))
    assert "query_action" in names
    assert "invoke_action" not in names


def test_write_mode_adds_invoke():
    names = tool_names(make_config("write"))
    assert "invoke_action" in names and "query_action" in names


def test_list_actions_can_filter_by_category_and_implementation():
    response = rpc(
        make_config(), "tools/call", {"name": "list_actions", "arguments": {"category": "消息"}}
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["total"] >= 3
    assert {a["category"] for a in payload["actions"]} == {"消息"}

    done = rpc(
        make_config(),
        "tools/call",
        {"name": "list_actions", "arguments": {"implemented_only": True}},
    )
    payload = json.loads(done["result"]["content"][0]["text"])
    assert payload["actions"] and all(a["implemented"] for a in payload["actions"])


def test_get_action_carries_wip_reason_for_unimplemented():
    response = rpc(
        make_config(), "tools/call", {"name": "get_action", "arguments": {"name": "set_group_kick"}}
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["implemented"] is False
    assert payload["wip"]


def test_get_action_accepts_alias():
    response = rpc(
        make_config(),
        "tools/call",
        {"name": "get_action", "arguments": {"name": "send_private_msg"}},
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["name"] == "send_private_msg"


def test_get_action_unknown_is_error():
    response = rpc(
        make_config(), "tools/call", {"name": "get_action", "arguments": {"name": "nope"}}
    )
    assert response["result"]["isError"] is True


def test_search_and_categories():
    response = rpc(
        make_config(), "tools/call", {"name": "search_actions", "arguments": {"query": "群"}}
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["total"] > 0

    cats = rpc(make_config(), "tools/call", {"name": "list_categories", "arguments": {}})
    payload = json.loads(cats["result"]["content"][0]["text"])
    assert sum(item["count"] for item in payload["categories"]) == len(server.catalog.ACTIONS)


def test_query_action_without_endpoint_explains_what_is_missing():
    response = rpc(
        make_config("read"),
        "tools/call",
        {"name": "query_action", "arguments": {"action": "get_group_list"}},
    )
    assert "FLANDORA_MCP_ENDPOINT" in response["result"]["content"][0]["text"]


def test_query_action_refuses_write_actions():
    response = rpc(
        make_config("read", endpoint="http://127.0.0.1:1/"),
        "tools/call",
        {"name": "query_action", "arguments": {"action": "send_group_msg"}},
    )
    assert response["result"]["isError"] is True
    assert "只读" in response["result"]["content"][0]["text"]


def test_unknown_tool_and_unknown_method():
    unknown = rpc(make_config(), "tools/call", {"name": "nuke", "arguments": {}})
    assert unknown["result"]["isError"] is True
    assert (
        server.handle(make_config(), {"jsonrpc": "2.0", "id": 9, "method": "nope"})["error"]["code"]
        == -32601
    )


def test_arguments_must_be_object():
    response = rpc(make_config(), "tools/call", {"name": "list_actions", "arguments": ["x"]})
    assert response["error"]["code"] == -32602


def test_resources_expose_the_catalog():
    listing = rpc(make_config(), "resources/list")
    assert listing["result"]["resources"][0]["uri"] == "flandora://onebot/actions"

    read = rpc(make_config(), "resources/read", {"uri": "flandora://onebot/actions"})
    payload = json.loads(read["result"]["contents"][0]["text"])
    assert len(payload["actions"]) == len(server.catalog.ACTIONS)

    missing = rpc(make_config(), "resources/read", {"uri": "flandora://nope"})
    assert missing["error"]["code"] == -32602


def test_stdio_transport_answers_line_by_line():
    stdin = io.StringIO(
        '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
        "\n"
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        "not json\n"
    )
    stdout = io.StringIO()
    server.serve(make_config(), stdin, stdout)
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0]["id"] == 1 and "serverInfo" in lines[0]["result"]
    assert lines[1]["id"] == 2 and lines[1]["result"]["tools"]
    assert lines[2]["error"]["code"] == -32700


def test_mode_validation(monkeypatch):
    monkeypatch.setenv("FLANDORA_MCP_MODE", "root")
    try:
        server.Config()
    except SystemExit as e:
        assert "docs/read/write" in str(e)
    else:  # pragma: no cover - 配置错必须直接退出
        raise AssertionError("非法模式应当 SystemExit")


def test_invoke_action_is_refused_outside_write_mode():
    """工具表不列 ≠ 调不到：照名字直接调 invoke_action 也要被模式挡下。"""
    for mode in ("docs", "read"):
        config = make_config(mode, endpoint="http://127.0.0.1:5700/")
        response = rpc(
            config,
            "tools/call",
            {"name": "invoke_action", "arguments": {"action": "get_login_info", "params": {}}},
        )
        text = json.dumps(response, ensure_ascii=False)
        assert f"{mode} 模式" in text, text
