"""把截获到的内存片段还原成微信数据库口令。

截获的片段是按现场指针读出来的原始内存，口令可能落在任何 4 字节对齐的位置。
这里对每个片段做全偏移枚举，命中与否由数据库页 1 的 HMAC 判定：只有真正能解
开该数据库的那段字节会被认下来，所以不存在"猜出来但解不开"的口令。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Sequence

try:
    from .. import wx_crypto
except ImportError:  # 从包外单独跑时，把包目录加进来
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from .. import wx_crypto

log = logging.getLogger("wxkey.recover")

#: 微信的口令是 32 字节，所以 32 排在最前——每个偏移先试它，绝大多数命中都在这一步。
LEN_CANDIDATES = (32, 24, 16, 48, 64)
ALIGN = 4
DEFAULT_WORKERS = min(8, os.cpu_count() or 4)


@dataclass(frozen=True)
class Hit:
    """一次命中的全部取证信息。"""

    passphrase: bytes
    source: str
    tried: int
    seconds: float

    def describe(self) -> str:
        return (
            f"口令 {self.passphrase.hex()}（{len(self.passphrase)} 字节）"
            f" 来自 {self.source}，共试 {self.tried} 个候选，耗时 {self.seconds:.1f} 秒"
        )


def page1_of(db_path: str | Path) -> bytes:
    """读加密数据库的第一页，用来当校验基准。"""
    with open(db_path, "rb") as fh:
        page = fh.read(wx_crypto.PAGE)
    if len(page) < wx_crypto.PAGE:
        raise ValueError(f"数据库不足一页，无法校验：{db_path}")
    return page


def load_capture(path: str | Path) -> list[tuple[str, bytes]]:
    """读注入端写出的 JSONL，返回 (来源标记, 原始字节) 列表。"""
    blobs: list[tuple[str, bytes]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                log.warning("跳过无法解析的行：%r", line[:80])
                continue
            if rec.get("type") != "blob":
                continue
            try:
                data = bytes.fromhex(rec.get("hex", ""))
            except ValueError:
                log.warning("跳过十六进制非法的片段：%s", rec.get("tag"))
                continue
            if data:
                tag = rec.get("tag", "?")
                where = rec.get("where", "?")
                blobs.append((f"{tag}@{where}", data))
    return blobs


def iter_candidates(
    blobs: Sequence[tuple[str, bytes]], lengths: Sequence[int] = LEN_CANDIDATES
) -> Iterator[tuple[str, bytes]]:
    """按"最像口令"的顺序吐出候选值。

    分三档，前一档跑完才进下一档：

    1. 解引用片段开头的 32 字节。实测口令就落在 ``keyData[8]`` 所指缓冲区的
       第 0 字节，这一档通常第一个候选即命中；
    2. 其余解引用片段与主缓冲区；
    3. 未解引用的原始片段。

    每档内部再从偏移 0 起逐个偏移枚举，保证不会漏掉换版本后偏移变化的情形。

    同一轮里多个指针槽常常指向同一块缓冲区，候选值按内容去重：实测一次登录
    截获 68 段片段、42432 个候选，去重后 34466 个——每个候选都要跑 256000 轮
    PBKDF2（单次约 144 ms），删掉重复值是这一档最省时间的动作。
    """
    seen_blobs: set[bytes] = set()
    seen_values: set[bytes] = set()

    def unique(data: bytes) -> bool:
        if data in seen_blobs:
            return False
        seen_blobs.add(data)
        return True

    deref = [(s, d) for s, d in blobs if "deref" in s and unique(d)]
    plain = [(s, d) for s, d in blobs if "deref" not in s and unique(d)]

    def emit(source: str, data: bytes, offsets) -> Iterator[tuple[str, bytes]]:
        for off in offsets:
            for length in lengths:
                if off + length > len(data):
                    continue
                value = data[off : off + length]
                if value in seen_values:
                    continue
                seen_values.add(value)
                yield f"{source}+{off}x{length}", value
                stripped = value.rstrip(b"\x00")
                if len(stripped) >= 16 and stripped != value:
                    if stripped not in seen_values:
                        seen_values.add(stripped)
                        yield f"{source}+{off}x{length}[strip]", stripped

    for source, data in deref:
        yield from emit(source, data, [0])

    for source, data in deref:
        yield from emit(source, data, range(ALIGN, max(ALIGN, len(data) - 15), ALIGN))

    for source, data in plain:
        yield from emit(source, data, range(0, max(ALIGN, len(data) - 15), ALIGN))


def _probe(task: tuple[str, bytes, bytes]) -> tuple[str, bytes, bool]:
    source, value, page1 = task
    try:
        return source, value, wx_crypto.check_page1(value, page1)
    except Exception:  # 长度不合法的候选直接算未命中
        return source, value, False


def find_passphrase(
    blobs_or_path,
    db_or_page1,
    *,
    workers: int = DEFAULT_WORKERS,
    deadline_s: Optional[float] = None,
) -> Optional[Hit]:
    """在截获片段里找能解开指定数据库的口令。

    ``blobs_or_path`` 可以是 JSONL 路径或 ``load_capture()`` 的返回值；
    ``db_or_page1`` 可以是加密数据库路径或它的一页原始字节。
    """
    blobs = (
        load_capture(blobs_or_path)
        if isinstance(blobs_or_path, (str, Path))
        else list(blobs_or_path)
    )
    page1 = page1_of(db_or_page1) if isinstance(db_or_page1, (str, Path)) else bytes(db_or_page1)
    if not blobs:
        log.error("没有任何截获片段，无法还原口令")
        return None

    started = time.time()
    tried = 0
    width = max(1, workers) * 8
    total = len(blobs)

    def expired() -> bool:
        return deadline_s is not None and time.time() - started > deadline_s

    log.info("开始还原：%d 段片段，%d 个并行工作线程", total, workers)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        batch: list[tuple[str, bytes, bytes]] = []
        for source, value in iter_candidates(blobs):
            batch.append((source, value, page1))
            if len(batch) < width:
                continue
            for src, val, ok in pool.map(_probe, batch):
                tried += 1
                if ok:
                    return Hit(val, src, tried, time.time() - started)
            batch.clear()
            if expired():
                log.warning("到达时间上限，已试 %d 个候选仍未命中", tried)
                return None
        if batch:
            for src, val, ok in pool.map(_probe, batch):
                tried += 1
                if ok:
                    return Hit(val, src, tried, time.time() - started)

    log.warning("遍历完 %d 个候选仍未命中——请确认截获的是该数据库所在账号的登录过程", tried)
    return None


def verify(passphrase: bytes, db_path: str | Path) -> bool:
    """确认一个候选口令确实能解开该数据库。"""
    return wx_crypto.check_page1(passphrase, page1_of(db_path))


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="从截获片段还原微信数据库口令")
    parser.add_argument("--capture", required=True, help="注入端写出的 JSONL 路径")
    parser.add_argument("--db", required=True, help="任一加密数据库路径，用于校验")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--seconds", type=float, default=None, help="时间上限（秒）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    hit = find_passphrase(args.capture, args.db, workers=args.workers, deadline_s=args.seconds)
    if hit is None:
        print("未命中")
        return 1
    print("命中：" + hit.describe())
    return 0


__all__ = [
    "Hit",
    "find_passphrase",
    "iter_candidates",
    "load_capture",
    "main",
    "page1_of",
    "verify",
]

if __name__ == "__main__":
    raise SystemExit(main())
