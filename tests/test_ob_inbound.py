"""入站结构还原：微信的 @ 与引用还原成 OneBot 段。

微信把「选中的提及」渲染成 `@昵称` 加一个 U+2005 分隔符，手打的 @ 没有这个分隔符；
引用消息把被引内容放在 appmsg 的 `<refermsg>` 里，`<svrid>` 就是被引消息的服务端号。
这两条都是本机真实数据里核对过的规律，这里守住它们的边界：认得出的还原成段，
认不出的一律留在原文里。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import ob_inbound  # noqa: E402

NAMES = {"wxid_lisi": "李四", "wxid_wangwu": "王五"}


def _resolve(name):
    hits = [wxid for wxid, shown in NAMES.items() if shown == name]
    return 1 if hits else 0


def test_real_mention_becomes_at_segment():
    segments = ob_inbound.split_mentions("@李四\u2005看一下这个", _resolve)
    assert segments[0] == {"type": "at", "data": {"qq": 1}}
    assert segments[1]["data"]["text"] == "看一下这个"


def test_plain_at_without_separator_stays_text():
    """手打的 @ 不是提及，不能被拆成 at 段。"""
    segments = ob_inbound.split_mentions("@李四 看一下这个", _resolve)
    assert [s["type"] for s in segments] == ["text"]
    assert segments[0]["data"]["text"] == "@李四 看一下这个"


def test_unknown_name_keeps_original_text():
    segments = ob_inbound.split_mentions("请 @查无此人\u2005 来一下", _resolve)
    assert [s["type"] for s in segments] == ["text"]
    assert segments[0]["data"]["text"] == "请 @查无此人\u2005 来一下"


def test_mention_only_message():
    segments = ob_inbound.split_mentions("@王五\u2005", _resolve)
    assert segments == [{"type": "at", "data": {"qq": 1}}]


def test_text_before_mention_is_kept():
    segments = ob_inbound.split_mentions("早上好 @李四\u2005帮我看下", _resolve)
    assert [s["type"] for s in segments] == ["text", "at", "text"]
    assert segments[0]["data"]["text"] == "早上好 "
    assert segments[2]["data"]["text"] == "帮我看下"


def test_reply_segment_uses_lookup():
    quote = {"server_id": "6961106450683539328", "sender": "wxid_lisi", "name": "李四"}
    seg = ob_inbound.reply_segment(quote, lambda sid: 1234 if sid == quote["server_id"] else None)
    assert seg == {"type": "reply", "data": {"id": 1234}}


def test_reply_segment_without_known_target_keeps_zero_id():
    """查不到被引消息时 id 记 0：引用关系仍在，只是取不到原文。"""
    seg = ob_inbound.reply_segment({"server_id": "999"}, lambda sid: None)
    assert seg == {"type": "reply", "data": {"id": 0}}


def test_reply_segment_requires_server_id():
    assert ob_inbound.reply_segment({}, lambda sid: 1) is None
    assert ob_inbound.reply_segment({"server_id": ""}, lambda sid: 1) is None


def test_resolve_from_names_requires_unique_match(isolated_ids):
    unique = {"wxid_lisi": "李四"}
    assert ob_inbound.resolve_from_names(unique, "李四") == isolated_ids.contact_id("wxid_lisi")


def test_resolve_from_names_refuses_duplicates(isolated_ids):
    """重名的人不止一个时无法确定是谁，返回 0 让调用方保留原文。"""
    dup = {"wxid_a": "小明", "wxid_b": "小明"}
    assert ob_inbound.resolve_from_names(dup, "小明") == 0


def test_resolve_from_names_unknown_name(isolated_ids):
    assert ob_inbound.resolve_from_names({"wxid_a": "小明"}, "小红") == 0
