"""验证：单实例判重（命名互斥量）真的拦得住第二份 bridge。

不启动 bridge、不动微信——只验证互斥量本身：同进程重复创建要判出「已存在」，
跨进程也要判得出来，换成没被占用的名字要判「首次」（对照组），拿不到句柄时
不得把程序拦死。
"""

import ctypes
import os
import subprocess
import sys
import uuid

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import single_instance  # noqa: E402


def main() -> int:
    checks = 0

    assert single_instance.SINGLE_INSTANCE_NAME.startswith("Global\\"), (
        single_instance.SINGLE_INSTANCE_NAME
    )
    print(f"  ok   互斥量名字带 Global\\ 前缀（{single_instance.SINGLE_INSTANCE_NAME}）")
    checks += 1

    # 用随机名字，免得和真正在跑的 bridge 撞上
    name = "Global\\Flandora_Test_" + uuid.uuid4().hex

    h1, running1 = single_instance.acquire_mutex(name)
    if sys.platform != "win32":
        assert (h1, running1) == (None, False), (h1, running1)
        print(f"  skip 非 Windows，跳过互斥量行为检查（{checks}/{checks} 通过）")
        return 0

    assert h1, "第一次创建互斥量应拿到句柄"
    assert running1 is False, "第一次创建不该报「已存在」"
    print("  ok   首次创建拿到句柄，未报已存在")
    checks += 1

    # 同进程再建一次：内核返回同一个对象，last-error 置 ERROR_ALREADY_EXISTS
    h2, running2 = single_instance.acquire_mutex(name)
    assert h2, "同进程内重复创建也应拿到句柄"
    assert running2 is True, "同进程内重复创建必须判出「已存在」"
    print("  ok   同进程内重复创建判出已存在")
    checks += 1

    # 跨进程：子进程拿同一个名字——这才是真正的使用场景
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from flandora.single_instance import acquire_mutex;"
        "h, running = acquire_mutex(%r);"
        "print('ALREADY' if running else 'FIRST')"
    ) % (REPO_ROOT, name)

    def run_child(src: str) -> str:
        out = subprocess.run(
            [sys.executable, "-c", src], capture_output=True, text=True, timeout=60
        )
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    assert run_child(code) == "ALREADY", "第二个进程必须判出「已存在」"
    print("  ok   另一个进程里判出已存在（拦得住第二份 bridge）")
    checks += 1

    # 对照组：换个没人占用的名字，同一个子进程代码应判「首次」
    assert run_child(code.replace(repr(name), repr(name + "_other"))) == "FIRST", (
        "没被占用的名字应判为首次"
    )
    print("  ok   换成没被占用的名字时判为首次（对照）")
    checks += 1

    # 拿不到内核对象时不能把程序拦死。用替身顶掉模块里的 sys，不动真 sys.platform
    class _NotWindows:
        platform = "linux"

    real_sys = single_instance.sys
    single_instance.sys = _NotWindows()
    try:
        assert single_instance.acquire_mutex(name) == (None, False)
    finally:
        single_instance.sys = real_sys
    print("  ok   拿不到互斥量时返回 (None, False)，不拦启动")
    checks += 1

    kernel32 = ctypes.WinDLL("kernel32")
    for h in (h1, h2):
        if h:
            kernel32.CloseHandle(h)

    print(f"{checks}/{checks} 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
