"""领域模型：会话与发言人身份、消息段、入站消息与待推送消息。

这些类型只描述「一条消息是什么」：不带 OneBot 字段名、不读配置、也不碰
本地库。入站侧由 ``bridge_core`` 组装，出站侧由 ``send_plan`` 组装，OneBot
的字段形状只在渲染时出现。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .content import ContentKind


class ChatKind(StrEnum):
    PRIVATE = "private"
    GROUP = "group"


@dataclass(frozen=True)
class ChatRef:
    """一个会话：微信侧标识 + 显示名 + 群/私聊。"""

    handle: str
    name: str
    kind: ChatKind

    @property
    def is_group(self) -> bool:
        return self.kind is ChatKind.GROUP


@dataclass(frozen=True)
class SenderRef:
    """一条消息的发言人。

    群消息的 ``handle`` 是发言人 wxid（昵称会改、同群可能重名，都不能当身份）；
    私聊取不到 wxid 时为空串，由渲染层退回会话标识。
    """

    handle: str
    nickname: str


@dataclass(frozen=True)
class QuoteRef:
    """被引用的那条消息。

    ``server_id`` 能回查 OneBot 的 message_id，是还原 reply 段的唯一依据；
    ``sender`` / ``name`` 只用于显示。
    """

    server_id: str
    sender: str = ""
    name: str = ""


class SegmentKind(StrEnum):
    """消息段的类型名。取值就是 OneBot v11 的段类型（``at`` 表示 @）。"""

    TEXT = "text"
    MENTION = "at"
    REPLY = "reply"
    IMAGE = "image"
    EMOJI = "emoji"
    FILE = "file"
    VOICE = "voice"
    VIDEO = "video"
    FACE = "face"


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Mention:
    """正文里的一个 @。

    ``user_id`` 为空表示只认得出名字、没解析出整数 ID；``name`` 是 @ 后面
    那段显示名，给日志与重名判断留底。
    """

    user_id: int | None
    name: str = ""


@dataclass(frozen=True)
class Reply:
    message_id: int


@dataclass(frozen=True)
class Image:
    """本地图片文件路径。"""

    path: str


@dataclass(frozen=True)
class Emoji:
    """本地表情包文件路径（表情包本体就是一张图）。"""

    path: str


@dataclass(frozen=True)
class File:
    path: str
    name: str = ""


@dataclass(frozen=True)
class Voice:
    path: str


@dataclass(frozen=True)
class Video:
    path: str


@dataclass(frozen=True)
class Face:
    """微信表情码（形如 ``"[撇嘴]"``）。

    OneBot 客户端发来的 QQ 表情号在协议转换层就换成了微信表情码，所以模型
    里只有这一个字段。
    """

    face_id: str


Segment = Text | Mention | Reply | Image | Emoji | File | Voice | Video | Face


@dataclass(frozen=True)
class InboundMessage:
    """一条从微信本地库读出来的消息。

    ``raw`` 是解码出来的原始字典，只给「必须看原文」的地方用（例如 @ 检测），
    其余字段都已类型化。``media`` 是解码器解出的本地媒体文件路径，正文里的
    占位符保持原样（图片的路径不进正文）。
    """

    chat: ChatRef
    sender: SenderRef
    text: str
    kind: ContentKind
    media: tuple[str, ...]
    quote: QuoteRef | None
    local_id: int
    server_id: str
    timestamp: int
    raw: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True)
class Message:
    """一条要推给 OneBot 客户端的消息。"""

    chat: ChatRef
    sender: SenderRef
    segments: tuple[Segment, ...]
    message_id: int
    timestamp: int
    mentioned: bool = False
    self_id: int = 0
