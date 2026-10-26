"""微信 4.x 图片 `.dat` 的解码。

格式（实测，微信 4.1.15.56）：

    [0:6]    magic 07 08 56 32 08 07
    [6:10]   AES 段明文长度 u32 LE（实测恒为 1024，不含填充）
    [10:14]  尾段长度     u32 LE
    [14]     填充字节
    [15 .. ] AES-128-ECB 密文，长度 = 明文长度按 PKCS#7 补齐到 16 的整数倍
    [ .. ]   明文中段
    [ .. ]   单字节 XOR 尾段

    完整图像 = AES 段解密结果（去掉 PKCS#7 填充）+ 明文中段 + 尾段逐字节 XOR

**填充块不是明文中段**：明文长度正好是 16 的整数倍时，PKCS#7 还要多出一个整块填充，
它的密文紧跟在 AES 段之后。把它当明文中段拼回去，HEVC 那类比特流会从那里失步
（CABAC 没有重同步点，整张图只剩上面一小条能解），JPEG 只是画面上多一道花。

**AES 密钥与 XOR 密钥都是账号级的**：换账号后必须重新取，取错就解出随机字节。
实测同一账号下 195/195 个文件全部可解。

密钥只在微信**实际解码图片**的那几秒内驻留内存；图片已缓存后就不加载。所以取钥
必须配合「打开一张没缓存过的图」，详见 tools/imagekey.py。
"""

from __future__ import annotations

import ctypes
import logging
import struct
from dataclasses import dataclass

from .wx_crypto import aes_cbc_decrypt  # noqa: F401  (保持 AES 走同一套 CNG 实现)

log = logging.getLogger("wx_image")

MAGIC = b"\x07\x08V2\x08\x07"
AES_BLOCK = 16

#: 解密结果的文件头 → 扩展名。wxgf 是微信私有的 HEVC 容器。
_MAGICS = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),
    (b"GIF8", "gif"),
    (b"wxgf", "wxgf"),
)


class ImageDecodeError(Exception):
    """这个文件不是可解的 V2 图片，或密钥不对。"""


@dataclass(frozen=True)
class DecodedImage:
    """一次成功解码的结果。"""

    data: bytes
    ext: str

    @property
    def size(self) -> int:
        return len(self.data)


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    """AES-ECB 解密（Windows CNG），入参长度必须是 16 的倍数。"""
    if len(key) not in (16, 24, 32):
        raise ImageDecodeError(f"AES 密钥长度应为 16/24/32，实为 {len(key)}")
    if not data or len(data) % AES_BLOCK:
        raise ImageDecodeError(f"密文长度应为 16 的倍数，实为 {len(data)}")

    bcrypt = ctypes.WinDLL("bcrypt")
    h_alg = ctypes.c_void_p()
    h_key = ctypes.c_void_p()
    try:
        _nt(bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0), "open")
        mode = ctypes.create_unicode_buffer("ChainingModeECB")
        _nt(
            bcrypt.BCryptSetProperty(
                h_alg, "ChainingMode", ctypes.cast(mode, ctypes.c_void_p), ctypes.sizeof(mode), 0
            ),
            "mode",
        )
        kb = ctypes.create_string_buffer(key, len(key))
        _nt(
            bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0, kb, len(key), 0),
            "key",
        )
        out = ctypes.create_string_buffer(len(data))
        done = ctypes.c_ulong(0)
        _nt(
            bcrypt.BCryptDecrypt(
                h_key, data, len(data), None, None, 0, out, len(data), ctypes.byref(done), 0
            ),
            "decrypt",
        )
        return out.raw[: done.value]
    finally:
        if h_key:
            bcrypt.BCryptDestroyKey(h_key)
        if h_alg:
            bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def _nt(status: int, what: str) -> None:
    if status != 0:
        raise ImageDecodeError(f"BCrypt {what} 失败，NTSTATUS=0x{status & 0xFFFFFFFF:08x}")


def _sniff(buf: bytes) -> str:
    for magic, ext in _MAGICS:
        if buf.startswith(magic):
            return ext
    return ""


def decode(raw: bytes, aes_key: bytes, xor_key: int) -> DecodedImage:
    """把一个 V2 `.dat` 的原始字节解成图片。

    `aes_key` 16 字节、`xor_key` 0-255。密钥不对时解出来的头不会是已知图片格式，
    此时抛 `ImageDecodeError`——批量处理时据异常就能区分「不是图片」与「密钥错了」。
    """
    if len(raw) < 15 + AES_BLOCK + 1:
        raise ImageDecodeError(f"文件太短（{len(raw)} 字节）")
    if raw[:6] != MAGIC:
        raise ImageDecodeError("不是 V2 格式")

    aes_size, xor_size = struct.unpack_from("<LL", raw, 6)
    if aes_size <= 0:
        raise ImageDecodeError(f"AES 段长度异常：{aes_size}")

    # 头里的长度是明文长度；密文按 PKCS#7 补齐，明文长度正好整除时还要多一个整块。
    pad_block = AES_BLOCK - aes_size % AES_BLOCK or AES_BLOCK
    aes_end = 15 + aes_size + pad_block
    raw_end = len(raw) - xor_size
    if aes_end > raw_end or raw_end > len(raw):
        raise ImageDecodeError(f"段长度越界：aes={aes_size} xor={xor_size} 文件={len(raw)}")

    padded = aes_ecb_decrypt(aes_key, raw[15:aes_end])
    pad = padded[-1]
    if not 0 < pad <= AES_BLOCK or padded[-pad:] != bytes([pad]) * pad:
        raise ImageDecodeError("AES 段的 PKCS#7 填充不合法（多半是密钥不对）")
    plain_aes = padded[:-pad]
    plain_mid = raw[aes_end:raw_end]
    plain_xor = bytes(b ^ xor_key for b in raw[raw_end:])
    data = plain_aes + plain_mid + plain_xor

    ext = _sniff(data)
    if not ext:
        raise ImageDecodeError("解出来的内容不是已知图片格式（多半是密钥不对）")
    return DecodedImage(data=data, ext=ext)


def decode_file(path: str, aes_key: bytes, xor_key: int) -> DecodedImage:
    with open(path, "rb") as fh:
        return decode(fh.read(), aes_key, xor_key)


def sniff_xor_key(paths, aes_key: bytes = b"") -> int:
    """从一批文件里反推 XOR 密钥。

    判据：尾段解出的最后一字节应当是图片的结束标记（JPEG 的 FFD9）。逐字节尝试
    0-255，取让最多文件成立的那个。样本不足时抛错，交上层要求用户提供。

    `aes_key` 只用于日志；反推本身不需要它——只用到文件尾字节与段长度。
    """
    votes: dict[int, int] = {}
    n = 0
    for p in paths[:64]:
        try:
            with open(p, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if len(raw) < 16 or raw[:6] != MAGIC:
            continue
        _aes, xor_size = struct.unpack_from("<LL", raw, 6)
        # 尾段至少要有一个字节，否则推不出密钥
        if xor_size < 1 or xor_size > len(raw) - 15:
            continue
        n += 1
        k = raw[-1] ^ 0xD9
        votes[k] = votes.get(k, 0) + 1
    if not votes:
        raise ImageDecodeError("没有足够的样本反推 XOR 密钥")
    best = max(votes, key=votes.get)
    log.info("反推 XOR 密钥 0x%02x（%d/%d 个文件支持）", best, votes[best], n)
    return best
