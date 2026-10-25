"""把消息段序列规划成「发送动作」序列。

微信这边一次发送只能承载一种形态：一段文字（可带 @）、一张图片、一个文件。
直接按段逐个发送会带来两个问题：

* 一段带 @ 的文字会被拆成「@某某」和「正文」两条消息；
* 文字段与 @ 段之间的顺序无法表达。

所以先把段序列规划成动作：连续的 text / at 段聚成一次文字发送（无 @ 时仍然
按段各发一条，保持既有行为），其余段各自成一次媒体发送。

本模块只做规划、不碰 UIA 与网络，也不读配置，便于离线测试；@ 的显示名解析
与具体发送留给 ``ob_protocol`` 与 ``domain.send.execute``。
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain.message import (
    Emoji,
    Face,
    Image,
    Mention,
    Reply,
    Segment,
    SegmentKind,
    Text,
    Video,
    Voice,
)
from .domain.send import MentionTarget, SendAction, SendMedia, SendMention, SendText

# QQ 表情在微信里是「发一段表情码文本」，与普通文字分开记日志与记账
FACE_LABEL = "QQ表情"


def plan_message(segments: Sequence[Segment] | None) -> list[SendAction]:
    """把消息段序列规划成发送动作列表。

    规则：

    * 连续的 Text / Mention 段聚成一次发送：带 @ 时合成一条 ``SendMention``
      （@ 顺序与段顺序一致，正文是这段里所有文字按顺序拼接）；不带 @ 时每个
      非空文字段各自一条——客户端把文本写进多个段，就是「发多条消息」的意思。
    * Reply 段不产生动作：微信 UIA 构造不出引用，调用方据此回报「只有引用段」。
    * Face 段（已换成微信表情码的 QQ 表情）单独成一条文字，不并入前面的文字：
      它是独立的一次输入。
    * 媒体段各自成一次发送；File 段不产生动作（协议转换层已按「不支持」拒绝）。
    """
    plan: list[SendAction] = []
    run: list[Text | Mention] = []

    def flush() -> None:
        if not run:
            return
        mentions = [seg for seg in run if isinstance(seg, Mention)]
        if mentions:
            plan.append(
                SendMention(
                    targets=tuple(MentionTarget(qq=seg.user_id, name=seg.name) for seg in mentions),
                    text="".join(seg.text for seg in run if isinstance(seg, Text)),
                )
            )
        else:
            for seg in run:
                if isinstance(seg, Text) and seg.text:
                    plan.append(SendText(text=seg.text))
        run.clear()

    for seg in segments or ():
        if isinstance(seg, (Text, Mention)):
            run.append(seg)
            continue
        flush()
        if isinstance(seg, Reply):
            continue
        if isinstance(seg, Face):
            if seg.face_id:
                plan.append(SendText(text=seg.face_id, label=FACE_LABEL))
        elif isinstance(seg, Image):
            plan.append(SendMedia(kind=SegmentKind.IMAGE, path=seg.path))
        elif isinstance(seg, Emoji):
            plan.append(SendMedia(kind=SegmentKind.EMOJI, path=seg.path))
        elif isinstance(seg, Voice):
            plan.append(SendMedia(kind=SegmentKind.VOICE, path=seg.path))
        elif isinstance(seg, Video):
            plan.append(SendMedia(kind=SegmentKind.VIDEO, path=seg.path))
    flush()
    return plan
