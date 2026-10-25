"""日志装配：文件 handler 的创建、轮转策略与热更新。

handler 归这里持有，配置门面只把 ``config.LOG_HANDLER`` / ``config.LOG_FILE`` 转发
过来。``setup_logging()`` 由 ``bootstrap()`` 调一次；策略变更走订阅，所以面板改了
``log_max_mb`` / ``log_backup_count`` 不必重启也不会丢日志。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from .store import Settings

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATEFMT = "%H:%M:%S"
LOG_LEVEL = logging.INFO

# 日志策略变更后要重新装配的键（HOT 里只这两项与 handler 有关）
POLICY_KEYS = frozenset({"log_max_mb", "log_backup_count"})

_handler: logging.handlers.RotatingFileHandler | None = None
_stream: logging.StreamHandler | None = None


def _config_module():
    # 惰性导入：门面在导入期就会 import 本模块，模块级反向导入会成环
    return sys.modules[__package__]


def log_file() -> str:
    """日志文件路径：跟配置文件所在目录走，配置换了日志也跟着换。"""
    return os.path.join(_config_module().data_dir(), "flandora.log")


def current_handler() -> logging.handlers.RotatingFileHandler | None:
    return _handler


def build_handler(
    max_mb: int, backup_count: int, path: str
) -> logging.handlers.RotatingFileHandler:
    """按策略建一份文件 handler；目录不存在就建（这是日志唯一负责的落盘）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=int(max_mb) * 1024 * 1024,
        backupCount=int(backup_count),
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATEFMT))
    return handler


def _detach(handler: logging.Handler | None) -> None:
    if handler is None:
        return
    logging.getLogger().removeHandler(handler)
    handler.close()


def setup_logging(settings: Settings | None = None, *, force: bool = False, attach: bool = True):
    """装配控制台与文件两个 handler，按当前策略。

    ``force`` 换到新的日志路径；``attach=False`` 只建 handler 不接管 root logger
    （临时配置用：测试与一次性脚本不该悄悄改全局日志输出）。
    """
    global _handler, _stream
    if settings is None:
        settings = _config_module().load_config()
    if _handler is not None and not force:
        apply_log_policy(settings.LOG_MAX_MB, settings.LOG_BACKUP_COUNT)
        return _handler
    _detach(_handler)
    _detach(_stream)
    _handler = build_handler(settings.LOG_MAX_MB, settings.LOG_BACKUP_COUNT, log_file())
    _stream = logging.StreamHandler()
    _stream.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATEFMT))
    if attach:
        root = logging.getLogger()
        root.setLevel(LOG_LEVEL)
        for handler in (_handler, _stream):
            if handler not in root.handlers:
                root.addHandler(handler)
    _config_module().get_store().subscribe(on_settings_changed)
    return _handler


def close_logging() -> None:
    """收掉 handler（测试与进程退出用），之后的日志只走控制台。"""
    global _handler, _stream
    _detach(_handler)
    _detach(_stream)
    _handler = None
    _stream = None


def apply_log_policy(max_mb: int, backup_count: int) -> None:
    """把策略改到运行中的 handler 上，只影响「下一份怎么写」。"""
    if _handler is None:
        setup_logging()
        return
    _handler.maxBytes = int(max_mb) * 1024 * 1024
    _handler.backupCount = int(backup_count)


def on_settings_changed(settings: Settings, changed: frozenset) -> None:
    """订阅入口：日志策略是 HOT 键，改完立刻套到 handler 上。"""
    if changed & POLICY_KEYS:
        apply_log_policy(settings.LOG_MAX_MB, settings.LOG_BACKUP_COUNT)
