"""生成 ``flandora/config.example.json``：字段、默认值与说明的唯一来源是 ``config/fields.py``。

示例是给人看的模板，保留 ``_comment`` 与 ``_comment_<key>``；首次启动按它生成
``config.json`` 时，写盘只留「键: 值」，这两类说明项会被过滤掉。

用法：

    python tools/gen_config_example.py           写：重新生成示例
    python tools/gen_config_example.py --check   只比对：与磁盘不一致即非零退出

改了字段或说明却忘了重新生成，CI 里的 ``--check`` 会直接红。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flandora.config import fields  # noqa: E402

TARGET = os.path.join(ROOT, "flandora", "config.example.json")

HEADER = (
    "Flandora 配置模板。键与说明由 flandora/config/fields.py 生成，"
    "改字段或说明后跑 python tools/gen_config_example.py 重新生成，不要手改这份文件。"
    "实际配置写在 data/config.json，也可以直接在 WebUI 里改。"
)


def build() -> dict:
    """示例的内容与键序：先头说明，再逐项「键 → 默认值 → 说明」。"""
    payload: dict = {"_comment": HEADER}
    for spec in fields.FIELDS:
        payload[spec.key] = spec.default
        payload[f"_comment_{spec.key}"] = spec.desc
    return payload


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=4) + "\n"


def _first_difference(current: dict, expected: dict) -> str:
    """找出第一处差异：先按字段表顺序比共有的键，再找多出来的键。"""
    for key, expected_value in expected.items():
        if key not in current:
            return f"{key}：期望 {expected_value!r}，实际 缺失"
        if current[key] != expected_value:
            return f"{key}：期望 {expected_value!r}，实际 {current[key]!r}"
    for key in current:
        if key not in expected:
            return f"{key}：期望 不存在，实际 {current[key]!r}"
    return "键值一致，差异在排版（缩进、分隔符或换行）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 flandora/config/fields.py 生成配置示例（字段与说明的唯一来源）"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比对不写；与磁盘不一致时打印第一处差异并以 1 退出",
    )
    args = parser.parse_args(argv)

    rel = os.path.relpath(TARGET, ROOT)
    expected = render()
    try:
        with open(TARGET, encoding="utf-8", newline="") as handle:
            current = handle.read()
    except FileNotFoundError:
        current = None

    if not args.check:
        if current == expected:
            print(f"无需改动：{rel} 已是最新")
            return 0
        with open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(expected)
        print(f"已写入 {rel}")
        return 0

    if current == expected:
        print(f"{rel} 与字段表一致")
        return 0

    if current is None:
        difference = "文件不存在"
    else:
        try:
            difference = _first_difference(json.loads(current), json.loads(expected))
        except json.JSONDecodeError as exc:
            difference = f"磁盘上的文件不是有效 JSON：{exc}"
    print(f"配置示例与字段表不一致：{difference}", file=sys.stderr)
    print("修法：跑 python tools/gen_config_example.py 重新生成", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
