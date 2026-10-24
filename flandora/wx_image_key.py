"""图片解码密钥：从微信进程内存里扫出 AES 密钥。

AES 密钥只在微信**实际解码图片**的那几秒内驻留内存。图片一旦缓存，再打开也不会
重新加载它，所以取密钥的办法只有一种：先让扫描跑起来，再让人在微信里点开一张没
看过的图。bridge 的取钥向导用的就是这个顺序——它把微信实况画面投给用户，让用户
直接在画面上点，扫描在后台同步进行。

XOR 密钥不用扫：它可以从本账号已有图片的尾部反推（末字节 XOR 0xD9 得 FFD9），
这里顺手算好一并写入，省得用户还要再填一个值。

这个模块刻意不导入 config：独立的 tools/imagekey.py 要在用户生成配置之前也能跑，
而 config 在 import 期就会查找 config.json，找不到直接抛异常。
"""

from __future__ import annotations

import ctypes
import glob
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

from . import wechat_account, wx_image

log = logging.getLogger("ob11-bridge")

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
RW_FLAGS = 0x04 | 0x08 | 0x40 | 0x80
SCAN_INTERVAL = 2.0

# 边界锚定的 ASCII 串：避免从长串里截出伪候选
RE_KEY16 = re.compile(rb"(?<![A-Za-z0-9])[A-Za-z0-9]{16}(?![A-Za-z0-9])")
RE_KEY32 = re.compile(rb"(?<![A-Za-z0-9])[A-Za-z0-9]{32}(?![A-Za-z0-9])")


class ImageKeyError(Exception):
    """取图片密钥失败。"""


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
    """默认落盘位置：打包后取 exe 同级的 data/，源码运行时取仓库根下的 data/。

    与 config.data_dir() 的规则一致。这里不直接复用它是为了避开导入期副作用
    （见模块开头）。
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", "wx_image_key.json")


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
    """取任一 V2 文件的 AES 段前 16 字节，作为判定候选密钥的基准。"""
    files = glob.glob(os.path.join(attach, "*", "**", "Img", "*.dat"), recursive=True)
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:300]:
        try:
            with open(f, "rb") as fh:
                head = fh.read(31)
        except OSError:
            continue
        if len(head) >= 31 and head[:6] == wx_image.MAGIC:
            return head[15:31], f
    raise NoSample("本地没有可用的 V2 图片样本，先在微信里收几张图再试")


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


def scan(handle, regions, block: bytes) -> Optional[bytes]:
    """扫一轮，返回能解开基准密文的密钥。"""
    k32 = ctypes.windll.kernel32
    seen: set[bytes] = set()
    for base, size in regions:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(handle, ctypes.c_void_p(base), buf, size, ctypes.byref(got)):
            continue
        data = buf.raw[: got.value]
        for m in RE_KEY32.finditer(data):
            s = m.group()
            for cand in (s[:16], s):
                if cand in seen:
                    continue
                seen.add(cand)
                if _works(cand, block):
                    return cand
        for m in RE_KEY16.finditer(data):
            cand = m.group()
            if cand in seen:
                continue
            seen.add(cand)
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
    deadline = time.time() + seconds
    rounds = 0
    found: Optional[bytes] = None
    try:
        while time.time() < deadline and found is None:
            if stop is not None and stop.is_set():
                break
            rounds += 1
            for pid, h in handles:
                key = scan(h, rw_regions(h), block)
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
    """扫出 AES 密钥、反推 XOR 密钥、写入文件，返回写入的内容。"""
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
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    if on_status:
        on_status(f"XOR 密钥 0x{xor_key:02x}")
        on_status(f"已写入 {path}")
        on_status("这个密钥只对当前账号有效，换账号后要重新取。")
    log.info("图片密钥已保存到 %s（账号 %s）", path, wxid or "未知")
    return payload
