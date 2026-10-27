"""表情消息的等待窗口：解不出文件时等密钥扫描，等到就带图推送，等不到仍按占位文本。

表情消息本来就在独立线程上处理（见 ``Flandora.emit_message``），这里的等待不会
挡住收件轮询；这条用例只验「补图」这一步的行为。
"""

from __future__ import annotations

import pytest

from flandora import bridge_core, wx_emoticon
from flandora.bridge_core import EMOJI_WAIT_SECONDS, Flandora
from flandora.domain.content import EMOJI_PLACEHOLDER, ContentKind
from flandora.domain.message import ChatKind, ChatRef, InboundMessage, SenderRef

MD5 = "ca2b1e3ba6f378481c9753f55428d6dd"


def _message(media=(), md5=MD5):
    return InboundMessage(
        chat=ChatRef(handle="wxid_a", name="某会话", kind=ChatKind.PRIVATE),
        sender=SenderRef(handle="wxid_a", nickname="某会话"),
        text=EMOJI_PLACEHOLDER,
        kind=ContentKind.EMOJI,
        media=tuple(media),
        quote=None,
        local_id=1,
        server_id="",
        timestamp=0,
        raw={"emojiMd5": md5},
    )


def test_emoji_without_a_file_waits_for_the_key(monkeypatch):
    """收件时没解出来，就按固定窗口等一次，等到就补进 media。"""
    seen = {}

    def fake_image_for(md5, *, wait=0.0, **kwargs):
        seen["md5"] = md5
        seen["wait"] = wait
        return "/tmp/emoji.gif"

    monkeypatch.setattr(bridge_core.wx_emoticon, "image_for", fake_image_for)
    out = Flandora(sender=None)._with_emoji_image(_message())

    assert out.media == ("/tmp/emoji.gif",)
    assert seen == {"md5": MD5, "wait": EMOJI_WAIT_SECONDS}


def test_emoji_already_resolved_is_left_alone(monkeypatch):
    """收件时已经解出文件就别再等：这条消息不该多花时间。"""
    monkeypatch.setattr(
        bridge_core.wx_emoticon, "image_for", lambda *a, **k: pytest.fail("不该再调一次")
    )
    out = Flandora(sender=None)._with_emoji_image(_message(media=("/tmp/ready.gif",)))
    assert out.media == ("/tmp/ready.gif",)


def test_emoji_without_a_key_still_reports_placeholder(monkeypatch):
    """等不到密钥就照旧推占位文本：客户端只会看到一种结果。"""

    def fake_image_for(md5, *, wait=0.0, **kwargs):
        raise wx_emoticon.EmoticonError("等了 5 秒还没扫到表情密钥")

    monkeypatch.setattr(bridge_core.wx_emoticon, "image_for", fake_image_for)
    out = Flandora(sender=None)._with_emoji_image(_message())
    assert out.media == ()
    assert out.text == EMOJI_PLACEHOLDER


def test_emoji_without_md5_is_not_waited_on(monkeypatch):
    """没有 md5 就无从解起，直接走占位文本，不做无谓等待。"""
    monkeypatch.setattr(
        bridge_core.wx_emoticon, "image_for", lambda *a, **k: pytest.fail("没有 md5 不该调")
    )
    out = Flandora(sender=None)._with_emoji_image(_message(md5=""))
    assert out.media == ()
