"""启动时查一次有没有新版本。

只看 GitHub 的 releases atom 订阅，不碰 API：未认证的 REST API 按 IP 限流
（每小时 60 次），很容易撞上；atom 订阅没有这个限制，取第一条 entry 的标题
就是最新版本号。

任何一步出问题（连不上、超时、限流、响应不是 XML、订阅里没有条目）都按
「查不到」处理，只写一条 debug 日志——版本提示不能影响程序启动。
"""
import logging
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from . import __version__

log = logging.getLogger("ob11-bridge")

REPO = "OMSociety/WeBot-Bridge"
FEED_API = f"https://github.com/{REPO}/releases.atom"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
TIMEOUT = 5.0

# 单独提出来，方便测试替换
_opener = urllib.request.urlopen

_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


def parse_version(text) -> tuple:
    """把 `v1.2.3` / `1.2.3-beta.1` 解成 (1, 2, 3)；解不出来给空元组。

    允许前导 v、允许多段，预发布后缀按「不属于版本号」丢掉。
    """
    if not isinstance(text, str):
        return ()
    body = text.strip()
    if body[:1] in ("v", "V"):
        body = body[1:]
    head = body.split("-", 1)[0].split("+", 1)[0].strip()
    if not head or not head[0].isdigit():
        return ()
    parts = head.split(".")
    out = []
    for part in parts:
        if not part.isdigit():
            return ()
        out.append(int(part))
    return tuple(out)


def is_newer(candidate, current) -> bool:
    """candidate 是否比 current 新。任一侧解不出数字就不提示。"""
    left = parse_version(candidate)
    right = parse_version(current)
    if not left or not right:
        return False
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left > right


def _latest_tag(body: bytes):
    """从 atom 订阅里取第一条 release 的 (tag, 链接)；没有条目返回 None。"""
    root = ET.fromstring(body)
    entries = root.findall("a:entry", _ATOM_NS)
    if not entries:
        return None
    first = entries[0]
    tag = (first.findtext("a:title", "", _ATOM_NS) or "").strip()
    if not tag:
        return None
    link = first.find("a:link", _ATOM_NS)
    url = link.get("href") if link is not None else ""
    if not url:
        url = f"{RELEASES_PAGE}/tag/{tag}"
    return tag, url


def check(timeout: float = TIMEOUT):
    """查一次。有新版本返回 (版本号, 页面链接)，否则 None。不抛异常。"""
    request = urllib.request.Request(FEED_API, headers={
        "User-Agent": f"WeBot-Bridge/{__version__}",
        "Accept": "application/atom+xml",
    })
    try:
        with _opener(request, timeout=timeout) as response:
            body = response.read()
    except Exception as exc:
        log.debug("查新版本失败（忽略）：%s: %s", type(exc).__name__, exc)
        return None

    try:
        found = _latest_tag(body)
    except Exception as exc:
        log.debug("解析版本订阅失败（忽略）：%s: %s", type(exc).__name__, exc)
        return None

    if not found:
        return None
    tag, url = found
    if not is_newer(tag, __version__):
        return None
    latest = tag.strip().lstrip("vV")
    log.info("发现新版本 %s（当前 %s）：%s", latest, __version__, url)
    return latest, url


def check_async(timeout: float = TIMEOUT) -> threading.Thread:
    """后台查一次，结果写进 state.update_available。返回该线程便于测试等待。"""
    def worker():
        found = check(timeout=timeout)
        if found:
            latest, url = found
            try:
                from . import state
                state.update_available = {
                    "latest": latest,
                    "current": __version__,
                    "url": url,
                }
            except Exception as exc:
                log.debug("写入版本状态失败（忽略）：%s: %s", type(exc).__name__, exc)

    thread = threading.Thread(target=worker, name="update-check", daemon=True)
    thread.start()
    return thread
