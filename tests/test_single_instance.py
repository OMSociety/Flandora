"""验证：单实例判重（命名互斥量）真的拦得住第二份 bridge。

不启动 bridge、不动微信——只验证互斥量本身：同进程重复创建要判出「已存在」，
跨进程也要判得出来，换成没被占用的名字要判「首次」（对照组），拿不到句柄时
不得把程序拦死。

两种跑法等价：

    python -m pytest tests/test_single_instance.py -q
    python tests/test_single_instance.py
"""

import ctypes
import os
import subprocess
import sys
import uuid

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import single_instance  # noqa: E402

# 互斥量是 Windows 内核对象；非 Windows 上 acquire_mutex 直接回 (None, False)，
# 只有「不拦启动」这一条还能验。
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="命名互斥量是 Windows 内核对象")

# 用随机名字，免得和真正在跑的 bridge 撞上
_NAME = "Global\\Flandora_Test_" + uuid.uuid4().hex

# 跨进程检查要用的子进程代码：拿同一个互斥量名字，打印 ALREADY / FIRST
_CHILD_SRC = (
    "import sys; sys.path.insert(0, %r);"
    "from flandora.single_instance import acquire_mutex;"
    "h, running = acquire_mutex(%r);"
    "print('ALREADY' if running else 'FIRST')"
) % (REPO_ROOT, _NAME)


def _run_child(src: str) -> str:
    out = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_mutex_name_is_global():
    """名字带 Global\\ 前缀：跨会话（多用户 / 服务）也能互斥。"""
    assert single_instance.SINGLE_INSTANCE_NAME.startswith("Global\\"), (
        single_instance.SINGLE_INSTANCE_NAME
    )


@windows_only
def test_first_acquire_takes_handle():
    """首次创建拿到句柄，且不报「已存在」。"""
    h, running = single_instance.acquire_mutex(_NAME)
    try:
        assert h, "第一次创建互斥量应拿到句柄"
        assert running is False, "第一次创建不该报「已存在」"
    finally:
        if h:
            ctypes.WinDLL("kernel32").CloseHandle(h)


@windows_only
def test_second_acquire_in_same_process_reports_existing():
    """同进程再建一次：内核返回同一个对象，last-error 置 ERROR_ALREADY_EXISTS。"""
    h1, _ = single_instance.acquire_mutex(_NAME)
    h2, running2 = single_instance.acquire_mutex(_NAME)
    try:
        assert h2, "同进程内重复创建也应拿到句柄"
        assert running2 is True, "同进程内重复创建必须判出「已存在」"
    finally:
        kernel32 = ctypes.WinDLL("kernel32")
        for h in (h1, h2):
            if h:
                kernel32.CloseHandle(h)


@windows_only
def test_other_process_reports_existing():
    """跨进程：子进程拿同一个名字——这才是真正的使用场景。"""
    h, _ = single_instance.acquire_mutex(_NAME)
    try:
        assert _run_child(_CHILD_SRC) == "ALREADY", "第二个进程必须判出「已存在」"
    finally:
        if h:
            ctypes.WinDLL("kernel32").CloseHandle(h)


@windows_only
def test_unused_name_reports_first():
    """对照组：换个没人占用的名字，同一段子进程代码应判「首次」。"""
    h, _ = single_instance.acquire_mutex(_NAME)
    try:
        src = _CHILD_SRC.replace(repr(_NAME), repr(_NAME + "_other"))
        assert _run_child(src) == "FIRST", "没被占用的名字应判为首次"
    finally:
        if h:
            ctypes.WinDLL("kernel32").CloseHandle(h)


def test_unavailable_kernel_object_does_not_block_startup():
    """拿不到内核对象时返回 (None, False)，不把程序拦死。

    用替身顶掉模块里的 sys，不动真 sys.platform。
    """

    class _NotWindows:
        platform = "linux"

    real_sys = single_instance.sys
    single_instance.sys = _NotWindows()
    try:
        assert single_instance.acquire_mutex(_NAME) == (None, False)
    finally:
        single_instance.sys = real_sys


def _is_skipped(fn) -> bool:
    """脚本模式下判断用例是否应跳过：评估 skipif 的条件，而不是见到标记就跳。"""
    for mark in getattr(fn, "pytestmark", []):
        if getattr(mark, "name", "") == "skipif" and mark.args and mark.args[0]:
            return True
    return False


def main() -> int:
    """脚本直跑：与 pytest 用同一套 skipif 条件。"""
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    ran = 0
    for fn in tests:
        if _is_skipped(fn):
            print(f"  skip {fn.__name__}")
            continue
        ran += 1
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"{ran - failed}/{ran} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
