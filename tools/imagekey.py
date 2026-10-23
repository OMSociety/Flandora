"""取图片解码密钥：从微信进程内存里扫出 AES 密钥。

为什么必须单独一个工具、且必须配合人操作：AES 密钥只在微信**实际解码图片**的
那几秒内驻留内存。图片一旦缓存，再打开也不会重新加载密钥。所以流程是：

    1. 起本工具（它开始持续扫描）
    2. 用户在微信里打开一个没缓存过的会话 / 点开一张图
    3. 工具扫到密钥，写成 wx_image_key.json

XOR 密钥不用扫：它可以从本账号已有图片的尾部反推（末字节 XOR 0xD9 得 FFD9），
这里顺手算好一并写入，省得用户还要再填一个值。

用法：
    python tools/imagekey.py --out <配置目录>            # 交互，等用户打开图片
    python tools/imagekey.py --out <dir> --seconds 300   # 限定等待时长
"""
from __future__ import annotations

import argparse
import ctypes
import glob
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

_BRIDGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IMPL_ROOT = os.path.join(_BRIDGE_ROOT, "bridge")
for _p in (_IMPL_ROOT, _BRIDGE_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import wx_image  # noqa: E402

EXIT_OK = 0
EXIT_NOT_FOUND = 2

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
RW_FLAGS = 0x04 | 0x08 | 0x40 | 0x80
SCAN_INTERVAL = 2.0

# 边界锚定的 ASCII 串：与参考实现一致，避免从长串里截出伪候选
RE_KEY16 = re.compile(rb"(?<![A-Za-z0-9])[A-Za-z0-9]{16}(?![A-Za-z0-9])")
RE_KEY32 = re.compile(rb"(?<![A-Za-z0-9])[A-Za-z0-9]{32}(?![A-Za-z0-9])")


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]


def weixin_pids() -> list[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="ignore",
                         creationflags=0x08000000).stdout
    pids = []
    for line in out.splitlines():
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2 and parts[1].isdigit():
            pids.append(int(parts[1]))
    return pids


def attach_dir() -> str:
    """当前登录账号的图片目录。"""
    import wechat_account
    acct = wechat_account.current_account()
    if acct is None or not acct.data_dir:
        raise SystemExit("找不到已登录的微信账号，请先登录微信")
    return os.path.join(acct.data_dir, "msg", "attach")


def sample_block(attach: str) -> tuple[bytes, str]:
    """取任一 V2 文件的 AES 段前 16 字节，作为判定基准。"""
    files = glob.glob(os.path.join(attach, "*", "**", "Img", "*.dat"), recursive=True)
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:300]:
        try:
            with open(f, "rb") as fh:
                head = fh.read(31)
        except OSError:
            continue
        if len(head) >= 31 and head[:6] == wx_image.MAGIC:
            return head[15:31], f
    raise SystemExit("本地没有可用的 V2 图片样本，先在微信里收几张图再试")


def rw_regions(handle) -> list[tuple[int, int]]:
    k32 = ctypes.windll.kernel32
    out = []
    addr = 0
    while addr < 0x7FFFFFFFFFFF:
        mbi = MBI()
        if not k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi),
                                  ctypes.sizeof(mbi)):
            break
        nxt = addr + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
        if (mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS
                and not (mbi.Protect & PAGE_GUARD)
                and (mbi.Protect & RW_FLAGS)
                and mbi.RegionSize <= 64 * 1024 * 1024):
            out.append((mbi.BaseAddress, mbi.RegionSize))
    return out


def scan(handle, regions, block: bytes) -> bytes | None:
    """扫一轮，返回能解开基准密文的密钥。"""
    k32 = ctypes.windll.kernel32
    seen: set[bytes] = set()
    for base, size in regions:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(handle, ctypes.c_void_p(base), buf, size,
                                     ctypes.byref(got)):
            continue
        data = buf.raw[:got.value]
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
    files = glob.glob(os.path.join(attach, "*", "**", "Img", "*.dat"), recursive=True)
    return wx_image.sniff_xor_key(files, b"")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从微信进程取图片解码密钥")
    parser.add_argument("--out", required=True,
                        help="输出目录，密钥写到该目录下的 wx_image_key.json")
    parser.add_argument("--seconds", type=float, default=600.0,
                        help="持续扫描的秒数上限")
    args = parser.parse_args(argv)

    attach = attach_dir()
    block, src = sample_block(attach)
    print(f"判定样本：{os.path.basename(src)}")
    print("=" * 62)
    print("接下来请在微信里打开一个新会话、或双击一张没看过的图片。")
    print("密钥只在微信解码图片的那几秒存在，所以扫描会一直跑着等你。")
    print("=" * 62)

    pids = weixin_pids()
    if not pids:
        print("微信没有在运行")
        return EXIT_NOT_FOUND
    k32 = ctypes.windll.kernel32
    handles = []
    for pid in pids:
        h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if h:
            handles.append((pid, h))

    deadline = time.time() + args.seconds
    rounds = 0
    found = None
    try:
        while time.time() < deadline and not found:
            rounds += 1
            for pid, h in handles:
                key = scan(h, rw_regions(h), block)
                if key:
                    found = key
                    print(f"\n找到 AES 密钥（pid={pid}）：{key.decode('ascii')}")
                    break
                print(f"  第 {rounds} 轮 pid={pid} …", end="\r", flush=True)
            if not found:
                time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        print("\n已中断")
    finally:
        for _pid, h in handles:
            try:
                k32.CloseHandle(h)
            except Exception:
                pass

    if not found:
        print(f"\n扫了 {rounds} 轮仍未找到。多半是图片已经缓存过——换一个没打开过的会话再试。")
        return EXIT_NOT_FOUND

    xor_key = sniff_xor(attach)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "wx_image_key.json")
    payload = {"aes_key": found.decode("ascii"), "xor_key": xor_key}

    import wechat_account
    acct = wechat_account.current_account()
    if acct is not None:
        payload["wxid"] = acct.wxid
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"XOR 密钥：0x{xor_key:02x}")
    print(f"已写入 {path}")
    print("这个密钥只对当前账号有效，换账号后要重新取。")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
