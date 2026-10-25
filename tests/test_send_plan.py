"""发送计划：把消息段序列规划成发送动作。

规划层的职责只有一个——决定「哪些段合成一条消息、哪些段各发一条」。
连续的 text / at 段必须合起来：拆开发会把「@某人 看一下」变成两条消息，
@ 也就落在了一条没有正文的消息里。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import send_plan  # noqa: E402
from flandora.domain.message import (  # noqa: E402
    Emoji,
    Face,
    File,
    Image,
    Mention,
    Reply,
    SegmentKind,
    Text,
    Voice,
)
from flandora.domain.send import MentionTarget, SendMedia, SendMention, SendText  # noqa: E402


def test_text_segments_stay_separate_without_mention():
    plan = send_plan.plan_message([Text("第一句"), Text("第二句")])
    assert plan == [SendText(text="第一句"), SendText(text="第二句")]


def test_mention_run_is_merged_into_one_message():
    plan = send_plan.plan_message([Mention(user_id=123), Text(" 看一下"), Text("这个")])
    assert plan == [SendMention(targets=(MentionTarget(qq=123, name=""),), text=" 看一下这个")]


def test_text_around_a_mention_stays_one_message():
    # 一次 @ 前后跟着的文字属于同一条消息：拆开发会让「前 后」之间的语序消失
    plan = send_plan.plan_message([Text("前"), Mention(user_id=1), Text("后")])
    assert plan == [SendMention(targets=(MentionTarget(qq=1, name=""),), text="前后")]


def test_media_segment_breaks_the_run():
    plan = send_plan.plan_message([Text("前"), Image("a.png"), Mention(user_id=1), Text("后")])
    assert [type(item) for item in plan] == [SendText, SendMedia, SendMention]
    assert plan[1] == SendMedia(kind=SegmentKind.IMAGE, path="a.png")
    assert plan[2].text == "后"


def test_mention_name_is_carried_through():
    plan = send_plan.plan_message([Mention(user_id=None, name="李四")])
    assert plan == [SendMention(targets=(MentionTarget(qq=None, name="李四"),), text="")]


def test_empty_text_segments_are_dropped():
    plan = send_plan.plan_message([Text(""), Text("")])
    assert plan == []


def test_reply_and_file_yield_no_action():
    # 引用构造不出来、file 段在协议转换层就被拒绝：规划层都不能凭空造一次发送
    plan = send_plan.plan_message([Reply(message_id=7), File(path="/tmp/x.bin")])
    assert plan == []


def test_face_uses_wechat_code_as_its_own_text_message():
    plan = send_plan.plan_message([Text("前"), Face(face_id="[撇嘴]"), Text("后")])
    assert plan == [
        SendText(text="前"),
        SendText(text="[撇嘴]", label="QQ表情"),
        SendText(text="后"),
    ]


def test_media_segments_map_to_their_kinds():
    plan = send_plan.plan_message([Image("/a.png"), Emoji("/b.gif"), Voice("/c.mp3")])
    assert plan == [
        SendMedia(kind=SegmentKind.IMAGE, path="/a.png"),
        SendMedia(kind=SegmentKind.EMOJI, path="/b.gif"),
        SendMedia(kind=SegmentKind.VOICE, path="/c.mp3"),
    ]


def test_empty_input_yields_empty_plan():
    assert send_plan.plan_message([]) == []
    assert send_plan.plan_message(None) == []
