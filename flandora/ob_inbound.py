"""入站消息的结构化还原：把微信侧的 @ 与引用还原成 OneBot 消息段。

微信桌面端拿到的正文是一段扁平文本，两处结构只能从文本里认出来：

- **@ 提及**：真正的 @ 是「@昵称」后面跟一个 U+2005（四分之一空格）或 U+200B；
  用户手打的 `@昵称` 后面没有这个分隔符。这是区分「真 @」与「只是打了个 @」的
  唯一依据，实测本机库里 5 条带分隔符的消息全部是选中成员产生的提及。
- **引用消息**：被引内容在 appmsg 的 `<refermsg>` 里，`<svrid>` 就是被引消息的
  服务端号，可用它回查 `message_id`，还原成 v11 的 `reply` 段。

还原不出来的部分一律保持原样：认不出的 @ 留在文本里，查不到的引用 id 记 0，
绝不猜测或丢弃。
"""

from __future__ import annotations

import re

from . import ob_ids, ob_segments

# 微信用来标记「这是一个选中的提及」的分隔符（U+2005 见得多，U+200B 也见过）
MENTION_SEPARATORS = "\u2005\u200b"
_MENTION_RE = re.compile(rf"@([^\s@{MENTION_SEPARATORS}]{{1,64}})[{MENTION_SEPARATORS}]")


def split_mentions(text: str, resolve) -> list[dict]:
    """把正文里真正的 @ 拆成 `at` 段。

    `resolve(昵称)` 返回该成员的 OneBot user_id；返回 None 表示认不出这个人，
    这一处 @ 连同分隔符原样留在文本段里，不做任何猜测。
    """
    segments: list[dict] = []
    if not text:
        return segments
    pos = 0
    for match in _MENTION_RE.finditer(text):
        user_id = resolve(match.group(1))
        if not user_id:
            continue
        if match.start() > pos:
            segments.append(ob_segments.text_segment(text[pos : match.start()]))
        segments.append({"type": "at", "data": {"qq": int(user_id)}})
        pos = match.end()
    if pos < len(text):
        segments.append(ob_segments.text_segment(text[pos:]))
    return segments


def reply_segment(quote: dict, lookup) -> dict | None:
    """把引用信息还原成 `reply` 段。

    `lookup(服务端号)` 返回被引消息的 message_id；查不到（那条消息没被本进程推送过）
    时 id 记 0——段本身仍在，客户端据此知道这是一条引用，只是没法按 id 取原文。
    """
    server_id = str((quote or {}).get("server_id") or "").strip()
    if not server_id:
        return None
    message_id = lookup(server_id)
    return {"type": "reply", "data": {"id": int(message_id or 0)}}


def resolve_from_names(names: dict, name: str) -> int:
    """按显示名查 user_id；没有或重名都返回 0（调用方据此保留原文）。

    `names` 是 wxid → 显示名 的映射（发送者本人视角的名字表）。重名的人不止一个时
    无法确定 @ 的是谁，宁可保留文本也不指错人。
    """
    target = (name or "").strip()
    if not target:
        return 0
    hits = [wxid for wxid, shown in names.items() if shown == target]
    if len(hits) != 1:
        return 0
    return ob_ids.contact_id(hits[0])
