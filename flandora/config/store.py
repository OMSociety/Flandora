"""配置的唯一持有者：读盘、校验、原子写盘、通知订阅者。

``ConfigStore`` 不碰日志 handler，也不碰窗口绑定：那些运行期副作用由订阅者
（``logging_setup``、面板侧绑定）在收到通知时自己做。这样「谁在读配置」与
「配置怎么生效」分开，导入配置不会牵动网络层、日志或磁盘。

有效配置取「盘上文件为主、``overrides`` 为默认兜底」：面板改完立刻回读同一份
文件，界面显示的值与桥接实际用的值就不会分叉。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import fields, paths

log = logging.getLogger("flandora.config")

# 订阅者签名：拿新的有效配置与真正变更的键（只含 HOT 键）
Subscriber = Callable[["Settings", frozenset], None]


@dataclass(frozen=True)
class Settings:
    """一次校验后的有效配置。字段顺序与 ``fields.FIELDS`` 一致。"""

    # OneBot v11 服务端
    OB_SERVER_HOST: str = "127.0.0.1"
    OB_SERVER_PORT: int = 7999
    OB_SERVER_TOKEN: str = ""
    OB_NETWORKS: Any = None
    OB_MESSAGE_FORMAT: str = "array"
    OB_HEARTBEAT_SECONDS: int = 30
    # 控制面板
    WEB_PORT: int = 4950
    WEB_BIND_HOST: str = "127.0.0.1"
    WEBUI_AUTO_OPEN: bool = True
    WEBUI_PASSWORD: str = ""
    WEBUI_TLS: bool = False
    WEBUI_TLS_CERT_FILE: str = ""
    WEBUI_TLS_KEY_FILE: str = ""
    # 微信与收件
    CLIENT_ATTACHMENTS: str = ""
    WECHAT_HWND: int = 0
    WECHAT_FILES_ROOT: str = ""
    ALLOW_FOREGROUND_STEAL: bool = False
    SEND_KEY: str = "auto"
    GROUP_NAME_MAP: Any = field(default_factory=dict)
    WX_KEY_FILE: str = ""
    WX_WORK_DIR: str = ""
    WX_BACKFILL_SECONDS: int = 0
    WX_POLL_SECONDS: float = 1.0
    WX_IMAGE_KEY_FILE: str = ""
    WXKEY_MANUAL_RVA: int = 0
    ISOLATED_DESKTOP: bool = True
    ISOLATED_DESKTOP_NAME: str = "FlandoraSandbox"
    WECHAT_EXE: str = ""
    # 日志
    LOG_MAX_MB: int = 8
    LOG_BACKUP_COUNT: int = 5
    # 派生状态：web_bind_host 回退过时留给人看的原文，其余回退原因按序收集
    WEB_BIND_HOST_ERROR: str = ""
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class UpdateResult:
    """一次写入的结果：新的有效配置，以及要重启才成立的键。"""

    settings: Settings
    restart_required: tuple[str, ...] = ()


def build_settings(
    raw: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
    data_dir: str | None = None,
    *,
    strict: bool = False,
    strict_keys: frozenset[str] = frozenset(),
) -> Settings:
    """把盘上的原始 dict 校验成 ``Settings``。

    ``strict`` / ``strict_keys`` 决定哪些键的非法值直接抛 :class:`ConfigError`：
    面板保存只严格校验这次提交的键，盘上原有的值仍旧按规则回退——手改坏一处配置
    不该让面板连别的键都存不进去。其余键按既定规则回退，原因收进
    ``warnings``/``errors``。``overrides`` 与默认值只做兜底，不参与严格校验。
    """
    merged: dict[str, Any] = dict(overrides or {})
    merged.update(raw)
    directory = data_dir or paths.default_data_dir()
    values: dict[str, Any] = {}
    context: dict[str, Any] = {"data_dir": directory}
    warnings: list[str] = []
    errors: list[str] = []
    warns_by_key: dict[str, list[str]] = {}

    order = list(fields.SERVER_KEYS)
    order += [key for key in fields.KEY_ORDER if key not in order]
    for key in order:
        if key in raw:
            value, strict_here = raw[key], strict or key in strict_keys
        else:
            value, strict_here = merged.get(key, fields.default_of(key)), False
        cleaned = fields.clean_field(key, value, strict_here, context=context)
        values[fields.attr_of(key)] = cleaned.value
        context[key] = cleaned.value
        if cleaned.warnings:
            warnings.extend(cleaned.warnings)
            warns_by_key.setdefault(key, []).extend(cleaned.warnings)
        errors.extend(cleaned.errors)

    bind_error = warns_by_key.get("web_bind_host", [""])[0]
    settings = dict(values)
    settings["WEB_BIND_HOST_ERROR"] = bind_error
    return Settings(warnings=tuple(warnings), errors=tuple(errors), **settings)


def write_json(path: str, payload: Mapping[str, Any]) -> None:
    """原子写：先写同目录临时文件再 ``os.replace``，中途断电不会留下半个 JSON。

    只写 ``fields`` 声明过的键，顺序照声明顺序；未知键（包括示例模板里的
    ``_comment*``）不落盘——配置文件里只该有「键: 值」。
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle_fd, temporary = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=directory)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            ordered = {key: payload[key] for key in fields.KEY_ORDER if key in payload}
            json.dump(ordered, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class ConfigStore:
    """一个 config.json 的读、校验、写与通知。线程安全，惰性加载。"""

    def __init__(
        self,
        path: str,
        overrides: Mapping[str, Any] | None = None,
        *,
        inherit: Iterable[Subscriber] = (),
        generate: bool = True,
    ) -> None:
        self._path = os.path.abspath(path)
        self._overrides = dict(overrides or {})
        # 首次启动按示例生成 config.json（正式运行要这个）；临时 store 不要，
        # 否则测试目录里会凭空多出一份示例配置
        self._generate = generate
        self._lock = threading.RLock()
        self._settings: Settings | None = None
        self._generated = False
        self._subscribers: list[Subscriber] = list(inherit)

    # ---- 只读接口 -------------------------------------------------------
    @property
    def path(self) -> str:
        return self._path

    @property
    def generated(self) -> bool:
        """config.json 是不是本次按示例生成的（首次启动提示用）。"""
        self.settings
        return self._generated

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._ensure_loaded_locked()

    def data_dir(self) -> str:
        """配置文件所在目录：日志、pid、缓存都跟它走。"""
        return os.path.dirname(self._path) or "."

    def read_raw(self) -> dict[str, Any]:
        """只返回 ``fields`` 声明过的键；文件不存在按空配置。

        示例模板里的 ``_comment*`` 与手工加进来的未知键都不参与配置，也不回给面板。
        """
        try:
            with open(self._path, encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: value for key, value in data.items() if key in fields.KEYS}

    def subscribe(self, callback: Subscriber) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def subscribers(self) -> tuple[Subscriber, ...]:
        with self._lock:
            return tuple(self._subscribers)

    # ---- 写接口 ---------------------------------------------------------
    def write_raw(self, payload: Mapping[str, Any]) -> None:
        write_json(self._path, payload)

    def update(self, payload: Mapping[str, Any]) -> UpdateResult:
        """严格校验后落盘，再通知 HOT 变更；未声明的键不写进文件。

        严格只针对这次提交的键：盘上原有的非法值不会挡住对别的键的保存，它继续按
        回退规则生效（回退原因在 ``settings.warnings``/``errors`` 里）。
        """
        incoming = dict(payload)
        with self._lock:
            self._ensure_loaded_locked()
            current = self.read_raw()
            merged = dict(current)
            merged.update(incoming)
            settings = build_settings(
                merged,
                self._overrides,
                self.data_dir(),
                strict_keys=frozenset(key for key in incoming if key in fields.KEYS),
            )
            self.write_raw(merged)
            previous = self._settings
            self._settings = settings
            changed = self._changed_keys(previous, settings, incoming)
            hot_changed = frozenset(key for key in changed if fields.is_hot(key))
            restart = tuple(key for key in incoming if fields.is_restart(key))
        self._notify(settings, hot_changed)
        return UpdateResult(settings=settings, restart_required=restart)

    def replace(self, data: Mapping[str, Any]) -> UpdateResult:
        """从备份恢复：与现有文件合并后整体重校验，语义同 :meth:`update`。"""
        return self.update(data)

    # ---- 内部 -----------------------------------------------------------
    def _ensure_loaded_locked(self) -> Settings:
        if self._settings is not None:
            return self._settings
        raw = self.read_raw()
        if self._generate and not os.path.isfile(self._path):
            raw = self._generate_from_example()
        self._settings = build_settings(raw, self._overrides, self.data_dir())
        return self._settings

    def _generate_from_example(self) -> dict[str, Any]:
        """首次启动：按示例里的默认值生成 config.json，返回它的内容（不参与严格校验）。

        示例是给人看的模板，带 ``_comment*``；写盘经 :func:`write_json` 过滤，配置
        文件里只留「键: 值」。
        """
        example = paths.find_example()
        if example is None:
            raise FileNotFoundError(
                "找不到 config.json，也没有可用的 config.example.json。"
                f"请把 config.example.json 复制为 config.json，放到 {paths.default_data_dir()}。"
            )
        with open(example, encoding="utf-8-sig") as handle:
            template = json.load(handle)
        os.makedirs(self.data_dir(), exist_ok=True)
        self.write_raw(template if isinstance(template, dict) else {})
        self._generated = True
        return self.read_raw()

    def _changed_keys(
        self, previous: Settings | None, settings: Settings, incoming: Mapping[str, Any]
    ) -> set[str]:
        changed: set[str] = set()
        for key in incoming:
            if key not in fields.KEYS:
                continue
            attr = fields.attr_of(key)
            if previous is None or getattr(previous, attr) != getattr(settings, attr):
                changed.add(key)
        return changed

    def _notify(self, settings: Settings, changed: frozenset) -> None:
        for callback in self.subscribers():
            try:
                callback(settings, changed)
            except Exception as exc:  # 一个订阅者出错不能连累保存
                log.exception("配置订阅回调失败：%s", exc)
