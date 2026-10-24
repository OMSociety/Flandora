"""把 OneBot 消息段序列规划成「发送动作」序列。

OneBot 的 `message` 是一串消息段，而微信这边一次发送只能承载一种形态：
一段文字（可带 @）、一张图片、一个文件。直接按段逐个发送会带来两个问题：

* 一段带 @ 的文字会被拆成「@某某」和「正文」两条消息；
* 文字段与 @ 段之间的顺序无法表达。

所以先把段序列规划成动作：连续的 `text` / `at` 段聚成一次文字发送（无 @ 时
仍然按段各发一条，保持既有行为），其余段各自成一次媒体发送。

本模块只做规划、不碰 UIA 与网络，便于离线测试；@ 的显示名解析与具体发送
留给 `ob_protocol` 与 `uia_sender`。
"""

from __future__ import annotations

from typing import Any

PLAIN_KINDS = frozenset({"text", "at"})


def _is_at(seg: dict) -> bool:
    return str(seg.get("type") or "") == "at"


def _text_of(seg: dict) -> str:
    data = seg.get("data") or {}
    if not isinstance(data, dict):
        return ""
    return str(data.get("text") or "")


def _at_target(seg: dict) -> dict:
    data = seg.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    qq = data.get("qq", "")
    return {"qq": qq, "name": str(data.get("name") or "")}


def plan_message(segments: Any) -> list[dict]:
    """把消息段序列规划成发送动作列表。

    返回的每一项是：

    * `{"kind": "text", "text": str}` —— 单独发一条文字；
    * `{"kind": "mention", "targets": [{"qq", "name"}], "text": str}` —— 发一条
      带 @ 的文字（`text` 是所有文字段按顺序拼起来的结果）；
    * `{"kind": <段类型>, "segment": dict}` —— 其余段原样交给发送层处理。

    非 dict 的条目直接忽略；`text` 为空的文字段不产生动作。
    """
    plan: list[dict] = []
    run: list[dict] = []

    def flush() -> None:
        if not run:
            return
        if any(_is_at(seg) for seg in run):
            plan.append(
                {
                    "kind": "mention",
                    "targets": [_at_target(seg) for seg in run if _is_at(seg)],
                    "text": "".join(_text_of(seg) for seg in run if not _is_at(seg)),
                }
            )
        else:
            for seg in run:
                text = _text_of(seg)
                if text:
                    plan.append({"kind": "text", "text": text})
        run.clear()

    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        kind = str(seg.get("type") or "")
        if kind in PLAIN_KINDS:
            run.append(seg)
            continue
        flush()
        plan.append({"kind": kind or "unknown", "segment": seg})
    flush()
    return plan
