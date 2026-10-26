import flandora_sdk as sdk
from flandora_sdk import message as m


def test_escape_and_unescape_round_trip():
    raw = "a&b[c]d,e"
    escaped = m.escape(raw)
    assert escaped == "a&amp;b&#91;c&#93;d&#44;e"
    assert m.unescape(escaped) == raw


def test_text_segment_builds_onebot_shape():
    assert m.text("你好") == {"type": "text", "data": {"text": "你好"}}


def test_to_cq_escapes_text_and_params():
    segments = [m.text("a[b]"), m.at(123), m.image("x.png", summary="带,逗号")]
    assert (
        m.to_cq(segments) == "a&#91;b&#93;[CQ:at,qq=123][CQ:image,file=x.png,summary=带&#44;逗号]"
    )


def test_from_cq_parses_segments_and_plain_text():
    segments = m.from_cq("你好[CQ:at,qq=42] 再见[CQ:image,file=a.png]")
    assert segments[0] == {"type": "text", "data": {"text": "你好"}}
    assert segments[1] == {"type": "at", "data": {"qq": 42}}
    assert segments[2] == {"type": "text", "data": {"text": " 再见"}}
    assert segments[3] == {"type": "image", "data": {"file": "a.png"}}


def test_from_cq_keeps_malformed_brackets_as_text():
    segments = m.from_cq("看这个 [CQ:image,file=x 还没闭合")
    assert all(seg["type"] == "text" for seg in segments)
    joined = "".join(seg["data"]["text"] for seg in segments)
    assert joined == "看这个 [CQ:image,file=x 还没闭合"


def test_cq_round_trip_preserves_content():
    original = [m.text("括号[方]与逗号,和&符号"), m.face(14), m.record("a.silk")]
    assert m.from_cq(m.to_cq(original)) == original


def test_normalize_accepts_string_chain_and_list():
    chain = m.MessageChain().text("hi").at(7)
    assert m.normalize("hi") == [{"type": "text", "data": {"text": "hi"}}]
    assert m.normalize(chain) == chain.to_list()
    assert m.normalize([m.text("x")]) == [{"type": "text", "data": {"text": "x"}}]


def test_normalize_rejects_non_dict_segments():
    try:
        m.normalize([_BadSegment()])
    except TypeError as err:
        assert "dict" in str(err)
    else:
        raise AssertionError("非 dict 段应当抛 TypeError")


def test_message_chain_str_is_cq():
    chain = sdk.MessageChain().at(42).text(" 在吗")
    assert str(chain) == "[CQ:at,qq=42] 在吗"
    assert chain.to_cq() == str(chain)
    assert len(chain) == 2


def test_message_chain_is_isolated_from_input_segments():
    source = m.text("原始")
    chain = sdk.MessageChain(source)
    source["data"]["text"] = "被改"
    assert chain.to_list()[0]["data"]["text"] == "原始"


class _BadSegment:
    pass


def test_entity_text_is_not_decoded_twice():
    """反转义一次扫完：``&amp;#91;`` 只能解一层，否则原文会被吃掉一层。"""
    assert m.unescape("a&amp;#91;b") == "a&#91;b"
    raw = "a&#91;b&amp;c&#93;d&#44;e&f"
    assert m.unescape(m.escape(raw)) == raw
