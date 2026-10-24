"""Flandora 的 MCP 服务（stdio）。

给 AI 客户端一个「知道 Flandora 能干什么、并且能按需调用」的入口：
- `docs`（默认）：只读动作目录，不需要桥接在线；
- `read`：可以调只读动作（查好友、群、消息），需要配置 endpoint；
- `write`：可以调任意动作（发消息等），必须显式 `FLANDORA_MCP_MODE=write`。

环境变量：
- `FLANDORA_MCP_ENDPOINT`：桥接 HTTP 服务端地址，如 `http://127.0.0.1:5700/`；
- `FLANDORA_MCP_TOKEN`：访问令牌（与 config.json 的 ob_server_token 一致）；
- `FLANDORA_MCP_TIMEOUT_MS`：单次调用超时，默认 30000；
- `FLANDORA_MCP_MODE`：`docs` / `read` / `write`。
"""

import json
import os
import sys
from typing import Any

from . import __version__, catalog, client

PROTOCOL_VERSION = "2024-11-05"
MODES = ("docs", "read", "write")

_MODE_HINT = {
    "docs": "当前是 docs 模式（只读动作目录）。要看效果请设 FLANDORA_MCP_MODE=read 或 write。",
    "read": "当前是 read 模式：只能调用只读动作。要发消息请设 FLANDORA_MCP_MODE=write。",
    "write": "当前是 write 模式：可以调用任意动作。",
}


class Config:
    def __init__(self) -> None:
        self.mode = str(os.environ.get("FLANDORA_MCP_MODE", "docs") or "docs").strip().lower()
        if self.mode not in MODES:
            raise SystemExit(f"FLANDORA_MCP_MODE 只能是 {'/'.join(MODES)}，收到 {self.mode!r}")
        self.endpoint = str(os.environ.get("FLANDORA_MCP_ENDPOINT", "") or "").strip()
        self.token = str(os.environ.get("FLANDORA_MCP_TOKEN", "") or "").strip()
        try:
            timeout_ms = int(os.environ.get("FLANDORA_MCP_TIMEOUT_MS", "30000") or 30000)
        except ValueError:
            timeout_ms = 30000
        self.timeout = max(1.0, timeout_ms / 1000.0)


def tool_definitions(config: Config) -> list[dict[str, Any]]:
    """按模式给出工具清单——docs 模式不该出现能写消息的工具。"""
    tools: list[dict[str, Any]] = [
        {
            "name": "list_actions",
            "description": "列出 Flandora 支持的 OneBot 动作（含未实现的，带 wip 说明原因）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "只看某个分类，如 消息 / 群管理"},
                    "implemented_only": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_action",
            "description": "查一个动作的完整定义：参数、是否只读、是否已实现、未实现的原因。",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_actions",
            "description": "按关键词搜动作（名字、别名、摘要、分类都能命中）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_categories",
            "description": "列出动作分类。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]
    if config.mode in ("read", "write"):
        tools.append(
            {
                "name": "query_action",
                "description": "调用一个只读动作（查好友、群、群成员、消息、运行状态）。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "params": {"type": "object", "default": {}},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            }
        )
    if config.mode == "write":
        tools.append(
            {
                "name": "invoke_action",
                "description": "调用任意动作（包括发消息、@ 成员）。写操作会真的发出去，确认目标再调。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "params": {"type": "object", "default": {}},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            }
        )
    return tools


def _text(payload: Any) -> list[dict[str, Any]]:
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]


def _fail(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _need_endpoint(config: Config) -> str | None:
    if config.endpoint:
        return None
    return "没有配置 FLANDORA_MCP_ENDPOINT（形如 http://127.0.0.1:5700/），这个工具需要连桥接"


def call_tool(config: Config, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    arguments = arguments or {}

    if name == "list_actions":
        category = str(arguments.get("category") or "")
        only_done = bool(arguments.get("implemented_only"))
        actions = [
            catalog.brief(a)
            for a in catalog.ACTIONS
            if (not category or a.get("category") == category)
            and (not only_done or a.get("implemented"))
        ]
        return {"content": _text({"total": len(actions), "actions": actions})}

    if name == "get_action":
        spec = catalog.get(str(arguments.get("name") or ""))
        if spec is None:
            return _fail(f"没有这个动作：{arguments.get('name')!r}（用 search_actions 找）")
        return {"content": _text(catalog.brief(spec))}

    if name == "search_actions":
        query = str(arguments.get("query") or "")
        try:
            limit = int(arguments.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        hits = catalog.search(query, max(1, min(limit, 50)))
        return {
            "content": _text(
                {"query": query, "total": len(hits), "actions": [catalog.brief(a) for a in hits]}
            )
        }

    if name == "list_categories":
        counts: dict[str, int] = {}
        for action in catalog.ACTIONS:
            cat = str(action.get("category") or "")
            counts[cat] = counts.get(cat, 0) + 1
        items = [{"name": c, "count": counts[c]} for c in catalog.CATEGORIES]
        return {"content": _text({"categories": items})}

    if name == "query_action":
        problem = _need_endpoint(config)
        if problem:
            return _fail(problem)
        spec = catalog.get(str(arguments.get("action") or ""))
        if spec is None:
            return _fail(f"没有这个动作：{arguments.get('action')!r}")
        if not spec.get("readOnly"):
            return _fail(
                f"{spec['name']} 不是只读动作，query_action 不能调它（用 invoke_action；需要 write 模式）"
            )
        return _run(config, spec["name"], arguments.get("params") or {})

    if name == "invoke_action":
        problem = _need_endpoint(config)
        if problem:
            return _fail(problem)
        spec = catalog.get(str(arguments.get("action") or ""))
        if spec is None:
            return _fail(f"没有这个动作：{arguments.get('action')!r}")
        return _run(config, spec["name"], arguments.get("params") or {})

    return _fail(f"没有这个工具：{name}")


def _run(config: Config, action: str, params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        return _fail("params 必须是对象")
    try:
        data = client.call(config.endpoint, config.token, action, params, config.timeout)
    except client.BridgeError as e:
        return _fail(f"桥接拒绝了这个动作（retcode={e.retcode}）：{e}")
    except client.TransportError as e:
        return _fail(f"连不上桥接：{e}")
    return {"content": _text({"action": action, "data": data})}


def handle(config: Config, request: dict[str, Any]) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 请求；通知类返回 None（不回包）。"""
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return _ok(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "flandora-mcp", "version": __version__},
                "instructions": f"Flandora OneBot 桥接的动作入口。{_MODE_HINT[config.mode]}",
            },
        )

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return _ok(request_id, {})

    if method == "tools/list":
        return _ok(request_id, {"tools": tool_definitions(config)})

    if method == "tools/call":
        params = request.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _err(request_id, -32602, "arguments 必须是对象")
        return _ok(request_id, call_tool(config, name, arguments))

    if method == "resources/list":
        return _ok(
            request_id,
            {
                "resources": [
                    {
                        "uri": "flandora://onebot/actions",
                        "name": "动作目录",
                        "description": f"Flandora 支持的 {len(catalog.ACTIONS)} 个 OneBot 动作",
                        "mimeType": "application/json",
                    }
                ]
            },
        )

    if method == "resources/read":
        uri = str((request.get("params") or {}).get("uri") or "")
        if uri != "flandora://onebot/actions":
            return _err(request_id, -32602, f"没有这个资源：{uri}")
        return _ok(
            request_id,
            {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(
                            {"categories": catalog.CATEGORIES, "actions": catalog.ACTIONS},
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    if request_id is None:
        return None
    return _err(request_id, -32601, f"不认识的方法：{method}")


def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(config: Config, stdin, stdout) -> None:
    """stdio 传输：一行一条 JSON-RPC，UTF-8。"""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            stdout.write(
                json.dumps(_err(None, -32700, f"不是合法 JSON：{e}"), ensure_ascii=False) + "\n"
            )
            stdout.flush()
            continue
        if not isinstance(request, dict):
            stdout.write(
                json.dumps(_err(None, -32600, "请求必须是对象"), ensure_ascii=False) + "\n"
            )
            stdout.flush()
            continue
        response = handle(config, request)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        # 控制台默认代码页会把中文打成乱码，自检输出固定走 UTF-8
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 - 重定向到非文本流时保持原样
            pass
        config = Config()
        print(
            f"flandora-mcp {__version__} mode={config.mode} endpoint={config.endpoint or '（未配置）'}"
        )
        for tool in tool_definitions(config):
            print(f"  {tool['name']}\t{tool['description']}")
        print(f"动作目录：{len(catalog.ACTIONS)} 个")
        return 0
    try:
        config = Config()
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    serve(config, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
