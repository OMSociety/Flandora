"""入站文件的下载令牌：把本机文件变成客户端能取的 HTTP 地址。

AstrBot 等客户端收到 ``file`` 段后只认 ``http(s)`` 的 ``url`` 或能换出 url 的
``file_id``，不会去读桥接所在机器上的路径。每个文件登记一枚随机令牌，面板的
``/files/<令牌>/<文件名>`` 只服务登记过的路径，令牌不可猜、数量有上限，
不接受任何由请求给出的路径。客户端与桥接分处两台机器时，设 ``web_public_host`` 让地址可达。
"""

import collections
import os
import secrets
import threading
from urllib.parse import quote

from . import config

MAX_ENTRIES = 512

_lock = threading.Lock()
_entries: "collections.OrderedDict[str, tuple[str, str]]" = collections.OrderedDict()
_by_path: dict[str, str] = {}


def register(path: str, name: str = "") -> str:
    """登记一个本机文件，返回令牌；同一路径重复登记得到同一枚令牌。"""
    path = os.path.abspath(path)
    with _lock:
        token = _by_path.get(path)
        if token and token in _entries:
            _entries.move_to_end(token)
            return token
        token = secrets.token_urlsafe(18)
        _entries[token] = (path, name or os.path.basename(path))
        _by_path[path] = token
        while len(_entries) > MAX_ENTRIES:
            _, (old_path, _name) = _entries.popitem(last=False)
            _by_path.pop(old_path, None)
        return token


def lookup(token: str) -> tuple[str, str] | None:
    """令牌 → ``(路径, 文件名)``；没登记过、或文件已不在返回 None。"""
    with _lock:
        entry = _entries.get(token)
    if entry is None or not os.path.isfile(entry[0]):
        return None
    return entry


def url_for(token: str, name: str) -> str:
    """面板上服务这枚令牌的地址。

    主机取 ``web_public_host``（客户端在别的机器上时填桥接的局域网地址），留空时用面板
    绑定地址；绑定全部网卡或未设时回落 127.0.0.1。
    """
    host = (config.WEB_PUBLIC_HOST or config.WEB_BIND_HOST or "").strip()
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = "https" if config.WEBUI_TLS else "http"
    return f"{scheme}://{host}:{config.WEB_PORT}/files/{token}/{quote(name or 'file')}"
