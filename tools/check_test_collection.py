"""核对每个测试文件都真的被 pytest 收到了用例。

挡的是一种静默失效：文件名叫 ``tests/test_*.py``，但里面全是 ``main()`` 脚本式
断言、没有 ``def test_``，于是 ``pytest tests -q`` 收集到 0 个用例却不报错——
整套断言从未在 CI 里执行过，绿灯是假的。

判定口径：``tests/test_*.py`` 里的每个文件，其自身至少要贡献一个被 pytest
收集到的用例。``--collect-only`` 的输出按文件聚合，没有出现在输出里的文件
即视为零收集。

    python tools/check_test_collection.py

退出码 0 表示全部有归属；1 表示存在零收集的测试文件。
"""

import os
import re
import subprocess
import sys

# Windows 上 stdout 可能不是 UTF-8（控制台默认 GBK、CI runner 默认 cp1252），
# 直接 print 中文会抛 UnicodeEncodeError 并把退出码变成 1——那样这个闸会把
# 「全部通过」误报成失败。重新绑定成 UTF-8，无法编码的字符转义输出而不中断。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):  # 非文本流，或被重定向到不支持的对象
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT, "tests")

# 收集到的条目形如 tests/test_x.py::test_name 或 tests/test_x.py::Class::test_name
_ITEM = re.compile(r"^(tests[/\\][^\s:]+\.py)::")


def _test_files() -> list[str]:
    names = sorted(n for n in os.listdir(TESTS_DIR) if n.startswith("test_") and n.endswith(".py"))
    return [os.path.join("tests", n) for n in names]


def _collected() -> dict[str, int]:
    """跑一次 --collect-only，返回 {相对路径: 用例数}。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 5):  # 5 = 没收集到任何用例，下面按空表处理
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"pytest --collect-only 失败（退出码 {proc.returncode}）")

    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        m = _ITEM.match(line.strip())
        if not m:
            continue
        key = m.group(1).replace("\\", "/")
        counts[key] = counts.get(key, 0) + 1
    return counts


def main() -> int:
    counts = _collected()
    files = _test_files()
    empty = [f for f in files if counts.get(f.replace("\\", "/"), 0) == 0]

    for path in files:
        n = counts.get(path.replace("\\", "/"), 0)
        print(f"  {n:4d}  {path}")

    if not empty:
        print(f"\n{len(files)} 个测试文件全部被 pytest 收集，共 {sum(counts.values())} 个用例")
        return 0

    print(f"\n以下 {len(empty)} 个文件被 pytest 收集到 0 个用例：", file=sys.stderr)
    for path in empty:
        print(f"  {path}", file=sys.stderr)
    print(
        "\n这类文件通常是 main() 脚本式断言：pytest 不会收集 main()，"
        "整套断言会在 CI 里静默跳过。把其中的检查改成 def test_* 函数，"
        "或确认它应当被 pytest 忽略（那就改名，别叫 test_*.py）。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
