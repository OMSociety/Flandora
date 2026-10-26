"""验证：版本号的解析与比较，以及查不动的时候真的什么都不做。

不联网——`update_check._opener` 被换成桩函数。要验的是三件事：版本号怎么比
（字符串序会踩的坑、补零、解析不出来）、各种失败（连不上 / 超时 / 限流 /
响应不是 XML / 订阅里没有条目）是不是真的静默，以及后台线程写进
`RUNTIME.update_available` 的内容对不对。

两种跑法等价：

    python -m pytest tests/test_update_check.py -q
    python tests/test_update_check.py
"""

import os
import sys
import urllib.error

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import update_check  # noqa: E402
from flandora.runtime import RUNTIME

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


@pytest.fixture
def opener(monkeypatch):
    """把 update_check._opener 换成桩，用例结束自动还原。"""

    def install(body: bytes = b"", exc: BaseException = None):
        monkeypatch.setattr(update_check, "_opener", _stub(body=body, exc=exc))

    return install


@pytest.fixture
def clean_update_state(monkeypatch):
    """RUNTIME.update_available 归零，用例结束自动还原。"""
    monkeypatch.setattr(RUNTIME, "update_available", None)
    return RUNTIME


# ===== 版本号比大小 =====


@pytest.mark.parametrize(
    "candidate,current,want",
    [
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
    ],
)
def test_is_newer(candidate, current, want):
    """含 1.10 与 1.9 的字符串序陷阱：按数字段比才判得对。"""
    assert update_check.is_newer(candidate, current) is want


# ===== 查到新版 =====


def test_check_reports_newer_version(opener):
    """查到新版返回 (版本号, 页面链接)，tag 的 v 前缀已去掉。"""
    opener(body=_body("v9.9.9", "https://example.invalid/v999"))
    assert update_check.check(timeout=0.1) == ("9.9.9", "https://example.invalid/v999")


def test_check_builds_page_url_from_tag_when_link_missing(opener):
    """订阅里没给链接时，按 tag 自己拼发布页地址。"""
    opener(body=_FEED.format(tag="v9.9.9", url="").encode("utf-8"))
    got = update_check.check(timeout=0.1)
    assert got and got[0] == "9.9.9" and got[1].endswith("/tag/v9.9.9"), got


def test_check_returns_none_when_current(opener):
    """已是最新版本时返回 None。"""
    opener(body=_body("v" + update_check.__version__))
    assert update_check.check(timeout=0.1) is None


def test_check_returns_none_on_empty_feed(opener):
    """订阅里没有条目（仓库还没发过版）→ 静默返回 None。"""
    opener(body=_EMPTY_FEED.encode("utf-8"))
    assert update_check.check(timeout=0.1) is None


# ===== 五种失败都要静默 =====


@pytest.mark.parametrize(
    "label,body,exc",
    [
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
    ],
)
def test_check_failure_is_silent(opener, label, body, exc):
    """查不动时不能抛异常，也不能报出根本不存在的版本。"""
    opener(body=body, exc=exc)
    assert update_check.check(timeout=0.1) is None, f"{label} 时返回了非 None"


# ===== 后台线程把结果写进 RUNTIME =====


def test_check_async_writes_state(opener, clean_update_state):
    """check_async 把 (latest, current, url) 写进 RUNTIME.update_available。"""
    opener(body=_body("v9.9.9", "https://example.invalid/v999"))
    update_check.check_async(timeout=0.1).join(timeout=10)
    assert clean_update_state.update_available == {
        "latest": "9.9.9",
        "current": update_check.__version__,
        "url": "https://example.invalid/v999",
    }


def test_check_async_leaves_state_alone_when_current(opener, clean_update_state):
    """没有新版时 check_async 不写 RUNTIME。"""
    opener(body=_body("v" + update_check.__version__))
    update_check.check_async(timeout=0.1).join(timeout=10)
    assert clean_update_state.update_available is None


def test_check_async_is_quiet_when_offline(opener, clean_update_state):
    """连不上时 check_async 不写 RUNTIME、不抛异常。"""
    opener(exc=urllib.error.URLError("no network"))
    update_check.check_async(timeout=0.1).join(timeout=10)
    assert clean_update_state.update_available is None


def main() -> int:
    """脚本直跑：委托给 pytest 执行同一个文件。

    本文件的用例依赖 fixture 与参数化表，逐条直接调用会 TypeError。委托之后
    两条入口跑的是同一套断言，不会出现「pytest 绿、脚本红」的分裂。
    """
    import pytest

    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
