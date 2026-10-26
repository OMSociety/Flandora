"""正文类别判定：三种占位符与普通文本。

微信解码器把「不是文本」的消息也拼成一段正文：图片解不出文件时只有
``[图片] md5="…"``，表情包是 ``[表情] md5="…"``。判定错一条，客户端就会把
一张图当成一条文字记进上下文。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora.domain.content import ContentKind, classify  # noqa: E402

MD5_REF = "0123456789abcdef0123456789abcdef"


def test_plain_text_is_text():
    assert classify("今天下午开会") == (ContentKind.TEXT, ())


def test_image_placeholder_is_image():
    assert classify("[图片]") == (ContentKind.IMAGE, ())


def test_image_with_local_path_carries_the_path():
    assert classify("[图片] C:/data/a.png") == (ContentKind.IMAGE, ("C:/data/a.png",))


def test_image_md5_reference_is_not_a_path():
    """解不出文件时尾巴是 md5 引用，不是能渲染的路径。"""
    assert classify(f'[图片] md5="{MD5_REF}"') == (ContentKind.IMAGE, ())


def test_emoji_placeholders_are_emoji():
    assert classify("[表情]") == (ContentKind.EMOJI, ())
    assert classify(f'[表情] md5="{MD5_REF}"') == (ContentKind.EMOJI, ())


def test_empty_text_is_text():
    assert classify("") == (ContentKind.TEXT, ())


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
