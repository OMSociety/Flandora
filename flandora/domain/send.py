"""出站动作与执行。

一条客户端消息先由 ``send_plan.plan_message`` 规划成若干动作，再由 ``execute``
统一执行：OneBot 的 ``send_msg`` 与面板的测试发送都走这一条路径，两边不会
再各写一套「先发文字、再发图片、失败怎么算」的逻辑。

发送器只通过 ``domain.window.Sender`` 协议调用；这里不读配置、不认识 OneBot
字段——base64 内联数据、客户端附件目录这类协议细节在准备阶段就解成了本地
文件路径。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .message import SegmentKind
from .window import Sender


class SendStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    REFUSED = "refused"


@dataclass(frozen=True)
class SendOutcome:
    """一条动作的执行结果。

    ``text`` 在 SENT 时是写进出站记录的文案（``[图片]``、``@李四 在吗``…），
    在 FAILED / REFUSED 时是给客户端的人话原因。``label`` 是段类型的人话名字，
    日志与「X发送失败」都用它——文案是按段类型拼的，不能由调用方另猜。
    """

    status: SendStatus
    label: str
    text: str


@dataclass(frozen=True)
class MentionTarget:
    """一个要 @ 的成员：整数 ID 优先，没有 ID 时按名字匹配名册。"""

    qq: int | None
    name: str


@dataclass(frozen=True)
class SendText:
    text: str
    label: str = "文字"


@dataclass(frozen=True)
class SendMention:
    targets: tuple[MentionTarget, ...]
    text: str


@dataclass(frozen=True)
class SendMedia:
    """一个已经落到本地文件的媒体段。"""

    kind: SegmentKind
    path: str
    name: str = ""


SendAction = SendText | SendMention | SendMedia

_MENTION_LABEL = "文字+@"

# 媒体段的「人话名字」与出站记录文案：语音/视频只能以文件形式投递，记录里
# 仍按语音/视频归类，面板与监控看到的是同一条消息的形状。
_MEDIA_LABEL = {
    SegmentKind.IMAGE: ("图片", "[图片]"),
    SegmentKind.EMOJI: ("表情", "[表情]"),
    SegmentKind.FILE: ("文件", "[文件]"),
    SegmentKind.VOICE: ("语音（文件形式）", "[语音]"),
    SegmentKind.VIDEO: ("视频（文件形式）", "[视频]"),
}


def _outcome(ok: bool, label: str, record: str) -> SendOutcome:
    if ok:
        return SendOutcome(SendStatus.SENT, label, record)
    return SendOutcome(SendStatus.FAILED, label, "")


def execute(
    actions: Sequence[SendAction],
    sender: Sender,
    resolve_mention: Callable[[tuple[MentionTarget, ...]], tuple[list[str], str]],
    contact: str,
) -> list[SendOutcome]:
    """按顺序执行动作，逐条返回结果。

    ``contact`` 是要搜索的微信会话显示名：发送器的每个方法都要它，所以它属于
    本次执行的上下文，而不是某条动作的字段。发送失败不抛异常，一律变成 FAILED
    结果——只有「这条没发出去」这个事实值得上报，异常会把整个 API 请求打掉。
    """
    outcomes: list[SendOutcome] = []
    for action in actions:
        if isinstance(action, SendText):
            outcomes.append(
                _outcome(bool(sender.send_text(contact, action.text)), action.label, action.text)
            )
        elif isinstance(action, SendMention):
            names, problem = resolve_mention(action.targets)
            if problem:
                outcomes.append(SendOutcome(SendStatus.REFUSED, _MENTION_LABEL, problem))
                continue
            ok = bool(sender.send_mention_text(contact, names, action.text))
            record = f"@{' @'.join(names)} {action.text}".strip()
            outcomes.append(_outcome(ok, _MENTION_LABEL, record))
        else:
            target = _MEDIA_LABEL.get(action.kind)
            if target is None:
                # 规划层不产这两种段；真出现了也不能假装发出去。
                reason = f"不支持的消息段 {action.kind}"
                outcomes.append(SendOutcome(SendStatus.REFUSED, str(action.kind), reason))
                continue
            label, record = target
            if action.kind in (SegmentKind.IMAGE, SegmentKind.EMOJI):
                ok = bool(sender.send_image(contact, action.path))
            else:
                ok = bool(sender.send_file(contact, action.path))
            outcomes.append(_outcome(ok, label, record))
    return outcomes
