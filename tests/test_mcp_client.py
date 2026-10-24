"""MCP 到桥接的 HTTP 调用：成功路径与两类失败。

真起一个本地 HTTP 假服务端（标准库 http.server），不碰真桥接。
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_ROOT = os.path.join(REPO_ROOT, "mcp", "python")
if MCP_ROOT not in sys.path:
    sys.path.insert(0, MCP_ROOT)

from flandora_mcp import server

TOKEN = "s3cret"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 的接口名
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        auth = self.headers.get("Authorization") or ""
        if auth != f"Bearer {TOKEN}":
            self._send(401, {"status": "failed", "retcode": 1401, "message": "Unauthorized"})
            return
        payload = json.loads(raw)
        action = payload.get("action")
        if action == "get_group_list":
            self._send(
                200,
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [{"group_id": 7, "group_name": "测试群"}],
                    "echo": payload.get("echo"),
                },
            )
        elif action == "send_group_msg":
            self._send(
                200,
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 41},
                    "echo": payload.get("echo"),
                },
            )
        elif action == "set_group_kick":
            self._send(
                200,
                {"status": "failed", "retcode": 1404, "message": "微信 PC 客户端不提供对应接口"},
            )
        else:
            self._send(
                200, {"status": "failed", "retcode": 1405, "message": f"不支持的动作：{action}"}
            )

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # 静音测试输出
        return


class FakeBridge:
    def __init__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(5)


def make_config(mode, endpoint, token=TOKEN):
    config = server.Config.__new__(server.Config)
    config.mode = mode
    config.endpoint = endpoint
    config.token = token
    config.timeout = 5.0
    return config


def call(config, name, arguments):
    response = server.handle(
        config,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    return response["result"]


def test_query_action_returns_bridge_data():
    with FakeBridge() as bridge:
        result = call(make_config("read", bridge.url), "query_action", {"action": "get_group_list"})
    payload = json.loads(result["content"][0]["text"])
    assert payload["data"][0]["group_name"] == "测试群"
    assert "isError" not in result


def test_invoke_action_sends_and_returns_message_id():
    with FakeBridge() as bridge:
        result = call(
            make_config("write", bridge.url),
            "invoke_action",
            {"action": "send_group_msg", "params": {"group_id": 7, "message": "hi"}},
        )
    payload = json.loads(result["content"][0]["text"])
    assert payload["data"]["message_id"] == 41


def test_business_failure_is_tool_error_with_retcode():
    with FakeBridge() as bridge:
        result = call(
            make_config("write", bridge.url),
            "invoke_action",
            {"action": "set_group_kick", "params": {}},
        )
    assert result["isError"] is True
    assert "1404" in result["content"][0]["text"]


def test_wrong_token_is_reported_as_auth_problem():
    with FakeBridge() as bridge:
        result = call(
            make_config("read", bridge.url, token="wrong"),
            "query_action",
            {"action": "get_group_list"},
        )
    assert result["isError"] is True
    assert "令牌" in result["content"][0]["text"]


def test_unreachable_bridge_is_tool_error():
    result = call(
        make_config("read", "http://127.0.0.1:1/"), "query_action", {"action": "get_group_list"}
    )
    assert result["isError"] is True
    assert "连不上桥接" in result["content"][0]["text"]


def test_unknown_action_never_reaches_the_bridge():
    with FakeBridge() as bridge:
        result = call(
            make_config("write", bridge.url), "invoke_action", {"action": "nuke", "params": {}}
        )
    assert result["isError"] is True
    assert "没有这个动作" in result["content"][0]["text"]
