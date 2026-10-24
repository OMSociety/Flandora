"""消息段与 CQ 码编解码。

CQ 码是 OneBot v11 的可选字符串形态：文本与 ``[CQ:type,k=v]`` 混排，值和文本都要
转义 ``& [ ] ,`` 四个字符。这里断言的重点是**不丢内容**：不认识的 CQ 码、残缺的
参数、没闭合的方括号都必须原样留在文本里，客户端发来的半句话不能凭空消失。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest  # noqa: E402

from flandora import ob_segments as S  # noqa: E402


def test_escape_roundtrip_covers_all_four_chars():
    raw = "a&b[c]d,e"
    assert S.escape_text(raw) == "a&amp;b&#91;c&#93;d&#44;e"
    assert S.unescape_text(S.escape_text(raw)) == raw


def test_text_only_message_to_cq():
    segs = [S.text_segment("你好"), S.text_segment("，世界")]
    assert S.segments_to_cq(segs) == "你好，世界"


def test_non_text_segment_renders_params():
    segs = [{"type": "face", "data": {"id": "1"}}, {"type": "at", "data": {"qq": "123"}}]
    assert S.segments_to_cq(segs) == "[CQ:face,id=1][CQ:at,qq=123]"


def test_param_values_are_escaped():
    segs = [{"type": "image", "data": {"file": "a[1],b&c.png"}}]
    assert S.segments_to_cq(segs) == "[CQ:image,file=a&#91;1&#93;&#44;b&amp;c.png]"


def test_parse_mixed_text_and_cq():
    segs = S.parse_cq("看[CQ:image,file=x.png]这个")
    assert segs == [
        S.text_segment("看"),
        {"type": "image", "data": {"file": "x.png"}},
        S.text_segment("这个"),
    ]


def test_parse_unescapes_values():
    segs = S.parse_cq("[CQ:image,file=a&#91;1&#93;&#44;b&amp;c.png]")
    assert segs == [{"type": "image", "data": {"file": "a[1],b&c.png"}}]


def test_roundtrip_through_cq():
    segs = [
        S.text_segment("含&与[方括号],逗号"),
        {"type": "at", "data": {"qq": "10001"}},
        {"type": "image", "data": {"file": "x.png", "url": "http://a/b?c=1&d=2"}},
    ]
    assert S.parse_cq(S.segments_to_cq(segs)) == segs


def test_unknown_cq_type_survives_roundtrip():
    """不认识的段类型照样保留：类型名与参数都原样往返，不吞内容。"""
    text = "看[CQ:unknown_seg,x=1]这个"
    assert S.segments_to_cq(S.parse_cq(text)) == text


def test_non_ascii_cq_type_is_kept_as_text():
    text = "[CQ:中文段,x=1]"
    assert S.plain_text(S.parse_cq(text)) == text


def test_broken_cq_stays_literal():
    for text in ("[CQ:]", "[CQ:image,file=x.png", "没闭合[CQ:at", "[CQ:=1]"):
        assert S.plain_text(S.parse_cq(text)) == text


def test_trailing_comma_is_tolerated():
    assert S.parse_cq("[CQ:face,]") == [{"type": "face", "data": {}}]


def test_empty_string_is_empty_list():
    assert S.parse_cq("") == []
    assert S.normalize_message("") == []
    assert S.normalize_message([]) == []


def test_normalize_array_copies_segments():
    src = [{"type": "text", "data": {"text": "hi"}}]
    out = S.normalize_message(src)
    assert out == src
    out[0]["data"]["text"] = "changed"
    assert src[0]["data"]["text"] == "hi"


def test_normalize_accepts_missing_data_object():
    assert S.normalize_message([{"type": "face"}]) == [{"type": "face", "data": {}}]


def test_auto_escape_treats_string_as_plain_text():
    segs = S.normalize_message("[CQ:image,file=x.png]", auto_escape=True)
    assert segs == [S.text_segment("[CQ:image,file=x.png]")]


@pytest.mark.parametrize(
    "bad",
    [
        [123],
        [{"data": {}}],
        [{"type": "", "data": {}}],
        [{"type": "text", "data": "not-a-dict"}],
        {"type": "text", "data": {}},
        42,
        None,
    ],
)
def test_invalid_messages_raise(bad):
    with pytest.raises(S.SegmentError):
        S.normalize_message(bad)


def test_plain_text_ignores_non_text_segments():
    segs = [S.text_segment("a"), {"type": "image", "data": {"file": "x"}}]
    assert S.plain_text(segs) == "a"
