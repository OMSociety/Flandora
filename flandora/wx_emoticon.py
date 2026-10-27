"""微信表情（贴纸）缓存：把 ``cache/<月>/{Emoticon,HttpResource}/<md5>`` 还原成能发出去的图片。

微信 4.x 在本地按 ``AES-128-CBC`` 保存贴纸，密钥由账号级 seed 派生：

    key = MD5(f"{seed}{wxid}EMOTICON").digest()      # iv 与 key 相同
    plaintext = AES-128-CBC-decrypt(key, iv=key, ciphertext)   # PKCS#7

解出来的明文有两种：GIF/PNG/JPEG 本体，或 ``wxgf`` 容器（HEVC 帧，需要 PyAV 解码）。
`HttpResource` 下的小图不带加密外壳，读到明文图片直接用。
seed 是 32 位整数，只活在微信进程内存里，重登后可能变，所以按账号缓存、解不开时重扫。
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import struct
import threading
from pathlib import Path

from . import wechat_account, wx_image_key
from .wx_crypto import aes_cbc_decrypt

log = logging.getLogger("flandora")

SUFFIX = "EMOTICON"
RE_DECIMAL = re.compile(rb"(?<!\d)\d{8,12}(?!\d)")
HEVC_START = b"\x00\x00\x00\x01\x40\x01"
MAGICS = (b"\x89PNG", b"GIF8", b"\xff\xd8", b"RIFF", b"wxgf")
CACHE_SUBDIR = "cache"
CACHE_KINDS = ("Emoticon", "HttpResource")
BUSINESS_SUBDIR = os.path.join("business", "emoticon")
FRAME_MS = 100

_SEED_LOCK = threading.Lock()
_SEEDS: dict[str, int] = {}
_SCAN_THREAD: threading.Thread | None = None


class EmoticonError(Exception):
    """表情还原失败；调用方据此退回占位文本。"""


def derive_key(seed: int, wxid: str) -> bytes:
    """按 seed 与账号派生表情密钥（16 字节）。"""
    if not wxid:
        raise EmoticonError("拿不到当前账号的 wxid")
    return hashlib.md5(f"{int(seed)}{wxid}{SUFFIX}".encode()).digest()


def decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-CBC 解密并去掉 PKCS#7 填充；填充不合法时原样返回。"""
    if not ciphertext or len(ciphertext) % 16:
        raise EmoticonError("表情缓存长度不是 16 的倍数")
    plain = aes_cbc_decrypt(key, key, ciphertext)
    pad = plain[-1]
    if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
        return plain[:-pad]
    return plain


def looks_like_image(plain: bytes) -> bool:
    return any(plain.startswith(magic) for magic in MAGICS)


def account_dirs(root: str | None = None) -> list[Path]:
    """当前账号的数据目录优先，其余账号目录随后（表情可能是老账号留下的）。"""
    acct = wechat_account.current_account(root)
    if acct is None or not acct.data_dir:
        return []
    base = Path(acct.data_dir)
    out = [base]
    parent = base.parent
    try:
        for item in parent.iterdir():
            if item.is_dir() and item != base and len(item.name) > 8:
                out.append(item)
    except OSError:
        pass
    return out


def cache_files(account: Path, md5: str) -> list[Path]:
    """按 md5 找出这个账号下所有可能的缓存文件。

    图片型表情落在 `cache/<月>/HttpResource/`（明文），贴纸落在
    `cache/<月>/Emoticon/`（加密），收藏过的还可能在 `business/emoticon/Persist/`。
    """
    name = md5.lower()
    prefix = name[:2]
    out: list[Path] = []
    cache = account / CACHE_SUBDIR
    try:
        months = sorted((p for p in cache.iterdir() if p.is_dir()), reverse=True)
    except OSError:
        months = []
    for month in months:
        for kind in CACHE_KINDS:
            # 贴纸按 md5 前两位分桶，图片型表情直接落在种类目录下，两种布局都认
            for candidate in (month / kind / prefix / name, month / kind / name):
                if candidate.is_file():
                    out.append(candidate)
    business = account / BUSINESS_SUBDIR / "Persist" / prefix / name
    if business.is_file():
        out.append(business)
    return out


def find_cache(md5: str, root: str | None = None) -> Path | None:
    for account in account_dirs(root):
        files = cache_files(account, md5)
        if files:
            return files[0]
    return None


def read_seed_region(handle, regions) -> list[int]:
    """从可写内存里挑出 8–12 位十进制数当 seed 候选。"""
    import ctypes

    k32 = ctypes.windll.kernel32
    out: list[int] = []
    for base, size in regions:
        buffer = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t()
        ok = k32.ReadProcessMemory(handle, ctypes.c_void_p(base), buffer, size, ctypes.byref(read))
        if not ok:
            continue
        for match in RE_DECIMAL.finditer(buffer.raw[: read.value]):
            try:
                out.append(int(match.group()))
            except ValueError:
                continue
    return out


def find_seed(sample: Path, wxid: str) -> int:
    """扫微信内存找能解开 sample 的 seed。"""
    ciphertext = sample.read_bytes()
    handles = wx_image_key.open_weixin()
    try:
        seen: set[int] = set()
        for _pid, handle in handles:
            for seed in read_seed_region(handle, wx_image_key.rw_regions(handle)):
                if seed in seen:
                    continue
                seen.add(seed)
                try:
                    plain = decrypt(ciphertext, derive_key(seed, wxid))
                except Exception:  # noqa: BLE001 - 候选解错很正常
                    continue
                if looks_like_image(plain):
                    return seed
    finally:
        wx_image_key.close_handles(handles)
    raise EmoticonError("扫不到表情密钥：先打开一次表情所在的会话，再重试")


def seed_for(wxid: str, sample: Path) -> int:
    """取当前账号的 seed：先验内存里缓存的那个，失效再扫。"""
    with _SEED_LOCK:
        cached = _SEEDS.get(wxid)
    if cached is not None:
        try:
            if looks_like_image(decrypt(sample.read_bytes(), derive_key(cached, wxid))):
                return cached
        except Exception:  # noqa: BLE001 - 重登后旧 seed 失效，重扫
            pass
    seed = find_seed(sample, wxid)
    with _SEED_LOCK:
        _SEEDS[wxid] = seed
    log.info("[表情] 已取到当前账号的表情密钥（seed=%s）", seed)
    return seed


def cached_seed(wxid: str) -> int | None:
    with _SEED_LOCK:
        return _SEEDS.get(wxid)


def start_seed_scan(sample: Path, wxid: str) -> bool:
    """在后台扫一次 seed；已经在扫或已扫到就返回 False。"""
    global _SCAN_THREAD
    with _SEED_LOCK:
        if _SCAN_THREAD is not None and _SCAN_THREAD.is_alive():
            return False
        if wxid in _SEEDS:
            return False

        def run() -> None:
            try:
                seed_for(wxid, sample)
            except Exception as exc:  # noqa: BLE001 - 扫不到只影响表情
                log.info("[表情] 还没扫到表情密钥：%s", exc)

        _SCAN_THREAD = threading.Thread(target=run, name="emoticon-seed", daemon=True)
        _SCAN_THREAD.start()
    return True


def parse_wxgf(plain: bytes) -> dict:
    """拆 wxgf 头：画布宽高、帧数、类型与 HEVC 起点。"""
    if not plain.startswith(b"wxgf") or len(plain) < 20:
        raise EmoticonError("不是 wxgf 容器")
    version = plain[6]
    if version not in (0, 2):
        raise EmoticonError(f"未知的 wxgf 布局版本 {version}")
    width = struct.unpack_from(">H", plain, 7)[0]
    height = struct.unpack_from(">H", plain, 9)[0]
    extra = struct.unpack_from(">H", plain, 11)[0]
    kind_at = 15 if version == 0 else 16
    kind = plain[kind_at]
    start = plain.find(HEVC_START)
    if start < 0:
        raise EmoticonError("wxgf 里没有 HEVC 数据")
    return {
        "width": width,
        "height": height,
        "frames": extra + 1,
        "kind": kind,
        "hevc": plain[start:],
    }


def decode_hevc(hevc: bytes) -> list:
    """用 PyAV 把 HEVC 码流解成 PIL 帧。

    `wxgf` 里的码流只带 VPS/SPS/PPS 与一个 IDR 片，按裸码流喂给 CodecContext
    一帧也解不出来；走 `hevc` 解复用器才认这套参数集与片头。
    """
    try:
        import av
    except Exception as exc:  # noqa: BLE001 - 没装解码器时退回占位文本
        raise EmoticonError(f"没有 HEVC 解码器：{exc}") from exc
    frames = []
    try:
        with av.open(io.BytesIO(hevc), format="hevc") as container:
            for frame in container.decode(video=0):
                frames.append(frame.to_image())
    except EmoticonError:
        raise
    except Exception as exc:  # noqa: BLE001 - 坏码流按解析失败处理
        raise EmoticonError(f"HEVC 解码失败：{exc}") from exc
    if not frames:
        raise EmoticonError("HEVC 里没有解出帧")
    return frames


def to_image_bytes(plain: bytes) -> tuple[bytes, str]:
    """明文 → 可直接发出去的图片字节与扩展名。"""
    if plain.startswith(b"\x89PNG") or plain.startswith(b"GIF8") or plain.startswith(b"\xff\xd8"):
        ext = (
            "png"
            if plain.startswith(b"\x89PNG")
            else ("gif" if plain.startswith(b"GIF8") else "jpg")
        )
        return plain, ext
    if plain.startswith(b"RIFF"):
        return plain, "webp"
    info = parse_wxgf(plain)
    frames = decode_hevc(info["hevc"])
    buffer = io.BytesIO()
    if len(frames) == 1:
        frames[0].save(buffer, format="PNG")
        return buffer.getvalue(), "png"
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
    )
    return buffer.getvalue(), "gif"


def image_for(md5: str, *, root: str | None = None, cache_dir: str | None = None) -> str:
    """把一条表情消息还原成图片文件路径；失败抛 EmoticonError。"""
    from . import config

    name = (md5 or "").lower()
    if len(name) != 32:
        raise EmoticonError("消息里没有可用的表情 md5")
    wxid = wx_image_key.current_wxid(root)
    target_dir = Path(cache_dir or os.path.join(config.data_dir(), "emoticon"))
    for ext in ("gif", "png", "jpg", "webp"):
        cached = target_dir / f"{name}.{ext}"
        if cached.is_file() and cached.stat().st_size > 0:
            return str(cached)
    source = find_cache(name, root)
    if source is None:
        raise EmoticonError("本地没有这个表情的缓存文件")
    raw = source.read_bytes()
    if looks_like_image(raw):
        # 小表情（缩略图一类）在缓存里就是明文图片，没有加密外壳
        data, ext = to_image_bytes(raw)
        target_dir.mkdir(parents=True, exist_ok=True)
        out = target_dir / f"{name}.{ext}"
        out.write_bytes(data)
        return str(out)
    seed = cached_seed(wxid)
    if seed is None:
        # 扫内存要十几秒，放到后台；这一条先按占位文本上报，扫到后自动生效
        if start_seed_scan(source, wxid):
            log.info("[表情] 正在扫当前账号的表情密钥，扫到后表情会自动变成图片")
        raise EmoticonError("表情密钥还在扫描")
    plain = decrypt(raw, derive_key(seed, wxid))
    data, ext = to_image_bytes(plain)
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"{name}.{ext}"
    out.write_bytes(data)
    return str(out)
