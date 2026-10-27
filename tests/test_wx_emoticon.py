"""表情缓存解密链路的单元测试（不碰真微信、不读内存）。"""

from __future__ import annotations

import io
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from flandora import wx_emoticon  # noqa: E402

WXID = "wxid_c77x9dap1anl22"
MD5 = "ca2b1e3ba6f378481c9753f55428d6dd"


def test_derive_key_matches_the_known_vector():
    key = wx_emoticon.derive_key(689159559, WXID)
    assert key == bytes.fromhex("d6da8afb77e883b15dafa7ec5a75f871")
    assert len(key) == 16


def test_derive_key_refuses_without_wxid():
    with pytest.raises(wx_emoticon.EmoticonError):
        wx_emoticon.derive_key(1, "")


def test_decrypt_strips_a_full_pkcs7_block(monkeypatch):
    monkeypatch.setattr(
        wx_emoticon, "aes_cbc_decrypt", lambda key, iv, data: b"abc" + bytes([13]) * 13
    )
    assert wx_emoticon.decrypt(b"x" * 16, b"k" * 16) == b"abc"


def test_decrypt_keeps_bytes_when_the_padding_is_invalid(monkeypatch):
    plain = b"abc" + bytes([9]) * 5 + b"\x00" * 8
    monkeypatch.setattr(wx_emoticon, "aes_cbc_decrypt", lambda key, iv, data: plain)
    assert wx_emoticon.decrypt(b"x" * 16, b"k" * 16) == plain


def test_decrypt_refuses_a_length_that_is_not_a_block_multiple(monkeypatch):
    monkeypatch.setattr(wx_emoticon, "aes_cbc_decrypt", lambda key, iv, data: b"")
    with pytest.raises(wx_emoticon.EmoticonError):
        wx_emoticon.decrypt(b"x" * 17, b"k" * 16)


def _wxgf(width: int, height: int, extra: int, kind: int, version: int = 2) -> bytes:
    head = bytearray(b"wxgf")
    head += bytes([19, 0, version])
    head += struct.pack(">H", width) + struct.pack(">H", height) + struct.pack(">H", extra)
    kind_at = 15 if version == 0 else 16
    head += b"\x00" * (kind_at - len(head)) + bytes([kind])
    return bytes(head) + wx_emoticon.HEVC_START + b"\x40\x01\x0c\x01"


def test_parse_wxgf_reads_the_header_fields():
    info = wx_emoticon.parse_wxgf(_wxgf(240, 240, 6, 0x81))
    assert info["width"] == 240
    assert info["height"] == 240
    assert info["frames"] == 7
    assert info["kind"] == 0x81
    assert info["hevc"].startswith(wx_emoticon.HEVC_START)


def test_parse_wxgf_uses_the_version_0_type_offset():
    info = wx_emoticon.parse_wxgf(_wxgf(120, 90, 0, 0x40, version=0))
    assert info["kind"] == 0x40
    assert info["frames"] == 1


def test_parse_wxgf_refuses_an_unknown_layout():
    data = bytearray(_wxgf(240, 240, 0, 0x40))
    data[6] = 9
    with pytest.raises(wx_emoticon.EmoticonError):
        wx_emoticon.parse_wxgf(bytes(data))


def test_to_image_bytes_passes_a_png_through():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    data, ext = wx_emoticon.to_image_bytes(png)
    assert (data, ext) == (png, "png")


def test_to_image_bytes_recognizes_gif_and_jpeg():
    assert wx_emoticon.to_image_bytes(b"GIF89a" + b"\x00" * 16)[1] == "gif"
    assert wx_emoticon.to_image_bytes(b"\xff\xd8\xff" + b"\x00" * 16)[1] == "jpg"


def test_looks_like_image_rejects_ciphertext():
    assert wx_emoticon.looks_like_image(b"GIF89a...")
    assert not wx_emoticon.looks_like_image(b"\xc7\x10\xe3\x32\x34\xd7\xab\x92")


def test_cache_files_looks_under_the_month_directory(tmp_path):
    month = tmp_path / "cache" / "2026-10" / "Emoticon" / MD5[:2]
    month.mkdir(parents=True)
    target = month / MD5
    target.write_bytes(b"cipher")
    assert wx_emoticon.cache_files(tmp_path, MD5) == [target]


def test_cache_files_also_sees_the_business_store(tmp_path):
    target = tmp_path / "business" / "emoticon" / "Persist" / MD5[:2] / MD5
    target.parent.mkdir(parents=True)
    target.write_bytes(b"cipher")
    assert wx_emoticon.cache_files(tmp_path, MD5) == [target]


def test_image_for_returns_an_existing_decoded_file_without_touching_memory(tmp_path, monkeypatch):
    decoded = tmp_path / f"{MD5}.gif"
    decoded.write_bytes(b"GIF89a" + b"\x00" * 16)
    monkeypatch.setattr(wx_emoticon, "find_cache", lambda name, root=None: None)
    path = wx_emoticon.image_for(MD5, cache_dir=str(tmp_path))
    assert path == str(decoded)


def test_image_for_reports_a_missing_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(wx_emoticon, "find_cache", lambda name, root=None: None)
    with pytest.raises(wx_emoticon.EmoticonError, match="缓存"):
        wx_emoticon.image_for(MD5, cache_dir=str(tmp_path))


def test_image_for_starts_a_scan_instead_of_blocking(tmp_path, monkeypatch):
    source = tmp_path / "cache" / "2026-10" / "Emoticon" / MD5[:2] / MD5
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x00" * 32)
    monkeypatch.setattr(wx_emoticon, "find_cache", lambda name, root=None: source)
    monkeypatch.setattr(wx_emoticon.wx_image_key, "current_wxid", lambda root=None: WXID)
    started = []
    monkeypatch.setattr(
        wx_emoticon, "start_seed_scan", lambda sample, wxid: started.append(wxid) or True
    )
    with pytest.raises(wx_emoticon.EmoticonError, match="扫描"):
        wx_emoticon.image_for(MD5, cache_dir=str(tmp_path / "out"))
    assert started == [WXID]


def test_cache_files_also_sees_the_plain_image_store(tmp_path):
    """图片型表情落在 HttpResource 下，且不按前两位分桶。"""
    target = tmp_path / "cache" / "2026-10" / "HttpResource" / MD5
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert wx_emoticon.cache_files(tmp_path, MD5) == [target]


def test_image_for_passes_a_plaintext_cache_through(tmp_path, monkeypatch):
    """缓存里本来就是明文图片时直接落盘，不去碰内存里的 seed。"""
    source = tmp_path / "cache" / "2026-10" / "HttpResource" / MD5
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    monkeypatch.setattr(wx_emoticon, "find_cache", lambda name, root=None: source)
    monkeypatch.setattr(wx_emoticon.wx_image_key, "current_wxid", lambda root=None: WXID)
    monkeypatch.setattr(
        wx_emoticon,
        "start_seed_scan",
        lambda sample, wxid: pytest.fail("明文图片不该触发密钥扫描"),
    )
    out = tmp_path / "out"
    path = wx_emoticon.image_for(MD5, cache_dir=str(out))
    assert path == str(out / f"{MD5}.png")
    assert Path(path).read_bytes().startswith(b"\x89PNG")


def test_decode_hevc_says_which_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(wx_emoticon, "ffmpeg_path", lambda: tmp_path / "nope" / "ffmpeg.exe")
    with pytest.raises(wx_emoticon.EmoticonError, match="tools.{1,2}ffmpeg"):
        wx_emoticon.decode_hevc(b"\x00" * 64)


def test_decode_hevc_reports_the_decoder_error(monkeypatch, tmp_path):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(wx_emoticon, "ffmpeg_path", lambda: exe)
    monkeypatch.setattr(
        wx_emoticon.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b"", b"bad stream\n"),
    )
    with pytest.raises(wx_emoticon.EmoticonError, match="bad stream"):
        wx_emoticon.decode_hevc(b"\x00" * 64)


def test_decode_hevc_splits_png_frames_from_one_pipe(monkeypatch, tmp_path):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"stub")
    first = io.BytesIO()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(first, format="PNG")
    second = io.BytesIO()
    Image.new("RGB", (8, 6), (30, 20, 10)).save(second, format="PNG")
    payload = first.getvalue() + second.getvalue()
    monkeypatch.setattr(wx_emoticon, "ffmpeg_path", lambda: exe)
    monkeypatch.setattr(
        wx_emoticon.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, payload, b""),
    )
    frames = wx_emoticon.decode_hevc(b"\x00" * 64)
    assert [frame.size for frame in frames] == [(8, 6), (8, 6)]
    assert frames[0].getpixel((0, 0)) == (10, 20, 30)


def test_decode_hevc_reports_a_timeout(monkeypatch, tmp_path):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"stub")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

    monkeypatch.setattr(wx_emoticon, "ffmpeg_path", lambda: exe)
    monkeypatch.setattr(wx_emoticon.subprocess, "run", fake_run)
    with pytest.raises(wx_emoticon.EmoticonError, match="秒"):
        wx_emoticon.decode_hevc(b"\x00" * 64)


def test_image_for_refuses_a_md5_that_is_not_32_hex():
    with pytest.raises(wx_emoticon.EmoticonError, match="md5"):
        wx_emoticon.image_for("abc", cache_dir=".")


def test_wait_for_seed_returns_none_when_nothing_is_scanning(monkeypatch):
    monkeypatch.setattr(wx_emoticon, "_SCAN_THREAD", None)
    assert wx_emoticon.wait_for_seed(WXID, 0.05) is None


def test_wait_for_seed_picks_up_a_finished_scan(monkeypatch):
    """后台扫到的 seed 落进 _SEEDS 后，等待中的那条消息就能直接用它解图。"""
    monkeypatch.setattr(wx_emoticon, "_SEEDS", {})
    thread = threading.Thread(target=lambda: wx_emoticon._SEEDS.__setitem__(WXID, 689159559))
    monkeypatch.setattr(wx_emoticon, "_SCAN_THREAD", thread)
    thread.start()
    assert wx_emoticon.wait_for_seed(WXID, 2.0) == 689159559


def test_image_for_gives_up_after_the_wait_window(tmp_path, monkeypatch):
    """等不到密钥就如实报「等了 N 秒」，调用方据此退回落占位文本。"""
    source = tmp_path / "cache" / "2026-10" / "Emoticon" / MD5[:2] / MD5
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x00" * 32)
    monkeypatch.setattr(wx_emoticon, "find_cache", lambda name, root=None: source)
    monkeypatch.setattr(wx_emoticon.wx_image_key, "current_wxid", lambda root=None: WXID)
    monkeypatch.setattr(wx_emoticon, "start_seed_scan", lambda sample, wxid: True)
    monkeypatch.setattr(wx_emoticon, "wait_for_seed", lambda wxid, timeout: None)
    with pytest.raises(wx_emoticon.EmoticonError, match="等了"):
        wx_emoticon.image_for(MD5, cache_dir=str(tmp_path / "out"), wait=0.1)
