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


def test_text_segments_stay_separate_without_mention():
    plan = send_plan.plan_message(
        [
            {"type": "text", "data": {"text": "第一句"}},
            {"type": "text", "data": {"text": "第二句"}},
        ]
    )
    assert plan == [
        {"kind": "text", "text": "第一句"},
        {"kind": "text", "text": "第二句"},
    ]


def test_mention_run_is_merged_into_one_message():
    plan = send_plan.plan_message(
        [
            {"type": "at", "data": {"qq": 123}},
            {"type": "text", "data": {"text": " 看一下"}},
            {"type": "text", "data": {"text": "这个"}},
        ]
    )
    assert plan == [
        {
            "kind": "mention",
            "targets": [{"qq": 123, "name": ""}],
            "text": " 看一下这个",
        }
    ]


def test_media_segment_breaks_the_run():
    plan = send_plan.plan_message(
        [
            {"type": "text", "data": {"text": "前"}},
            {"type": "image", "data": {"file": "a.png"}},
            {"type": "at", "data": {"qq": 1}},
            {"type": "text", "data": {"text": "后"}},
        ]
    )
    assert [item["kind"] for item in plan] == ["text", "image", "mention"]
    assert plan[2]["text"] == "后"


def test_mention_name_is_carried_through():
    plan = send_plan.plan_message([{"type": "at", "data": {"name": "李四"}}])
    assert plan[0]["targets"] == [{"qq": "", "name": "李四"}]
    assert plan[0]["text"] == ""


def test_empty_text_segments_are_dropped():
    plan = send_plan.plan_message(
        [
            {"type": "text", "data": {}},
            {"type": "text", "data": {"text": ""}},
        ]
    )
    assert plan == []


def test_non_dict_entries_are_ignored():
    plan = send_plan.plan_message(["x", None, {"type": "text", "data": {"text": "ok"}}])
    assert plan == [{"kind": "text", "text": "ok"}]


def test_missing_type_becomes_unknown():
    plan = send_plan.plan_message([{"data": {}}])
    assert plan == [{"kind": "unknown", "segment": {"data": {}}}]


def test_unknown_kind_is_kept_for_the_sender_to_reject():
    plan = send_plan.plan_message([{"type": "file", "data": {"file": "x"}}])
    assert plan == [{"kind": "file", "segment": {"type": "file", "data": {"file": "x"}}}]


def test_empty_input_yields_empty_plan():
    assert send_plan.plan_message([]) == []
    assert send_plan.plan_message(None) == []
