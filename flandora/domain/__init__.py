"""领域模型包。

只放「消息是什么」的类型与它们的约束，不放配置、日志与平台细节：

- ``content``：正文类别与占位符判定
- ``message``：会话/发言人身份、消息段、入站消息、待推送消息
- ``send``：出站动作与统一执行入口
- ``store`` / ``window``：本地库与发送器的访问协议
"""

from .content import ContentKind, classify
from .message import (
    ChatKind,
    ChatRef,
    Emoji,
    Face,
    File,
    Image,
    InboundMessage,
    Mention,
    Message,
    QuoteRef,
    Reply,
    Segment,
    SegmentKind,
    SenderRef,
    Text,
    Video,
    Voice,
)
from .send import (
    MentionTarget,
    SendAction,
    SendMedia,
    SendMention,
    SendOutcome,
    SendStatus,
    SendText,
    execute,
)
from .store import Store
from .window import Sender

__all__ = [
    "ChatKind",
    "ChatRef",
    "ContentKind",
    "Emoji",
    "Face",
    "File",
    "Image",
    "InboundMessage",
    "Mention",
    "MentionTarget",
    "Message",
    "QuoteRef",
    "Reply",
    "Segment",
    "SegmentKind",
    "SendAction",
    "SendMedia",
    "SendMention",
    "SendOutcome",
    "SendStatus",
    "SendText",
    "Sender",
    "SenderRef",
    "Store",
    "Text",
    "Video",
    "Voice",
    "classify",
    "execute",
]
