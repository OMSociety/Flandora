"""消息内容的类别判定。

微信本地库的解码器把「不是文本」的消息也拼成一段正文：图片是
``[图片] <本地路径>``（解不出文件时尾巴是 ``md5="…"``），表情包是
``[表情] md5="…"`` 或旧的 ``[动画表情]``。这段正文会被当成 OneBot 文本推给
客户端，所以判定必须贴着**产生**这些字符串的地方：占位符字面量只在这里
定义，``wx_store`` 造文本、``wx_source`` 拼路径都引用这几个常量。
"""

from enum import StrEnum

IMAGE_PLACEHOLDER = "[图片]"
EMOJI_PLACEHOLDER = "[表情]"
EMOJI_PLACEHOLDERS = (EMOJI_PLACEHOLDER, "[动画表情]")

# 解不出图片文件时，解码器把图片的引用写成 md5="…"；那不是能渲染的路径。
_MD5_PREFIX = "md5="


class ContentKind(StrEnum):
    """一条收件消息的内容类别。

    RECALL / POKE 不从正文判定：撤回与拍一拍在收件字典里另有结构化字段
    （``revoke`` / ``pat``），正文只是给客户端看的占位文本。
    """

    TEXT = "text"
    IMAGE = "image"
    EMOJI = "emoji"
    RECALL = "recall"
    POKE = "poke"


def classify(text: str) -> tuple[ContentKind, tuple[str, ...]]:
    """判定正文类别，并取出其中的本地媒体路径。

    返回的路径只包括**能直接渲染的文件**：图片占位符的尾巴可能是 md5 引用，
    那不是路径，不返回（客户端只该看到占位文本）。表情包的本地文件由收件源
    另行给出，正文里只有 md5。
    """
    text = text or ""
    if text == IMAGE_PLACEHOLDER:
        return ContentKind.IMAGE, ()
    if text.startswith(IMAGE_PLACEHOLDER + " "):
        tail = text[len(IMAGE_PLACEHOLDER) + 1 :]
        if tail and not tail.startswith(_MD5_PREFIX):
            return ContentKind.IMAGE, (tail,)
        return ContentKind.IMAGE, ()
    if text in EMOJI_PLACEHOLDERS or text.startswith(EMOJI_PLACEHOLDER + " "):
        return ContentKind.EMOJI, ()
    return ContentKind.TEXT, ()
