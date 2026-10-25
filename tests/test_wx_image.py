"""图片解码的离线单测：结构解析、密钥校验、XOR 反推。

不依赖真微信数据——用现造的 V2 文件（真结构、假内容）。
"""

import os
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO_ROOT, "flandora")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import wx_image


def _encrypt(key: bytes, data: bytes) -> bytes:
    """用 AES-ECB 加密，给测试造数据用（复用解码器的 CNG 包装反过来调）。"""
    import ctypes

    bcrypt = ctypes.WinDLL("bcrypt")
    h_alg = ctypes.c_void_p()
    h_key = ctypes.c_void_p()
    try:
        wx_image._nt(
            bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0), "open"
        )
        mode = ctypes.create_unicode_buffer("ChainingModeECB")
        wx_image._nt(
            bcrypt.BCryptSetProperty(
                h_alg, "ChainingMode", ctypes.cast(mode, ctypes.c_void_p), ctypes.sizeof(mode), 0
            ),
            "mode",
        )
        kb = ctypes.create_string_buffer(key, len(key))
        wx_image._nt(
            bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0, kb, len(key), 0),
            "key",
        )
        out = ctypes.create_string_buffer(len(data))
        done = ctypes.c_ulong(0)
        wx_image._nt(
            bcrypt.BCryptEncrypt(
                h_key, data, len(data), None, None, 0, out, len(data), ctypes.byref(done), 0
            ),
            "enc",
        )
        return out.raw[: done.value]
    finally:
        if h_key:
            bcrypt.BCryptDestroyKey(h_key)
        if h_alg:
            bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def _pkcs7(data: bytes) -> bytes:
    pad = 16 - len(data) % 16
    return data + bytes([pad]) * pad


def _make_dat(
    jpeg: bytes, aes_key: bytes, xor_key: int, aes_plain_len: int = 64, mid: bytes = b""
) -> bytes:
    """按真实结构造一个 V2 文件：AES 段（PKCS#7）+ 明文中段 + XOR 尾段。

    头里的 ``aes_size`` 是**去掉填充**后的明文长度，密文按 PKCS#7 补齐——明文长度正好
    是 16 的整数倍时那多出来的整块填充也属于 AES 段，不属于明文中段。
    """
    plain_aes = jpeg[:aes_plain_len]
    enc = _encrypt(aes_key, _pkcs7(plain_aes))
    xor_part = jpeg[aes_plain_len:]
    enc_part = bytes(b ^ xor_key for b in xor_part)
    body = b"\x07\x08V2\x08\x07" + struct.pack("<LL", len(plain_aes), len(enc_part)) + b"\x01"
    return body + enc + mid + enc_part


KEY = b"0123456789abcdef"


def test_decode_roundtrip():
    """造一个文件再解回来，内容必须逐字节相同。"""
    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4 + b"\xff\xd9"
    raw = _make_dat(jpeg, KEY, 0x5A)
    img = wx_image.decode(raw, KEY, 0x5A)
    assert img.ext == "jpg", img.ext
    assert img.data == jpeg, (len(img.data), len(jpeg))


def test_decode_rejects_wrong_key():
    """密钥错时不能返回图片，必须抛 ImageDecodeError。"""
    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 2 + b"\xff\xd9"
    raw = _make_dat(jpeg, KEY, 0x5A)
    try:
        wx_image.decode(raw, b"fedcba9876543210", 0x5A)
    except wx_image.ImageDecodeError:
        pass
    else:
        raise AssertionError("错误密钥应当报错")


def test_decode_rejects_bad_magic():
    try:
        wx_image.decode(b"not a dat file" * 10, KEY, 0x5A)
    except wx_image.ImageDecodeError:
        pass
    else:
        raise AssertionError("非 V2 文件应当报错")


def test_decode_rejects_short():
    try:
        wx_image.decode(b"\x07\x08V2\x08\x07" + b"\x00" * 4, KEY, 0x5A)
    except wx_image.ImageDecodeError:
        pass
    else:
        raise AssertionError("过短文件应当报错")


def test_decode_rejects_out_of_range_lengths():
    """段长度越界不能越读内存，必须报错。"""
    body = b"\x07\x08V2\x08\x07" + struct.pack("<LL", 1024, 999999) + b"\x01"
    try:
        wx_image.decode(body + b"\x00" * 64, KEY, 0x5A)
    except wx_image.ImageDecodeError:
        pass
    else:
        raise AssertionError("越界长度应当报错")


def test_sniff_xor_key():
    """XOR 密钥能从文件尾部反推出来。"""
    import tempfile

    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 2 + b"\xff\xd9"
    with tempfile.TemporaryDirectory() as d:
        for i, k in enumerate((0x5A, 0x5A, 0x5A, 0x87)):
            raw = _make_dat(jpeg, KEY, k)
            with open(os.path.join(d, f"f{i}.dat"), "wb") as fh:
                fh.write(raw)
        got = wx_image.sniff_xor_key([os.path.join(d, f"f{i}.dat") for i in range(4)], KEY)
    assert got == 0x5A, hex(got)


def test_decode_keeps_the_pkcs7_block_out_of_the_payload():
    """明文长度整除时多出的整块填充不能混进载荷（HEVC 会从那里失步）。"""
    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 2 + b"\xff\xd9"
    raw = _make_dat(jpeg, KEY, 0x5A, aes_plain_len=64)
    got = wx_image.decode(raw, KEY, 0x5A)
    assert got.data == jpeg, (len(got.data), len(jpeg))


def test_decode_accepts_unaligned_plain_len():
    """明文长度不是 16 的整数倍时填充在最后一块里，也要解对。"""
    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 2 + b"\xff\xd9"
    raw = _make_dat(jpeg, KEY, 0x5A, aes_plain_len=70)
    got = wx_image.decode(raw, KEY, 0x5A)
    assert got.data == jpeg, (len(got.data), len(jpeg))


def test_decode_keeps_the_plain_mid_region():
    """明文中段要原样拼在 AES 段与 XOR 尾段之间。"""
    jpeg = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 2 + b"\xff\xd9"
    mid = b"MID-MID-MID"
    raw = _make_dat(jpeg, KEY, 0x5A, aes_plain_len=64, mid=mid)
    got = wx_image.decode(raw, KEY, 0x5A)
    assert got.data == jpeg[:64] + mid + jpeg[64:], len(got.data)


def test_decode_rejects_broken_pkcs7_padding():
    """填充不合法（这里整块都是 0x00）时必须报错，不能把数据当明文返回。"""
    plain = b"\xff\xd8\xff\xe0" + b"A" * 60  # 正好 64 字节
    enc = _encrypt(KEY, plain + b"\x00" * 16)
    body = b"\x07\x08V2\x08\x07" + struct.pack("<LL", 64, 2) + b"\x01"
    try:
        wx_image.decode(body + enc + b"\xff\xd9", KEY, 0x5A)
    except wx_image.ImageDecodeError:
        pass
    else:
        raise AssertionError("填充不合法应当报错")


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
