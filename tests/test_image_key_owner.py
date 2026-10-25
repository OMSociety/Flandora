"""验证：图片密钥的账号归属检查。

场景——用户从账号 A 切到 B，密钥文件仍是 A 的。此时必须按「无密钥」处理，
而不是拿 A 的密钥去解 B 的图。

两种跑法等价：

    python -m pytest tests/test_image_key_owner.py -q
    python tests/test_image_key_owner.py
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import config, wx_source  # noqa: E402

# 记账用的一份密钥，归属 wxid_someone_else
_KEY_PAYLOAD = {"wxid": "wxid_someone_else", "aes_key": "0123456789abcdef", "xor_key": 135}


@contextlib.contextmanager
def key_file(payload=None):
    """把配置临时指到临时目录里的一份密钥，出块即还原。

    走 ``config.temporary()`` 而不是直接改 ``config.WX_IMAGE_KEY_FILE``：惰性门面
    下改模块属性只是盖住真实 store 的值，下一次换 store 就失效。
    """
    payload = _KEY_PAYLOAD if payload is None else payload
    d = tempfile.mkdtemp(prefix="wwb-idx-")
    path = os.path.join(d, "wx_image_key.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    try:
        with config.temporary(
            os.path.join(d, "config.json"), overrides={"wx_image_key_file": path}
        ):
            yield path
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_key_of_other_account_is_ignored():
    """账号不符 → 按无密钥处理，不拿旧密钥去解新账号的图。"""
    with key_file():
        key, xor = wx_source.load_image_key("wxid_current_user")
    assert key == b"", f"账号不符时不应返回密钥，实际 {key!r}"
    assert xor == -1, f"账号不符时 XOR 也应为 -1，实际 {xor}"


def test_key_of_same_account_is_loaded():
    """账号一致 → 正常返回 AES 密钥与 XOR 密钥。"""
    with key_file():
        key, xor = wx_source.load_image_key("wxid_someone_else")
    assert key == b"0123456789abcdef", key
    assert xor == 135, xor


def test_unspecified_account_does_not_block():
    """未指定当前账号（老调用方）→ 不阻断。"""
    with key_file():
        key, xor = wx_source.load_image_key("")
    assert key == b"0123456789abcdef", key
    assert xor == 135, xor


def test_missing_key_file_returns_empty():
    """密钥文件不存在 → 返回 (b"", -1)，图片退回占位符而不抛错。"""
    d = tempfile.mkdtemp(prefix="wwb-idx-")
    try:
        with config.temporary(
            os.path.join(d, "config.json"),
            overrides={"wx_image_key_file": os.path.join(d, "absent.json")},
        ):
            key, xor = wx_source.load_image_key("wxid_anyone")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert (key, xor) == (b"", -1)


def test_bad_hex_key_is_rejected():
    """aes_key 既不是 32 位十六进制也不是 16 个 ASCII → 视为无密钥。"""
    with key_file({"wxid": "", "aes_key": "not-a-key", "xor_key": 1}):
        key, xor = wx_source.load_image_key("")
    assert key == b"", key
    assert xor == -1, xor


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


def _utf8_streams():
    """stdout 在非 UTF-8 环境（GBK 控制台、cp1252 CI）下打印中文会抛
    UnicodeEncodeError，退出码变成 1，看起来像测试失败。重新绑定成 UTF-8 并有损转义。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_streams()
    raise SystemExit(main())
