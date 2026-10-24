"""面板登录：密码存 PBKDF2 哈希，会话是进程内 token + HttpOnly Cookie。

`webui_password` 留空即关闭鉴权（任何本机进程都能改配置）；一旦设置，所有 `/api/*`
与 `/ws` 都要先登录。面板只监听回环地址，但浏览器里任意网页都能对 127.0.0.1 发
请求，所以开着密码时的鉴权是实打实的边界，不是摆设。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

PBKDF2_ITERATIONS = 200_000
SCHEME = "pbkdf2_sha256"
SESSION_TTL = 7 * 24 * 60 * 60
COOKIE_NAME = "flandora_session"
PUBLIC_API = frozenset({"/api/login", "/api/auth", "/api/logout"})
MIN_PASSWORD_LENGTH = 6
LOGIN_WINDOW = 60.0
LOGIN_MAX_FAILURES = 5


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """把明文密码变成可落盘的哈希串。空密码是配置错误，不静默当关闭。"""
    password = str(password or "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{SCHEME}${PBKDF2_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def is_hashed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(SCHEME + "$") and value.count("$") == 3


def verify_password(password: str, stored: object) -> bool:
    if not password or not is_hashed(stored):
        return False
    try:
        scheme, rounds, salt_b64, digest_b64 = str(stored).split("$")
        iterations = int(rounds)
        salt = _unb64(salt_b64)
        expected = _unb64(digest_b64)
    except (TypeError, ValueError):
        return False
    if scheme != SCHEME or iterations <= 0:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


class Sessions:
    """进程内会话表：面板重启即全部失效，换浏览器要重新登录。

    不落盘是有意的——服务端没有可靠的会话存储位置，而会话丢了最坏只是重新输一次密码。
    """

    def __init__(self, ttl: float = SESSION_TTL) -> None:
        self._ttl = ttl
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._tokens = {k: v for k, v in self._tokens.items() if v > now}
            self._tokens[token] = now + self._ttl
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expiry = self._tokens.get(token)
            if expiry is None:
                return False
            if expiry <= time.time():
                self._tokens.pop(token, None)
                return False
            return True

    def drop(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._tokens)


class LoginThrottle:
    """连续输错就等一会儿：面板是本机的，但脚本扫密码同样成立。"""

    def __init__(self, window: float = LOGIN_WINDOW, limit: int = LOGIN_MAX_FAILURES) -> None:
        self._window = window
        self._limit = limit
        self._failures: list[float] = []
        self._lock = threading.Lock()

    def blocked(self) -> bool:
        now = time.time()
        with self._lock:
            self._failures = [t for t in self._failures if now - t < self._window]
            return len(self._failures) >= self._limit

    def fail(self) -> None:
        now = time.time()
        with self._lock:
            self._failures.append(now)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


def is_public_api(path: str) -> bool:
    return path in PUBLIC_API


def enabled() -> bool:
    """配置里存了哈希即视为开启登录。"""
    from .. import config

    return is_hashed(getattr(config, "WEBUI_PASSWORD", "") or "")


def check(password: str) -> bool:
    from .. import config

    return verify_password(password, getattr(config, "WEBUI_PASSWORD", "") or "")


sessions = Sessions()
throttle = LoginThrottle()
