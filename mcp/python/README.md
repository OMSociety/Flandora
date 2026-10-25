# flandora-mcp

把 Flandora 的动作目录与调用能力包成一个 MCP 服务，给支持 MCP 的 AI 客户端用（Claude Desktop、
Cursor、DSH 等）。让模型先「知道桥接能干什么」，再按需调动作，而不是靠猜。

```powershell
python -m pip install -e mcp\python
python -m flandora_mcp.server --selftest
```

## 接入客户端

多数客户端读 JSON 里的 `mcpServers`：

```json
{
  "mcpServers": {
    "flandora": {
      "command": "python",
      "args": ["-m", "flandora_mcp.server"],
      "env": {
        "FLANDORA_MCP_MODE": "read",
        "FLANDORA_MCP_ENDPOINT": "http://127.0.0.1:5700/",
        "FLANDORA_MCP_TOKEN": ""
      }
    }
  }
}
```

装成命令行后也可以直接写 `"command": "flandora-mcp"`。

## 三种模式

| 模式 | 能做什么 | 需要桥接在线 |
| --- | --- | --- |
| `docs`（默认） | 查动作目录：有什么、要什么参数、哪些没实现 | 不需要 |
| `read` | 加上调用只读动作：好友、群、群成员、消息、运行状态 | 需要 |
| `write` | 加上调用任意动作：发消息、@ 成员 | 需要 |

写模式必须显式设置 `FLANDORA_MCP_MODE=write`，环境变量写错（拼错名字）会直接退出，不会悄悄退回 docs 或放开权限。

## 工具

| 工具 | 说明 |
| --- | --- |
| `list_actions` | 列动作，可按分类过滤、只看已实现 |
| `get_action` | 查单个动作的完整定义（含未实现的 `wip` 原因） |
| `search_actions` | 按关键词搜动作 |
| `list_categories` | 列分类与每类动作数 |
| `query_action` | 调只读动作，需要 `read`/`write` 模式 |
| `invoke_action` | 调任意动作，需要 `write` 模式 |

另有资源 `flandora://onebot/actions`（整份动作目录），适合客户端一次性拉全量上下文。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `FLANDORA_MCP_MODE` | `docs` | `docs` / `read` / `write` |
| `FLANDORA_MCP_ENDPOINT` | 空 | 桥接 HTTP 服务端地址，如 `http://127.0.0.1:5700/`（对应 `ob_networks` 里的 `http_server` 入口） |
| `FLANDORA_MCP_TOKEN` | 空 | 与 `config.json` 的入口令牌一致 |
| `FLANDORA_MCP_TIMEOUT_MS` | `30000` | 单次调用超时 |

## 边界

- 动作目录来自构建期快照（`flandora_mcp/_catalog.json`），与桥接的动作表由 CI 校验一致；客户端看到的就是服务端真实实现，不会多也不会少。
- 桥接拒绝动作（未实现、参数错、群名未映射）会作为工具错误返回并带 `retcode`，模型能看到原因；只有连不上才是传输层错误。
- 这套服务只走 HTTP；桥接没开 `http_server` 入口时无法调用，`docs` 模式仍可用。
