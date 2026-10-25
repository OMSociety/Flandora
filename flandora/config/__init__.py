"""配置门面：``from flandora import config`` 之后按名字取到的就是当前有效配置。

导入本模块不读盘、不建目录、不装 handler、不碰 ``ob_network``：第一次取属性时才
按需加载（加锁、只算一次），进入主流程时也可以显式调 :func:`bootstrap()` 把日志
与回退告警一起打出来。

名字与 ``fields.FIELDS`` 一一对应：``config.WEB_PORT`` 走模块级 ``__getattr__``
取 :class:`store.Settings` 上的值；``CONFIG_FILE``、``LOG_FILE``、``LOG_HANDLER``
这类派生值在下面单独转发。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import types
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from . import fields, logging_setup, paths
from .fields import ConfigError
from .store import ConfigStore, Settings, UpdateResult, build_settings, write_json

log = logging.getLogger("flandora")

# 对外入口：字段名走模块级 __getattr__，其余在这里显式列出
__all__ = [
    "ConfigError",
    "ConfigStore",
    "Settings",
    "UpdateResult",
    "apply_log_policy",
    "bootstrap",
    "build_settings",
    "data_dir",
    "fields",
    "get_store",
    "load_config",
    "logging_setup",
    "paths",
    "subscribe",
    "temporary",
    "webui_tls_problem",
    "write_json",
]

# Settings 属性名 → config.json 键名
_ATTR_TO_KEY: dict[str, str] = {spec.attr: spec.key for spec in fields.FIELDS}
# 这些名字可以被打补丁（测试里常见）：值只进覆盖表，不落成真实模块属性
_PINNABLE = frozenset(_ATTR_TO_KEY) | frozenset(
    {"CONFIG_FILE", "CONFIG_GENERATED", "LOG_FILE", "LOG_HANDLER", "WEB_BIND_HOST_ERROR"}
)
_OVERRIDES = "_attr_overrides"

_lock = threading.RLock()
_store: ConfigStore | None = None
# 订阅者台账：换 store（temporary）时整份带给新 store，登记过的回调不会掉
_listeners: list = []


class _ConfigModule(types.ModuleType):
    """模块子类：字段名永远不落成真实属性。

    ``config.WEB_PORT = 1234`` 这类写法如果在模块字典里真建了属性，之后所有读取都会
    被这个死值盖住，与 store 脱节；这里把它收进覆盖表，读的时候由 ``__getattr__``
    先看覆盖表（见 :func:`_clear_patches`）。
    """

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _PINNABLE:
            self.__dict__.setdefault(_OVERRIDES, {})[name] = value
            return
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        overrides = self.__dict__.get(_OVERRIDES) or {}
        if name in overrides:
            del overrides[name]
            return
        super().__delattr__(name)


def _clear_patches() -> None:
    """丢掉字段级补丁：临时配置一进来就以 store 为准，块内的补丁也不带出块外。"""
    overrides = sys.modules[__name__].__dict__.get(_OVERRIDES)
    if overrides:
        overrides.clear()


def get_store() -> ConfigStore:
    """当前配置 store；测试用 :func:`temporary` 换掉它。"""
    global _store
    with _lock:
        if _store is None:
            _store = ConfigStore(paths.find_config_path())
        return _store


def subscribe(callback) -> None:
    """登记配置变更订阅者；换 store 之后仍然有效。"""
    with _lock:
        if callback not in _listeners:
            _listeners.append(callback)
        get_store().subscribe(callback)


def load_config() -> Settings:
    """显式加载并返回有效配置（等价于读任意配置属性）。"""
    return get_store().settings


def data_dir() -> str:
    """配置文件所在目录：日志、pid、缓存都跟着它走。"""
    return get_store().data_dir()


def bootstrap() -> Settings:
    """进程启动时调一次：加载配置、装配日志、把回退告警打出来。"""
    settings = get_store().settings
    logging_setup.setup_logging(settings)
    for message in settings.warnings:
        log.warning("%s", message)
    for message in settings.errors:
        log.error("%s", message)
    return settings


def webui_tls_problem() -> str:
    """开着 HTTPS 但证书/私钥不可用时返回原因，正常时返回空串。

    证书是用户自己换的（续期、换域名），每次启动都要重新验，不能只在保存时验。
    """
    module = sys.modules[__name__]
    if not getattr(module, "WEBUI_TLS"):
        return ""
    cert = getattr(module, "WEBUI_TLS_CERT_FILE")
    key = getattr(module, "WEBUI_TLS_KEY_FILE")
    for label, path in (("证书", cert), ("私钥", key)):
        if not os.path.isfile(path):
            return f"{label}文件不存在：{path}"
    try:
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    except Exception as exc:  # 证书格式千奇百怪，把人读的原因原样交出去
        return f"证书与私钥无法加载：{exc}"
    return ""


def apply_log_policy(max_mb: int, backup_count: int) -> None:
    """即时改日志策略（不必重启），只影响下一份日志怎么写。"""
    logging_setup.apply_log_policy(max_mb, backup_count)


@contextmanager
def temporary(path: str | os.PathLike, overrides: Mapping[str, Any] | None = None):
    """把整个门面临时指向另一份配置：进入时换 store 与日志落点，退出时恢复。

    ``overrides`` 是低优先级默认值：文件里有的键以文件为准。字段级补丁只在块内
    有效——块内对 ``config.X`` 的赋值出块即失效，避免旧值盖住后面的真实配置。
    """
    global _store
    with _lock:
        previous = _store
        _clear_patches()
        store = ConfigStore(str(path), overrides, inherit=_listeners, generate=False)
        _store = store
    try:
        logging_setup.setup_logging(store.settings, force=True, attach=False)
        yield store
    finally:
        with _lock:
            _store = previous
            _clear_patches()
        if previous is None:
            # 最外层：别在收尾时又按默认目录把日志装回仓库 data/
            logging_setup.close_logging()
        else:
            logging_setup.setup_logging(load_config(), force=True, attach=False)


def __getattr__(name: str) -> Any:
    overrides = sys.modules[__name__].__dict__.get(_OVERRIDES) or {}
    if name in overrides:
        return overrides[name]
    if name in _ATTR_TO_KEY:
        return getattr(get_store().settings, name)
    if name == "CONFIG_FILE":
        return get_store().path
    if name == "CONFIG_GENERATED":
        return get_store().generated
    if name == "WEB_BIND_HOST_ERROR":
        return get_store().settings.WEB_BIND_HOST_ERROR
    if name in ("warnings", "errors"):
        return getattr(get_store().settings, name)
    if name == "LOG_FILE":
        return logging_setup.log_file()
    if name == "LOG_HANDLER":
        return logging_setup.current_handler()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 装上模块子类：字段名从此只进覆盖表，不会在模块字典里留死值
sys.modules[__name__].__class__ = _ConfigModule
