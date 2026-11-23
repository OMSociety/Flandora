"""微信语音（SILK v3）解成 WAV。

语音本体存在 ``media_0.db`` 的 ``VoiceInfo.voice_data``：首字节 ``0x02`` 后跟
``#!SILK_V3`` 标准 SILK 流。解码交给 ``silk-python``（带 Cython 扩展的 CPython
轮子，随发布包打入）。输出 24 kHz 单声道 16 位 PCM 的 WAV，客户端不需要自带 SILK 解码器。
"""

from __future__ import annotations

import io
import wave

SAMPLE_RATE = 24000
_SILK_MAGIC = b"#!SILK_V3"


class VoiceDecodeError(Exception):
    """语音数据不是可解的 SILK。"""


def silk_to_wav(blob: bytes) -> bytes:
    """``voice_data`` 原始字节 → WAV 字节。"""
    if not blob:
        raise VoiceDecodeError("语音数据为空")
    data = bytes(blob)
    if data[:1] == b"\x02" and data[1:].startswith(_SILK_MAGIC):
        data = data[1:]  # 微信在标准 SILK 头前多放了一个 0x02
    if not data.startswith(_SILK_MAGIC):
        raise VoiceDecodeError("语音数据不是 SILK v3")
    try:
        import pysilk

        pcm = io.BytesIO()
        pysilk.decode(io.BytesIO(data), pcm, SAMPLE_RATE)
    except Exception as exc:  # noqa: BLE001 - 解码库的异常类型随后端而异
        raise VoiceDecodeError(f"SILK 解码失败：{exc}") from exc
    frames = pcm.getvalue()
    if not frames:
        raise VoiceDecodeError("SILK 解码结果为空")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(frames)
    return out.getvalue()
