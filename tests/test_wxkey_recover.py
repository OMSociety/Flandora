"""取钥模块的纯逻辑单测（不依赖真微信、不依赖 Frida）。

只覆盖能离线判定的部分：候选枚举顺序与去重、JSONL 读写、页 1 校验的接线。
真机相关的定位与挂钩不在单测范围内。
"""
import os
import sys
import tempfile

# 仓库根 = 本文件的上一级；bridge 包在仓库根的 bridge/ 下。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(REPO_ROOT, "bridge")
for p in (BRIDGE, REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from wxkey import recover


def test_candidates_prioritise_deref_head():
    """解引用片段开头的 32 字节必须最先出现。"""
    blobs = [("setkey.before", bytes(range(64))),
             ("setkey.deref8", bytes(range(100, 164)))]
    cands = list(recover.iter_candidates(blobs))
    source, value = cands[0]
    assert "deref8" in source, source
    assert value == bytes(range(100, 132)), value
    assert len(value) == 32


def test_candidates_dedupe_identical_blobs():
    """同一块缓冲区被多个指针槽指向时，候选不应重复。"""
    shared = bytes(range(128))
    blobs = [("setkey.deref0", shared), ("setkey.deref8", shared),
             ("setkey.deref16", shared)]
    single = list(recover.iter_candidates([("setkey.deref0", shared)]))
    multi = list(recover.iter_candidates(blobs))
    assert len(multi) == len(single)
    values = [v for _s, v in multi]
    assert len(values) == len(set(values))


def test_candidates_cover_all_offsets():
    """去重后仍须覆盖每个 4 字节对齐偏移，不能漏掉换版本后的偏移变化。"""
    data = bytes((i * 7) % 251 for i in range(96))
    cands = list(recover.iter_candidates([("setkey.deref8", data)]))
    offsets = {int(src.split("+")[1].split("x")[0], 0) for src, _v in cands}
    expected = set(range(0, len(data) - 15, 4))
    assert expected <= offsets, sorted(expected - offsets)


def test_load_capture_skips_bad_lines():
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                    encoding="utf-8") as fh:
        fh.write('{"type":"meta","rva":"0x1"}\n')
        fh.write("not json\n")
        fh.write('{"type":"blob","tag":"t","hex":"00ff"}\n')
        fh.write('{"type":"blob","tag":"bad","hex":"zz"}\n')
        path = fh.name
    try:
        blobs = recover.load_capture(path)
    finally:
        os.unlink(path)
    assert len(blobs) == 1
    assert blobs[0][1] == b"\x00\xff"


def test_page1_of_rejects_short_file():
    with tempfile.NamedTemporaryFile("wb", suffix=".db", delete=False) as fh:
        fh.write(b"\x00" * 100)
        path = fh.name
    try:
        try:
            recover.page1_of(path)
        except ValueError:
            pass
        else:
            raise AssertionError("不足一页的库应当报错")
    finally:
        os.unlink(path)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
