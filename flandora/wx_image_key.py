"""图片解码密钥：从微信进程内存里扫出 AES 密钥。

AES 密钥只在微信**实际解码图片**的那几秒内驻留内存。图片一旦缓存，再打开也不会
重新加载它，所以取密钥的办法只有一种：先让扫描跑起来，再让人在微信里点开一张没
看过的图。bridge 的取钥向导用的就是这个顺序——它把微信实况画面投给用户，让用户
直接在画面上点，扫描在后台同步进行。

XOR 密钥不用扫：它可以从本账号已有图片的尾部反推（末字节 XOR 0xD9 得 FFD9），
这里顺手算好一并写入，省得用户还要再填一个值。

这个模块只按需惰性导入 ``config.paths``：独立的 tools/imagekey.py 要在用户生成配置
之前也能跑，而那条路径不该牵动配置加载与日志装配。
"""

from __future__ import annotations

import base64
import ctypes
import glob
import json
import logging
import os
import re
import subprocess
import threading
import time
import zlib
from ctypes import wintypes
from typing import Callable, Optional

from . import wechat_account, wx_image
from .wxkey import store as wxkey_store

log = logging.getLogger("flandora")

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
RW_FLAGS = 0x04 | 0x08 | 0x40 | 0x80
SCAN_INTERVAL = 0.5

# 判定样本的选定结果按 attach 目录缓存：attach 树很大，每次重选都要 stat 一遍。
_SAMPLE_CACHE: dict[str, tuple[bytes, str]] = {}

# 候选提取：先按「连续字母数字串」切，再只对落单的 16/32 字符串试解密。
# 带前后锚的正则要 11 秒才扫完 366 MB，换成这里的连续串匹配是 3 秒；密钥只在解码
# 那几秒驻留内存，一轮扫描的耗时直接决定能不能撞上，所以这里用快的。
RE_RUN = re.compile(rb"[A-Za-z0-9]{16,}")


class ImageKeyError(Exception):
    """取图片密钥失败。"""


class ImageKeyCancelled(ImageKeyError):
    """用户主动停止了取钥。"""


class WeChatNotRunning(ImageKeyError):
    """微信没在运行。"""


class NoSample(ImageKeyError):
    """本地没有可用的 V2 图片样本，判不出候选密钥对错。"""


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def default_key_file() -> str:
    """默认落盘位置：数据目录下的 ``wx_image_key.json``，规则与配置模块一致。"""
    from .config.paths import default_data_dir

    return os.path.join(default_data_dir(), "wx_image_key.json")


def seal_aes_key(aes_key: bytes) -> str:
    """把 AES 密钥用当前 Windows 账户的 DPAPI 凭据封起来。

    与数据库口令（``wx_key.dat``）用同一套保护：同机同账户才解得开，磁盘上的
    其它程序读不走明文密钥。
    """
    return base64.b64encode(wxkey_store.protect(aes_key)).decode("ascii")


def open_aes_key(sealed: str) -> bytes:
    """解开 :func:`seal_aes_key` 写的密文；解不开就返回 ``b""``（按没有密钥处理）。"""
    if not sealed:
        return b""
    try:
        return wxkey_store.unprotect(base64.b64decode(sealed, validate=True))
    except Exception as exc:  # noqa: BLE001 - 解不开等于没有密钥，不该拦住文本通道
        log.warning("图片 AES 密钥解不开（%s）：它是在别的 Windows 账户或别的机器上取的", exc)
        return b""


def parse_aes_key_text(raw: str) -> bytes:
    """把密钥文本解析成 16 字节：32 位十六进制与 16 个 ASCII 字符两种写法都接受。"""
    text = raw.strip()
    if len(text) == 32:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    if len(text) == 16 and text.isascii():
        return text.encode("ascii")
    log.warning("图片 AES 密钥格式不对：应为 32 位十六进制或 16 个 ASCII 字符，实为 %r", text[:40])
    return b""


def aes_key_from_payload(payload: dict) -> bytes:
    """从落盘的字典里取出 AES 密钥：DPAPI 密文优先，其次手填的明文字段。

    取钥向导写的是 ``aes_key_dpapi``（同机同账户才解得开）；手工编辑密钥文件时也可以
    直接写明文 ``aes_key``。两处读密钥的地方（收件源与向导的试解）走同一条规则，
    免得一处认、一处不认。
    """
    sealed = str(payload.get("aes_key_dpapi") or "")
    if sealed:
        key = open_aes_key(sealed)
        if key:
            return key
    return parse_aes_key_text(str(payload.get("aes_key") or ""))


def current_wxid(root: Optional[str] = None) -> str:
    acct = wechat_account.current_account(root)
    return acct.wxid if acct is not None else ""


def weixin_pids() -> list[int]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        errors="ignore",
        creationflags=0x08000000,
    ).stdout
    pids = []
    for line in out.splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def attach_dir(root: Optional[str] = None) -> str:
    """当前登录账号的图片目录。"""
    acct = wechat_account.current_account(root)
    if acct is None or not acct.data_dir:
        raise ImageKeyError("找不到已登录的微信账号，请先登录微信")
    return os.path.join(acct.data_dir, "msg", "attach")


def sample_block(attach: str) -> tuple[bytes, str]:
    """取任一 V2 文件的 AES 段前 16 字节，作为判定候选密钥的基准。

    只在最近几个月的目录里找、并记住上一次选中的文件：整个 attach 树有十万个
    文件，逐个 stat 一遍要十几秒，而向导一启动就要拿它判「已有密钥还能不能用」。
    """
    cached = _SAMPLE_CACHE.get(attach)
    if cached is not None and os.path.isfile(cached[1]):
        return cached[0], cached[1]
    for f in _recent_image_files(attach):
        try:
            with open(f, "rb") as fh:
                head = fh.read(31)
        except OSError:
            continue
        if len(head) >= 31 and head[:6] == wx_image.MAGIC:
            _SAMPLE_CACHE[attach] = (head[15:31], f)
            return head[15:31], f
    raise NoSample("本地没有可用的 V2 图片样本，先在微信里收几张图再试")


def _recent_image_files(attach: str, keep: int = 600) -> list[str]:
    """按月份目录名倒序列出最近的图片文件，不必遍历整棵树。"""
    months: list[str] = []
    try:
        for acct in os.scandir(attach):
            if not acct.is_dir():
                continue
            try:
                for month in os.scandir(acct.path):
                    if month.is_dir():
                        months.append(month.path)
            except OSError:
                continue
    except OSError:
        return []
    months.sort(reverse=True)
    out: list[str] = []
    for path in months[:6]:
        try:
            with os.scandir(os.path.join(path, "Img")) as it:
                files = [e.path for e in it if e.name.endswith(".dat")]
        except OSError:
            continue
        files.sort(key=_mtime, reverse=True)
        out.extend(files)
        if len(out) >= keep:
            break
    return out[:keep]


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def rw_regions(handle) -> list[tuple[int, int]]:
    k32 = ctypes.windll.kernel32
    out = []
    addr = 0
    while addr < 0x7FFFFFFFFFFF:
        mbi = MBI()
        if not k32.VirtualQueryEx(
            handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)
        ):
            break
        nxt = addr + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
        if (
            mbi.State == MEM_COMMIT
            and mbi.Protect != PAGE_NOACCESS
            and not (mbi.Protect & PAGE_GUARD)
            and (mbi.Protect & RW_FLAGS)
            and mbi.RegionSize <= 64 * 1024 * 1024
        ):
            out.append((mbi.BaseAddress, mbi.RegionSize))
    return out


def candidates(data: bytes):
    """从一块内存里切出可能的密钥串：落单的 16 字符，或 32 字符的前一半。

    32 个连续字母数字更可能是「16 字节密钥 + 相邻噪声」，所以取前 16 个字符试；
    更长的连续串里即使嵌着密钥也切不出来，与按边界锚定的取法一致（实测密钥是
    16 字节的独立 ASCII 串）。
    """
    for match in RE_RUN.finditer(data):
        run = match.group()
        size = len(run)
        if size == 16:
            yield run
        elif size == 32:
            yield run[:16]


def scan(
    handle,
    regions,
    block: bytes,
    seen: Optional[set] = None,
    memorized: Optional[dict] = None,
) -> Optional[bytes]:
    """扫一轮，返回能解开基准密文的密钥。

    区域按地址从高到低扫：刚解码的图会在堆顶附近新分配缓冲区，命中通常落在前面
    几十 MB 内，比按地址升序快得多。

    ``seen`` 跨轮复用：同一段内存里同样的串不必每轮重试一次 AES。``memorized``
    记录每块内存上一轮的校验和，内容没变就整块跳过——一轮的开销因此从「读满
    500 MB 再切候选」降到「算校验和 + 只处理变过的块」，否则一轮要四五秒，赶不上
    解码那几秒。
    """
    k32 = ctypes.windll.kernel32
    tried = seen if seen is not None else set()
    for base, size in sorted(regions, key=lambda r: -r[0]):
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(handle, ctypes.c_void_p(base), buf, size, ctypes.byref(got)):
            continue
        data = buf.raw[: got.value]
        if memorized is not None:
            stamp = zlib.crc32(data)
            if memorized.get(base) == stamp:
                continue
            memorized[base] = stamp
        for cand in candidates(data):
            if cand in tried:
                continue
            tried.add(cand)
            if _works(cand, block):
                return cand
    return None


def _works(key: bytes, block: bytes) -> bool:
    if len(key) not in (16, 24, 32):
        return False
    try:
        plain = wx_image.aes_ecb_decrypt(key, block)
    except Exception:
        return False
    return any(plain.startswith(m) for m, _ in wx_image._MAGICS)


def sniff_xor(attach: str) -> int:
    """从已有图片的尾部反推 XOR 密钥。"""
    files = glob.glob(os.path.join(attach, "*", "**", "Img", "*.dat"), recursive=True)
    return wx_image.sniff_xor_key(files, b"")


def open_weixin() -> list[tuple[int, int]]:
    """打开所有微信进程的读句柄。调用方负责用 close_handles() 收尾。"""
    pids = weixin_pids()
    if not pids:
        raise WeChatNotRunning("微信没有在运行")
    k32 = ctypes.windll.kernel32
    handles = []
    for pid in pids:
        h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if h:
            handles.append((pid, h))
    if not handles:
        raise ImageKeyError("打不开微信进程的读句柄（权限不足？）")
    return handles


def close_handles(handles) -> None:
    k32 = ctypes.windll.kernel32
    for _pid, h in handles:
        try:
            k32.CloseHandle(h)
        except Exception:
            pass


def scan_for_key(
    *,
    seconds: float = 600.0,
    sample_from: Optional[str] = None,
    stop: Optional[threading.Event] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_round: Optional[Callable[[int], None]] = None,
) -> bytes:
    """一直扫到找到密钥、超时或被 stop 叫停。阻塞，调用方自己放进线程。"""
    say = on_status or (lambda _t: None)
    attach = sample_from or attach_dir()
    block, src = sample_block(attach)
    say(f"判定样本：{os.path.basename(src)}")
    handles = open_weixin()
    # 解码发生在最大的那个进程里（主进程），先扫它，命中率更高
    ordered = sorted(handles, key=lambda ph: -sum(size for _b, size in rw_regions(ph[1])))
    deadline = time.time() + seconds
    rounds = 0
    found: Optional[bytes] = None
    seen: set[bytes] = set()
    memos: dict[int, dict[int, int]] = {}
    try:
        while time.time() < deadline and found is None:
            if stop is not None and stop.is_set():
                break
            rounds += 1
            for pid, h in ordered:
                key = scan(h, rw_regions(h), block, seen, memos.setdefault(pid, {}))
                if key:
                    found = key
                    say(f"找到 AES 密钥（pid={pid}）")
                    break
            if on_round:
                on_round(rounds)
            if found is None:
                time.sleep(SCAN_INTERVAL)
    finally:
        close_handles(handles)

    if found is None:
        if stop is not None and stop.is_set():
            raise ImageKeyCancelled("已取消取钥")
        raise ImageKeyError(
            f"扫了 {rounds} 轮仍未找到。多半是这张图已经缓存过——"
            "换一个没打开过的会话、或双击一张没看过的图片再试。"
        )
    return found


def grab_key(
    *,
    out_path: Optional[str] = None,
    seconds: float = 600.0,
    sample_from: Optional[str] = None,
    root: Optional[str] = None,
    stop: Optional[threading.Event] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_round: Optional[Callable[[int], None]] = None,
) -> dict:
    """扫出 AES 密钥、反推 XOR 密钥、写入文件，返回本次取到的密钥。

    落盘的是 DPAPI 密文（``aes_key_dpapi``），与数据库口令用同一套保护；返回的字典里
    仍带明文 ``aes_key``，供向导与控制台现场显示——明文只留在内存里。
    """
    attach = sample_from or attach_dir(root)
    aes_key = scan_for_key(
        seconds=seconds, sample_from=attach, stop=stop, on_status=on_status, on_round=on_round
    )
    xor_key = sniff_xor(attach)
    wxid = current_wxid(root)
    path = out_path or default_key_file()

    payload = {"aes_key": aes_key.decode("ascii"), "xor_key": xor_key}
    if wxid:
        payload["wxid"] = wxid
    stored = {"aes_key_dpapi": seal_aes_key(aes_key), "xor_key": xor_key}
    if wxid:
        stored["wxid"] = wxid
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(stored, fh, ensure_ascii=False, indent=2)

    if on_status:
        on_status(f"XOR 密钥 0x{xor_key:02x}")
        on_status(f"已写入 {path}")
        on_status("这个密钥只对当前账号有效，换账号后要重新取。")
    log.info("图片密钥已保存到 %s（账号 %s）", path, wxid or "未知")
    return payload
