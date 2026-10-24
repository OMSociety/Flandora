"""验证：版本号的解析与比较，以及查不动的时候真的什么都不做。

不联网——`update_check._opener` 被换成桩函数。要验的是三件事：版本号怎么比
（字符串序会踩的坑、补零、解析不出来）、各种失败（连不上 / 超时 / 限流 /
响应不是 XML / 订阅里没有条目）是不是真的静默，以及后台线程写进
`state.update_available` 的内容对不对。
"""

import os
import sys
import urllib.error

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import state, update_check  # noqa: E402

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Release notes from Flandora</title>
  <entry>
    <title>{tag}</title>
    <link rel="alternate" type="text/html" href="{url}"/>
  </entry>
</feed>
"""

_EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Release notes from Flandora</title>
</feed>
"""


class _FakeResponse:
    """够用的 urlopen 返回值：上下文管理器 + read()。"""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _body(tag: str = "v0.0.1", url: str = "https://example.invalid/release") -> bytes:
    return _FEED.format(tag=tag, url=url).encode("utf-8")


def _stub(body: bytes = b"", exc: BaseException = None):
    """造一个假的 urlopen：给了 exc 就抛，否则返回给定响应体。"""

    def opener(request, timeout=None):
        if exc is not None:
            raise exc
        return _FakeResponse(body if body else _body())

    return opener


def main() -> int:
    checks = 0
    original_opener = update_check._opener

    # ===== 版本号解析 =====
    for text, want in [
        ("v1.2.3", (1, 2, 3)),
        ("1.2.3", (1, 2, 3)),
        ("v1.0.0-beta.1", (1, 0, 0)),
        ("v1.2.3+build.5", (1, 2, 3)),
        ("v10.20.30", (10, 20, 30)),
        ("  1.0  ", (1, 0)),
        ("", ()),
        (None, ()),
        ("v", ()),
        ("abc", ()),
        ("1.x.0", ()),
    ]:
        got = update_check.parse_version(text)
        assert got == want, f"parse_version({text!r}) = {got!r}，期望 {want!r}"
    print("  ok   版本号解析 11 例（v 前缀、预发布与 build 后缀、空白、乱输入）")
    checks += 1

    # ===== 新旧比较 =====
    for candidate, current, want in [
        ("v1.0.1", "1.0.0", True),
        ("v1.0.0", "1.0.0", False),
        ("v1.0", "1.0.0", False),  # 缺位补 0，不算新
        ("v2.0.0", "1.9.9", True),
        ("v1.10.0", "1.9.0", True),  # 按数字段比，不是字符串比
        ("v1.0.0", "1.0.1", False),  # 比当前旧
        ("", "1.0.0", False),  # 解不出来就不提示
        ("v1.0.1", "", False),
        ("nonsense", "1.0.0", False),
        ("v1.0.1-beta.1", "1.0.0", True),
    ]:
        got = update_check.is_newer(candidate, current)
        assert got is want, f"is_newer({candidate!r}, {current!r}) = {got}，期望 {want}"
    print("  ok   新旧比较 10 例（含 1.10 与 1.9 的字符串序陷阱）")
    checks += 1

    # ===== 查到新版 =====
    try:
        update_check._opener = _stub(body=_body("v9.9.9", "https://example.invalid/v999"))
        got = update_check.check(timeout=0.1)
        assert got == ("9.9.9", "https://example.invalid/v999"), got
        print("  ok   查到新版返回 (版本号, 页面链接)，tag 的 v 前缀已去掉")
        checks += 1
    finally:
        update_check._opener = original_opener

    # ===== 订阅里没有链接时自己拼发布页 =====
    try:
        update_check._opener = _stub(body=_FEED.format(tag="v9.9.9", url="").encode("utf-8"))
        got = update_check.check(timeout=0.1)
        assert got and got[0] == "9.9.9" and got[1].endswith("/tag/v9.9.9"), got
        print("  ok   订阅里没给链接时，按 tag 自己拼发布页地址")
        checks += 1
    finally:
        update_check._opener = original_opener

    # ===== 已是最新 =====
    try:
        update_check._opener = _stub(body=_body("v" + update_check.__version__))
        assert update_check.check(timeout=0.1) is None
        print(f"  ok   已是最新（{update_check.__version__}）返回 None")
        checks += 1
    finally:
        update_check._opener = original_opener

    # ===== 订阅里没有任何 release =====
    try:
        update_check._opener = _stub(body=_EMPTY_FEED.encode("utf-8"))
        assert update_check.check(timeout=0.1) is None
        print("  ok   订阅里没有条目（仓库还没发过版）→ 静默返回 None")
        checks += 1
    finally:
        update_check._opener = original_opener

    # ===== 五种失败都要静默 =====
    for label, body, exc in [
        ("连不上", None, urllib.error.URLError("no network")),
        ("超时", None, TimeoutError("timed out")),
        (
            "HTTP 403 限流",
            None,
            urllib.error.HTTPError(update_check.FEED_API, 403, "rate limited", {}, None),
        ),
        (
            "HTTP 404 仓库不存在",
            None,
            urllib.error.HTTPError(update_check.FEED_API, 404, "Not Found", {}, None),
        ),
        ("响应不是 XML", b"<html>502 Bad Gateway</html>", None),
    ]:
        try:
            update_check._opener = _stub(body=body, exc=exc)
            got = update_check.check(timeout=0.1)
            assert got is None, f"{label} 时返回了 {got!r}"
            print(f"  ok   {label} → 静默返回 None")
            checks += 1
        finally:
            update_check._opener = original_opener

    # ===== 后台线程把结果写进 state =====
    try:
        state.update_available = None
        update_check._opener = _stub(body=_body("v9.9.9", "https://example.invalid/v999"))
        update_check.check_async(timeout=0.1).join(timeout=10)
        got = state.update_available
        assert got == {
            "latest": "9.9.9",
            "current": update_check.__version__,
            "url": "https://example.invalid/v999",
        }, got
        print("  ok   check_async 把 (latest, current, url) 写进 state.update_available")
        checks += 1
    finally:
        update_check._opener = original_opener

    # ===== 没有新版时 state 不动 =====
    try:
        state.update_available = None
        update_check._opener = _stub(body=_body("v" + update_check.__version__))
        update_check.check_async(timeout=0.1).join(timeout=10)
        assert state.update_available is None, state.update_available
        print("  ok   没有新版时 check_async 不写 state")
        checks += 1
    finally:
        update_check._opener = original_opener

    # ===== 查不动时也照样安静地结束 =====
    try:
        state.update_available = None
        update_check._opener = _stub(exc=urllib.error.URLError("no network"))
        update_check.check_async(timeout=0.1).join(timeout=10)
        assert state.update_available is None, state.update_available
        print("  ok   连不上时 check_async 不写 state、不抛异常")
        checks += 1
    finally:
        update_check._opener = original_opener

    print(f"{checks}/{checks} 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
