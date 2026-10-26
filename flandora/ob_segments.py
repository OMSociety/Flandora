"""OneBot v11 消息段与 CQ 码的编解码。

两个方向要分开看：

- **入站**（客户端 → 桥接）：``message`` 既可以是消息段数组，也可以是 CQ 码字符串。
  ``auto_escape=True`` 时整串按纯文本处理，不解析 ``[CQ:...]``。
- **出站**（桥接 → 客户端）：按配置决定 ``message`` 用数组还是 CQ 码字符串，
  ``raw_message`` 始终是 CQ 码字符串，这是 OneBot v11 的约定。

转义规则（v11 §6.1）在参数值与文本里都适用：``&`` → ``&amp;``、``[`` → ``&#91;``、
``]`` → ``&#93;``、``,`` → ``&#44;``。解析时先按 ``]`` 切段再反转义，因此值里出现
逗号、方括号都不会切错段。

不认识的 ``[CQ:...]`` 一律当普通文本保留，不吞内容也不抛异常：客户端发来的
消息宁可原样传过去让用户看见，也不要静默丢掉半句话。
"""

from __future__ import annotations

import re
from typing import Any

Segment = dict[str, Any]

CQ_PREFIX = "[CQ:"
_CQ_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

_ESCAPE = (
    ("&", "&amp;"),
    ("[", "&#91;"),
    ("]", "&#93;"),
    (",", "&#44;"),
)
# 反转义要一次扫完：先替换出来的 ``&`` 不能再和后面的 ``#91;`` 拼成 ``&#91;`` 被二次
# 解码，否则 ``a&amp;#91;b`` 这类原文在 escape → unescape 往返里会被吃掉一层。
_UNESCAPE_RE = re.compile(r"&(amp|#91|#93|#44);")
_UNESCAPE_MAP = {"amp": "&", "#91": "[", "#93": "]", "#44": ","}


class SegmentError(ValueError):
    """消息结构不合约（段不是 dict、type 缺失、data 不是对象等）。"""


def escape_text(text: str) -> str:
    """把文本转义成可以直接写进 CQ 码字符串的形式。"""
    out = text or ""
    for raw, esc in _ESCAPE:
        out = out.replace(raw, esc)
    return out


def unescape_text(text: str) -> str:
    """``escape_text`` 的逆操作：单次扫描，替换出来的字符不会再被解一遍。"""
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPE_MAP[m.group(1)], text or "")


def text_segment(text: str) -> Segment:
    return {"type": "text", "data": {"text": text or ""}}


def segments_to_cq(segments: list[Segment]) -> str:
    """消息段数组 → CQ 码字符串。

    文本段直接转义后拼接；其它段渲染成 ``[CQ:type,k=v,...]``，参数值单独转义。
    """
    parts = []
    for seg in segments or []:
        seg_type = seg.get("type") or ""
        data = seg.get("data") or {}
        if seg_type == "text":
            parts.append(escape_text(str(data.get("text", ""))))
            continue
        if not seg_type:
            continue
        args = ",".join(f"{k}={escape_text(str(v))}" for k, v in data.items())
        parts.append(f"{CQ_PREFIX}{seg_type}{',' + args if args else ''}]")
    return "".join(parts)


def _parse_cq_body(body: str) -> Segment | None:
    """解析 ``[CQ:`` 与 ``]`` 之间的内容；结构不对返回 None。

    段类型按 v11 约定是 ASCII 单词；不合这个形态的（例如 ``[CQ:=1]``、
    ``[CQ:中文]``）当普通文本保留，免得把用户的话静默吃掉。
    """
    head, _, tail = body.partition(",")
    seg_type = head.strip()
    if not _CQ_TYPE_RE.match(seg_type):
        return None
    data = {}
    if tail:
        for item in tail.split(","):
            key, sep, value = item.partition("=")
            if not sep or not key.strip():
                # 参数残缺（例如 [CQ:face] 后面接了一个裸逗号），整段当文本更安全
                return None
            data[unescape_text(key.strip())] = unescape_text(value)
    return {"type": seg_type, "data": data}


def parse_cq(text: str) -> list[Segment]:
    """CQ 码字符串 → 消息段数组。相邻的普通文本合并，空串回空列表。"""
    out: list[Segment] = []
    buf: list[str] = []
    i = 0
    n = len(text or "")
    while i < n:
        if text.startswith(CQ_PREFIX, i):
            end = text.find("]", i)
            if end == -1:
                buf.append(text[i:])
                break
            seg = _parse_cq_body(text[i + len(CQ_PREFIX) : end])
            if seg is None:
                buf.append(text[i : end + 1])
                i = end + 1
                continue
            if buf:
                out.append(text_segment(unescape_text("".join(buf))))
                buf.clear()
            out.append(seg)
            i = end + 1
            continue
        buf.append(text[i])
        i += 1
    if buf:
        out.append(text_segment(unescape_text("".join(buf))))
    return out


def _clean_segment(seg: Any, index: int) -> Segment:
    if not isinstance(seg, dict):
        raise SegmentError(f"第 {index + 1} 个消息段不是对象")
    seg_type = seg.get("type")
    if not isinstance(seg_type, str) or not seg_type:
        raise SegmentError(f"第 {index + 1} 个消息段缺少 type")
    data = seg.get("data")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SegmentError(f"第 {index + 1} 个消息段的 data 不是对象")
    return {"type": seg_type, "data": dict(data)}


def normalize_message(message: Any, *, auto_escape: bool = False) -> list[Segment]:
    """把客户端传来的 ``message`` 统一成消息段数组。

    接受消息段数组与 CQ 码字符串；``auto_escape=True`` 时字符串整串按纯文本处理。
    结构不合约抛 ``SegmentError``，由派发层转成 ``retcode=1400``。
    """
    if isinstance(message, list):
        return [_clean_segment(seg, i) for i, seg in enumerate(message)]
    if isinstance(message, str):
        if auto_escape:
            return [text_segment(message)] if message else []
        return parse_cq(message)
    raise SegmentError("message 必须是消息段数组或字符串")


def plain_text(segments: list[Segment]) -> str:
    """只取文本段拼成一行，用于日志与 ``raw_message`` 的纯文本兜底。"""
    return "".join(
        str(seg.get("data", {}).get("text", ""))
        for seg in segments or []
        if seg.get("type") == "text"
    )
