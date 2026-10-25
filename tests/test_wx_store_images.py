"""图片取件：主文件是 wxgf 时改用旁边的明文副本。

微信把一张图存好几份：主文件可能是 HEVC 容器（`wxgf`），高清原图 `_h`、cache 里的
预览 `_b`、缩略图 `_t` 才是普通图片。这里造一棵假账号目录，验证挑副本的优先顺序、
以及没有副本时退回占位符（返回 None）。
"""

import os
import sqlite3
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import wx_store  # noqa: E402

AES_KEY = b"0123456789abcdef"  # 16 字节 ASCII，与真实密钥同形
XOR_KEY = 0x87
TALKER = "talkerhash"
MONTH = "2026-10"
MD5 = "a" * 32

WXGF = b"wxgf\x13\x00\x02\x08" + b"\x11" * 100
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x01" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x02" * 100
PREVIEW = b"\xff\xd8\xff\xdb" + b"\x03" * 100
THUMB = b"\xff\xd8\xff\xc0" + b"\x04" * 100


def _encrypt(key: bytes, data: bytes) -> bytes:
    """用 AES-ECB 加密，给测试造数据用（复用解码器的 CNG 包装反过来调）。"""
    import ctypes

    bcrypt = ctypes.WinDLL("bcrypt")
    h_alg = ctypes.c_void_p()
    h_key = ctypes.c_void_p()
    try:
        wx_store.wx_image._nt(
            bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0), "open"
        )
        mode = ctypes.create_unicode_buffer("ChainingModeECB")
        wx_store.wx_image._nt(
            bcrypt.BCryptSetProperty(
                h_alg, "ChainingMode", ctypes.cast(mode, ctypes.c_void_p), ctypes.sizeof(mode), 0
            ),
            "mode",
        )
        kb = ctypes.create_string_buffer(key, len(key))
        wx_store.wx_image._nt(
            bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0, kb, len(key), 0),
            "key",
        )
        out = ctypes.create_string_buffer(len(data))
        done = ctypes.c_ulong(0)
        wx_store.wx_image._nt(
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


def _make_dat(payload: bytes, aes_plain_len: int = 64) -> bytes:
    """按真实结构造一个 V2 文件：AES 段（PKCS#7）+ 明文中段 + XOR 尾段。"""
    plain_aes = payload[:aes_plain_len]
    enc = _encrypt(AES_KEY, _pkcs7(plain_aes))
    enc_part = bytes(b ^ XOR_KEY for b in payload[aes_plain_len:])
    head = b"\x07\x08V2\x08\x07" + struct.pack("<LL", len(plain_aes), len(enc_part)) + b"\x01"
    return head + enc + enc_part


def _account(tmp_path) -> str:
    """造一棵假账号目录：attach 主文件目录、cache 预览目录、两个空库。"""
    os.makedirs(os.path.join(tmp_path, "msg", "attach", TALKER, MONTH, "Img"))
    os.makedirs(os.path.join(tmp_path, "cache", MONTH, "Message", TALKER, "Bubble"))
    for name in ("hardlink.db", "message_resource.db"):
        con = sqlite3.connect(os.path.join(tmp_path, name))
        con.execute(
            "CREATE TABLE IF NOT EXISTS image_hardlink_info_v4"
            " (md5 TEXT, md5_hash TEXT, file_name TEXT)"
        )
        con.commit()
        con.close()
    return str(tmp_path)


def _store(root: str) -> wx_store.WxStore:
    store = wx_store.WxStore(
        data_dir=root,
        passphrase=b"",
        work_dir=root,
        my_wxid="wxid_self",
        image_key=AES_KEY,
        image_xor_key=XOR_KEY,
    )
    # 测试里这些库本来就是明文，跳过解密
    store._decrypted.update({"hardlink.db", "message_resource.db"})
    return store


def _main_path(root: str) -> str:
    return os.path.join(root, "msg", "attach", TALKER, MONTH, "Img", f"{MD5}.dat")


def _sibling(root: str, suffix: str) -> str:
    return os.path.join(os.path.dirname(_main_path(root)), f"{MD5}{suffix}")


def _preview_path(root: str) -> str:
    return os.path.join(root, "cache", MONTH, "Message", TALKER, "Bubble", f"{MD5}_b.dat")


def _write(path: str, payload: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(payload)


def _extracted_bytes(path: str | None) -> bytes:
    assert path, "本该解出一张图"
    with open(path, "rb") as fh:
        return fh.read()


def test_wxgf_main_falls_back_to_the_high_quality_copy(tmp_path):
    root = _account(tmp_path)
    _write(_main_path(root), _make_dat(WXGF))
    _write(_sibling(root, "_h.dat"), _make_dat(JPEG))
    out = _store(root).extract_image(MD5)
    assert out.endswith(".jpg"), out
    assert _extracted_bytes(out) == JPEG


def test_cache_preview_wins_over_the_thumbnail(tmp_path):
    root = _account(tmp_path)
    _write(_main_path(root), _make_dat(WXGF))
    _write(_preview_path(root), _make_dat(PREVIEW))
    _write(_sibling(root, "_t.dat"), _make_dat(THUMB))
    out = _store(root).extract_image(MD5)
    assert _extracted_bytes(out) == PREVIEW


def test_thumbnail_is_the_last_resort(tmp_path):
    root = _account(tmp_path)
    _write(_main_path(root), _make_dat(WXGF))
    _write(_sibling(root, "_t.dat"), _make_dat(THUMB))
    out = _store(root).extract_image(MD5)
    assert _extracted_bytes(out) == THUMB


def test_wxgf_without_any_copy_returns_none(tmp_path):
    root = _account(tmp_path)
    _write(_main_path(root), _make_dat(WXGF))
    assert _store(root).extract_image(MD5) is None


def test_plain_main_is_used_even_when_a_copy_exists(tmp_path):
    root = _account(tmp_path)
    _write(_main_path(root), _make_dat(PNG))
    _write(_sibling(root, "_h.dat"), _make_dat(JPEG))
    out = _store(root).extract_image(MD5)
    assert out.endswith(".png"), out
    assert _extracted_bytes(out) == PNG


def test_extracted_image_is_cached(tmp_path):
    root = _account(tmp_path)
    _write(_main_path(root), _make_dat(WXGF))
    _write(_sibling(root, "_h.dat"), _make_dat(JPEG))
    store = _store(root)
    first = store.extract_image(MD5)
    # 同一份副本换个内容：落盘名字一样，就不再覆盖（同一张图只解一次）
    _write(_sibling(root, "_h.dat"), _make_dat(JPEG[:5] + b"\x09" * 100))
    assert store.extract_image(MD5) == first
    assert _extracted_bytes(first) == JPEG


OTHER = "b" * 32


def _resource_db(root: str, rows: list[tuple[str, str]]) -> None:
    """造一个最小的 message_resource.db：每一行是 (会话名, 该会话里 local_id=15 的资源 md5)。"""
    con = sqlite3.connect(os.path.join(root, "message_resource.db"))
    con.execute("CREATE TABLE IF NOT EXISTS ChatName2Id (user_name TEXT, update_time INTEGER)")
    con.execute(
        "CREATE TABLE IF NOT EXISTS MessageResourceInfo"
        " (message_id INTEGER, chat_id INTEGER, message_local_id INTEGER, packed_info BLOB)"
    )
    for index, (user_name, md5) in enumerate(rows, start=1):
        con.execute("INSERT INTO ChatName2Id (user_name, update_time) VALUES (?, 0)", (user_name,))
        con.execute(
            "INSERT INTO MessageResourceInfo (message_id, chat_id, message_local_id, packed_info)"
            " VALUES (0, ?, 15, ?)",
            (index, b'\x12"\n ' + md5.encode()),
        )
    con.commit()
    con.close()


def test_resource_lookup_is_scoped_to_the_talker(tmp_path):
    """local_id 只是会话内序号：别的会话的同号资源不能拿来顶替（真机踩过）。"""
    root = _account(tmp_path)
    _resource_db(root, [("other_talker", OTHER), (TALKER, MD5)])
    _write(_main_path(root), _make_dat(JPEG))
    _write(os.path.join(os.path.dirname(_main_path(root)), f"{OTHER}.dat"), _make_dat(PNG))
    out = _store(root).extract_image("f" * 32, 15, talker=TALKER)
    assert out and out.endswith(".jpg"), out
    assert _extracted_bytes(out) == JPEG


def test_missing_talker_disables_the_resource_fallback(tmp_path):
    """拿不到会话就别猜：宁可退回占位符，也不推别的会话的同号资源。"""
    root = _account(tmp_path)
    _resource_db(root, [("other_talker", OTHER)])
    _write(os.path.join(os.path.dirname(_main_path(root)), f"{OTHER}.dat"), _make_dat(PNG))
    assert _store(root).extract_image("f" * 32, 15) is None
