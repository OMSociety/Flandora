"""微信 4.x 本地库解密（SQLCipher 4）。

职责范围：只做「口令 → 可直接用 sqlite3 只读打开的普通文件」这一段。
口令由取钥链路提供（见 wx_key）。

页面布局（SQLCipher 4 默认参数）：
    KDF       PBKDF2-HMAC-SHA512，256000 轮
    加密      AES-256-CBC
    页大小    4096，保留区 80 字节 = IV 16 + HMAC 64
    mac key   PBKDF2-HMAC-SHA512(enc_key, salt XOR 0x3a, 2 轮)
    页 1      开头 16 字节是 salt，不参与加密；其余页整页加密
    页 HMAC   HMAC-SHA512(page[:4032] + 页号小端 u32)，存页尾 64 字节

WAL（每个库都有，最新消息常在里面）：
    帧里的页数据用与主库同一套 enc_key / mac_key；页口令里的页号取帧头 pgno；
    HMAC 一律覆盖 page[0:4032]，主库页 1 那种跳过 salt 的特例在 WAL 里不出现。
    只取到最后一个已提交事务为止的帧，其后的帧属于未提交事务，必须丢弃。

AES 走 Windows CNG（bcrypt.dll），不引入第三方依赖。
"""

import ctypes
import hashlib
import hmac
import logging
import os
import struct
from ctypes import wintypes

log = logging.getLogger("wx_crypto")

MAGIC = b"SQLite format 3\x00"
PAGE = 4096
RESERVE = 80
IV_SZ = 16
KDF_ITER = 256000
SALT_MASK = 0x3A
CT_BODY = PAGE - RESERVE  # 4016：加密正文长度（不含 IV/HMAC）
MAC_END = PAGE - RESERVE + IV_SZ  # 4032：页 HMAC 覆盖到这儿（IV 不参与计算）
WAL_HDR = 32
WAL_FRAME_HDR = 24


class WrongKey(Exception):
    """口令与库不匹配。"""


# --------------------------------------------------------------------------
# AES-256-CBC（Windows CNG）
# --------------------------------------------------------------------------

_BCRYPT = None
_BCRYPT_CHAIN_MODE_CBC = "ChainingModeCBC"
_BCRYPT_AES = "AES"


def _bcrypt():
    global _BCRYPT
    if _BCRYPT is None:
        if not hasattr(ctypes, "WinDLL"):
            raise OSError("解密需要 Windows CNG（bcrypt.dll）")
        _BCRYPT = ctypes.WinDLL("bcrypt")
    return _BCRYPT


def _nt(status, what):
    if status != 0:
        raise OSError(f"{what} 失败，NTSTATUS=0x{status & 0xFFFFFFFF:08x}")


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-CBC 解密，不做填充（入参必须是 16 的整数倍）。"""
    if len(iv) != IV_SZ:
        raise ValueError(f"IV 长度应为 {IV_SZ}，实为 {len(iv)}")
    if not data or len(data) % 16:
        raise ValueError(f"密文长度应为 16 的整数倍，实为 {len(data)}")

    b = _bcrypt()
    h_alg = ctypes.c_void_p()
    h_key = ctypes.c_void_p()
    try:
        _nt(
            b.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), _BCRYPT_AES, None, 0),
            "BCryptOpenAlgorithmProvider",
        )

        mode = ctypes.create_unicode_buffer(_BCRYPT_CHAIN_MODE_CBC)
        _nt(
            b.BCryptSetProperty(
                h_alg, "ChainingMode", ctypes.cast(mode, ctypes.c_void_p), ctypes.sizeof(mode), 0
            ),
            "BCryptSetProperty(ChainingMode)",
        )

        key_buf = ctypes.create_string_buffer(key, len(key))
        _nt(
            b.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0, key_buf, len(key), 0),
            "BCryptGenerateSymmetricKey",
        )

        iv_buf = ctypes.create_string_buffer(iv, IV_SZ)
        out = ctypes.create_string_buffer(len(data))
        done = wintypes.ULONG(0)
        _nt(
            b.BCryptDecrypt(
                h_key, data, len(data), None, iv_buf, IV_SZ, out, len(data), ctypes.byref(done), 0
            ),
            "BCryptDecrypt",
        )
        return out.raw[: done.value]
    finally:
        if h_key:
            b.BCryptDestroyKey(h_key)
        if h_alg:
            b.BCryptCloseAlgorithmProvider(h_alg, 0)


# --------------------------------------------------------------------------
# 密钥派生与页校验
# --------------------------------------------------------------------------


def derive_keys(passphrase: bytes, salt: bytes) -> tuple:
    """由口令与页 1 的 salt 派生 (enc_key, mac_key)。"""
    enc = hashlib.pbkdf2_hmac("sha512", passphrase, salt, KDF_ITER, dklen=32)
    mac_salt = bytes(x ^ SALT_MASK for x in salt)
    mac = hashlib.pbkdf2_hmac("sha512", enc, mac_salt, 2, dklen=32)
    return enc, mac


def page_mac(mac_key: bytes, page: bytes, pgno: int, skip_salt: bool) -> bytes:
    lo = 16 if skip_salt else 0
    h = hmac.new(mac_key, page[lo:MAC_END], hashlib.sha512)
    h.update(struct.pack("<I", pgno))
    return h.digest()


def page_ok(mac_key: bytes, page: bytes, pgno: int, skip_salt: bool) -> bool:
    return hmac.compare_digest(page_mac(mac_key, page, pgno, skip_salt), page[MAC_END:PAGE])


def check_page1(passphrase: bytes, page1: bytes) -> bool:
    """只凭页 1 判断口令对不对——下单库校验收敛到这一个入口。"""
    if len(page1) < PAGE:
        return False
    _, mac = derive_keys(passphrase, page1[:16])
    return page_ok(mac, page1, 1, skip_salt=True)


# --------------------------------------------------------------------------
# 页解密
# --------------------------------------------------------------------------


def _decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    """主库里的整页解密。页 1 跳过开头 16 字节 salt。"""
    iv = page[PAGE - RESERVE : PAGE - RESERVE + IV_SZ]
    if pgno == 1:
        body = MAGIC + aes_cbc_decrypt(enc_key, iv, page[16:CT_BODY])
    else:
        body = aes_cbc_decrypt(enc_key, iv, page[0:CT_BODY])
    return body + b"\x00" * RESERVE


def _decrypt_wal_page(enc_key: bytes, page: bytes) -> bytes:
    """WAL 帧里的整页解密。帧不分页 1，一律整页加密。"""
    iv = page[PAGE - RESERVE : PAGE - RESERVE + IV_SZ]
    return aes_cbc_decrypt(enc_key, iv, page[0:CT_BODY]) + b"\x00" * RESERVE


def read_wal(wal_path: str):
    """解析 WAL，返回 (已提交页字典, 提交后的库大小)；无有效内容时返回 None。

    帧头里的 salt 必须与 WAL 头的 salt 一致才是有效帧。WAL 被复位时 SQLite
    只重写头部、不擦掉后面的旧帧，所以文件尾部常留着一批 salt 对不上的陈帧，
    必须整批丢弃——SQLite 自己也是这么做的，否则会把旧一代的页盖到新库上。

    只取到最后一个已提交事务为止的帧；其后的帧属于未提交事务，同样丢弃。
    """
    try:
        with open(wal_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if len(raw) < WAL_HDR + WAL_FRAME_HDR:
        return None

    magic = struct.unpack(">I", raw[0:4])[0]
    if magic not in (0x377F0682, 0x377F0683):
        log.warning("WAL 头魔数异常 0x%x，按无 WAL 处理", magic)
        return None
    page_size = struct.unpack(">I", raw[8:12])[0]
    if page_size != PAGE:
        log.warning("WAL 页大小 %d 与预期 %d 不符，忽略 WAL", page_size, PAGE)
        return None
    wal_salt = struct.unpack(">II", raw[16:24])

    frame_sz = WAL_FRAME_HDR + PAGE
    nframes = (len(raw) - WAL_HDR) // frame_sz
    frames = []
    last_commit = -1
    ignored = 0
    db_size = None
    for i in range(nframes):
        off = WAL_HDR + i * frame_sz
        if struct.unpack(">II", raw[off + 8 : off + 16]) != wal_salt:
            ignored = nframes - i
            break
        pgno, dbsize = struct.unpack(">II", raw[off : off + 8])
        frames.append((pgno, raw[off + WAL_FRAME_HDR : off + WAL_FRAME_HDR + PAGE]))
        if dbsize:
            last_commit = i
            db_size = dbsize

    if ignored:
        log.debug(
            "%s 有 %d 帧 salt 与 WAL 头不符（WAL 复位后残留的旧帧），已丢弃",
            os.path.basename(wal_path),
            ignored,
        )
    if last_commit < 0:
        log.debug("%s 没有有效帧，按无 WAL 处理", os.path.basename(wal_path))
        return None

    dedup = {}
    for pgno, page in frames[: last_commit + 1]:
        dedup[pgno] = page
    log.debug(
        "WAL %s 有效 %d 帧，覆盖 %d 个页，提交后 %s 页",
        os.path.basename(wal_path),
        last_commit + 1,
        len(dedup),
        db_size,
    )
    return dedup, db_size


# --------------------------------------------------------------------------
# 单库解密
# --------------------------------------------------------------------------


def decrypt_db(
    passphrase: bytes, db_path: str, wal_path: str = None, expected_pages: int = None
) -> bytes:
    """把一个加密库解成完整的普通 SQLite 文件内容（已合并 WAL）。

    expected_pages 用于跨库校验口令：给出该库应有的大致页数，
    不符即判定口令不对，省掉逐库试错。
    """
    with open(db_path, "rb") as f:
        raw = f.read()
    if len(raw) < PAGE:
        raise ValueError(f"{os.path.basename(db_path)} 不足一页")

    page1 = raw[:PAGE]
    salt = page1[:16]
    if not check_page1(passphrase, page1):
        raise WrongKey(f"{os.path.basename(db_path)} 的页 1 校验不过")

    enc_key, mac_key = derive_keys(passphrase, salt)
    npages = len(raw) // PAGE

    pages = {}
    for i in range(npages):
        pgno = i + 1
        page = raw[i * PAGE : (i + 1) * PAGE]
        pages[pgno] = _decrypt_page(enc_key, page, pgno)

    head = pages[1]
    if head[:16] != MAGIC:
        # 页 1 解出来不是 SQLite 头，说明口令或布局判断有误
        raise WrongKey(f"{os.path.basename(db_path)} 页 1 解出的不是 SQLite 头")
    db_size = struct.unpack(">I", head[28:32])[0] or npages
    if expected_pages and abs(db_size - expected_pages) > max(4, expected_pages // 2):
        raise WrongKey(
            f"{os.path.basename(db_path)} 页数 {db_size} 与预期 {expected_pages} 偏离过大"
        )

    if wal_path is None:
        wal_path = db_path + "-wal"
    wal = read_wal(wal_path)
    if wal:
        wal_pages, wal_size = wal
        pages.update({pgno: _decrypt_wal_page(enc_key, p) for pgno, p in wal_pages.items()})
        if wal_size:
            db_size = wal_size
        if pages[1][:16] != MAGIC:
            raise WrongKey(f"{os.path.basename(db_path)} 合并 WAL 后页 1 不再是 SQLite 头")

    out = bytearray()
    for pgno in range(1, db_size + 1):
        page = pages.get(pgno)
        if page is None:
            raise ValueError(
                f"{os.path.basename(db_path)} 第 {pgno} 页在主库与 WAL 里都缺失"
                f"（库大小 {db_size} 页）"
            )
        out += page
    struct.pack_into(">I", out, 28, db_size)
    return bytes(out)


def decrypt_to_file(passphrase: bytes, db_path: str, out_path: str) -> int:
    """解到文件。先写临时文件再原子替换，避免读方看到半截内容。"""
    data = decrypt_db(passphrase, db_path)
    tmp = out_path + ".tmp"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, out_path)
    return len(data)


# --------------------------------------------------------------------------
# 自测
# --------------------------------------------------------------------------


def _selftest(argv):
    if len(argv) >= 2:
        db_path = argv[1]
        key_hex = argv[2] if len(argv) >= 3 else None
    else:
        root = (
            r"D:\WeChatFiles"
            r"\wxid_demouser10001\db_storage"
        )
        db_path = os.path.join(root, "message", "message_0.db")
        key_hex = None

    if key_hex is None:
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in ("key.hex", os.path.join(here, "key.hex")):
            if os.path.isfile(cand):
                key_hex = open(cand, encoding="utf-8").read().strip()
                break
    if not key_hex:
        print("需要口令：python wx_crypto.py <库路径> <key.hex 里的十六进制>")
        return 2
    passphrase = bytes.fromhex(key_hex)

    import sqlite3
    import tempfile
    import time

    t0 = time.time()
    data = decrypt_db(passphrase, db_path)
    t1 = time.time()
    out = os.path.join(tempfile.gettempdir(), "wx_selftest.db")
    with open(out, "wb") as f:
        f.write(data)
    con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    tables = [
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]
    print(f"库      {db_path}")
    print(f"输出    {out}  {len(data):,} B（{len(data) // PAGE} 页）")
    print(f"耗时    {t1 - t0:.2f} s")
    print(f"完整性  {ok}")
    print(f"表      {tables}")
    for t in tables:
        if t.startswith("Msg_"):
            n = con.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
            mx = con.execute(f"SELECT MAX(create_time), MAX(local_id) FROM [{t}]").fetchone()
            print(f"  {t}: {n} 行  最新 create_time={mx[0]} local_id={mx[1]}")
    con.close()
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")
    sys.exit(_selftest(sys.argv))
