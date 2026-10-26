"""消息段构造器与 CQ 码编解码。

消息段就是 OneBot v11 的字典：`{"type": "text", "data": {"text": "你好"}}`。
这里提供的是「写起来短一点」的糖，以及和服务端一致的转义规则（`& [ ] ,` 四个字符），
保证 `to_cq` / `from_cq` 往返不被吃掉内容。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

Segment = dict[str, Any]

_ESCAPE = (
    ("&", "&amp;"),
    ("[", "&#91;"),
    ("]", "&#93;"),
    (",", "&#44;"),
)
# 反转义一次扫完：替换出来的 & 不会再和后面的 #91; 拼成 &#91; 被二次解码，
# 否则 "&amp;#91;" 这类原文在 escape → unescape 往返里会被吃掉一层。
_UNESCAPE_RE = re.compile(r"&(amp|#91|#93|#44);")
_UNESCAPE_MAP = {"amp": "&", "#91": "[", "#93": "]", "#44": ","}
_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def escape(text: Any) -> str:
    out = "" if text is None else str(text)
    for frm, to in _ESCAPE:
        out = to.join(out.split(frm))
    return out


def unescape(text: Any) -> str:
    out = "" if text is None else str(text)
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPE_MAP[m.group(1)], out)


def text(content: str) -> Segment:
    return {"type": "text", "data": {"text": "" if content is None else str(content)}}


def at(qq: int | str) -> Segment:
    return {"type": "at", "data": {"qq": qq}}


def at_all() -> Segment:
    return {"type": "at", "data": {"qq": "all"}}


def reply(message_id: int) -> Segment:
    return {"type": "reply", "data": {"id": int(message_id)}}


def face(face_id: int) -> Segment:
    return {"type": "face", "data": {"id": int(face_id)}}


def image(file: str, **extra: Any) -> Segment:
    return {"type": "image", "data": {"file": file, **extra}}


def record(file: str) -> Segment:
    return {"type": "record", "data": {"file": file}}


def video(file: str) -> Segment:
    return {"type": "video", "data": {"file": file}}


def to_cq(segments: Iterable[Segment]) -> str:
    parts: list[str] = []
    for seg in segments or []:
        seg_type = str((seg or {}).get("type") or "")
        data = (seg or {}).get("data") or {}
        if seg_type == "text":
            parts.append(escape(data.get("text", "")))
            continue
        params = "".join(
            f",{key}={escape(value)}"
            for key, value in data.items()
            if value is not None and value != ""
        )
        parts.append(f"[CQ:{seg_type}{params}]")
    return "".join(parts)


def _parse_body(body: str) -> Segment | None:
    head, sep, tail = body.partition(",")
    seg_type = head.strip()
    if not _TYPE_RE.match(seg_type):
        return None
    data: dict[str, Any] = {}
    if sep:
        for item in tail.split(","):
            if not item:
                continue
            key, eq, value = item.partition("=")
            if not eq:
                return None
            key = key.strip()
            value = unescape(value)
            # qq / id 是 SDK 自己构造消息时用的整数参数，纯数字就还原成 int，
            # 这样 to_cq -> from_cq 往返不会把 14 变成 "14"；"all" 之类保持字符串。
            if key in ("qq", "id") and value.isdigit():
                data[key] = int(value)
            else:
                data[key] = value
    return {"type": seg_type, "data": data}


def from_cq(cq: str) -> list[Segment]:
    out: list[Segment] = []
    source = "" if cq is None else str(cq)
    index = 0
    while index < len(source):
        start = source.find("[CQ:", index)
        if start < 0:
            tail = source[index:]
            if tail:
                out.append(text(unescape(tail)))
            break
        if start > index:
            out.append(text(unescape(source[index:start])))
        end = source.find("]", start)
        if end < 0:
            out.append(text(unescape(source[start:])))
            break
        seg = _parse_body(source[start + 4 : end])
        if seg is None:
            out.append(text(unescape(source[start : end + 1])))
        else:
            out.append(seg)
        index = end + 1
    return out


def normalize(message: str | Iterable[Segment] | None) -> list[Segment]:
    """把字符串、段数组或消息链统一成段数组。"""
    if message is None:
        return []
    if isinstance(message, MessageChain):
        return message.to_list()
    if isinstance(message, str):
        return from_cq(message) if "[CQ:" in message else [text(message)]
    out: list[Segment] = []
    for seg in message:
        if not isinstance(seg, dict):
            raise TypeError(f"消息段必须是 dict，收到 {type(seg).__name__}")
        out.append({"type": str(seg.get("type") or ""), "data": dict(seg.get("data") or {})})
    return out


class MessageChain:
    """把若干段拼成一条消息，`str(chain)` 得到 CQ 码。"""

    def __init__(self, *segments: Segment | str | None):
        self._segments: list[Segment] = []
        for seg in segments:
            if seg is None:
                continue
            if isinstance(seg, str):
                self._segments.append(text(seg))
            else:
                self._segments.append({"type": seg["type"], "data": dict(seg.get("data") or {})})

    def text(self, content: str) -> "MessageChain":
        self._segments.append(text(content))
        return self

    def at(self, qq: int | str) -> "MessageChain":
        self._segments.append(at(qq))
        return self

    def image(self, file: str, **extra: Any) -> "MessageChain":
        self._segments.append(image(file, **extra))
        return self

    def extend(self, segments: Iterable[Segment]) -> "MessageChain":
        self._segments.extend(normalize(segments))
        return self

    def to_list(self) -> list[Segment]:
        return [
            {"type": seg["type"], "data": dict(seg.get("data") or {})} for seg in self._segments
        ]

    def to_cq(self) -> str:
        return to_cq(self._segments)

    def __iter__(self):
        return iter(self._segments)

    def __len__(self) -> int:
        return len(self._segments)

    def __str__(self) -> str:
        return self.to_cq()

    def __repr__(self) -> str:
        return f"MessageChain({self.to_cq()!r})"
