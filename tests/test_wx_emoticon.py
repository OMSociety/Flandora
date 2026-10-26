"""表情缓存解密链路的单元测试（不碰真微信、不读内存）。"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

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


def test_image_for_refuses_a_md5_that_is_not_32_hex():
    with pytest.raises(wx_emoticon.EmoticonError, match="md5"):
        wx_emoticon.image_for("abc", cache_dir=".")
