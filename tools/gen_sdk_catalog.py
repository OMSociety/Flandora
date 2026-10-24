"""把服务端的动作目录导出成 SDK 用的快照。

动作目录的唯一真相源是 `flandora/action_catalog.py`；SDK 不能 import 它（那会拖进
frida、uiautomation 这些只在 Windows 桥接机上装得起来的依赖），所以运行时读这份
JSON 快照，改动作之后跑一遍这个脚本即可。`tests/test_sdk_catalog.py` 会核对快照与
真实目录一致，忘了重新生成会直接红。
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flandora import action_catalog  # noqa: E402

TARGET = os.path.join(ROOT, "sdk", "python", "flandora_sdk", "_catalog.json")


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
    os.makedirs(os.path.dirname(TARGET), exist_ok=True)
    with io.open(TARGET, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"wrote {TARGET} ({len(payload['actions'])} actions)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
