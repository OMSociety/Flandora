"""把服务端的动作目录导出成两份快照：Python SDK 与 MCP 服务各读一份。

动作目录的唯一真相源是 `flandora/action_catalog.py`；这两个下游都不能 import 它
（会拖进 frida、uiautomation 这些只在 Windows 桥接机上装得起来的依赖），所以运行时
读 JSON 快照，改动作之后跑一遍这个脚本即可。`tests/test_sdk_catalog.py` 与
`tests/test_mcp_catalog.py` 会核对快照与真实目录一致，忘了重新生成会直接红。
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flandora import action_catalog  # noqa: E402

TARGETS = (
    os.path.join(ROOT, "sdk", "python", "flandora_sdk", "_catalog.json"),
    os.path.join(ROOT, "mcp", "python", "flandora_mcp", "_catalog.json"),
)


def build() -> dict:
    catalog = action_catalog.catalog_dict()
    return {
        "version": 1,
        "note": "由 tools/gen_sdk_catalog.py 从 flandora/action_catalog.py 生成，不要手改",
        "categories": catalog["categories"],
        "actions": catalog["actions"],
    }


def main() -> int:
    payload = build()
    for target in TARGETS:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with io.open(target, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"wrote {os.path.relpath(target, ROOT)} ({len(payload['actions'])} actions)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
