"""配置字段声明与唯一一份取值校验。

每项配置在这里声明四件事：``config.json`` 里的键名、``Settings`` 上的属性名、
策略标签与默认值。策略标签决定「改完怎么生效」：HOT 立刻生效，RESTART 要重启桥接
才成立，SECRET 表示算凭据（备份默认剔除、恢复时不覆盖本地现值）；HOT 与 RESTART
互斥，SECRET 与任一者叠加。

``clean_field()`` 是文件加载与面板保存共用的唯一入口：``strict=True`` 面向面板
（非法值直接拒绝，消息原样回给浏览器），``strict=False`` 面向文件（按既定规则
回退，把回退原因收成告警）。两边规则必须一模一样，差别只在「拒绝」还是「回退」。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

KIND_HOT = "HOT"
KIND_RESTART = "RESTART"
KIND_SECRET = "SECRET"

# web_bind_host 只认回环与全网卡：桥接没有别的网卡语义，写错地址只会让人以为面板没起
ALLOWED_BIND_HOSTS = ("127.0.0.1", "0.0.0.0")
MESSAGE_FORMATS = ("array", "string")

_HOT = frozenset({KIND_HOT})
_RESTART = frozenset({KIND_RESTART})
_HOT_SECRET = frozenset({KIND_HOT, KIND_SECRET})
_RESTART_SECRET = frozenset({KIND_RESTART, KIND_SECRET})


class ConfigError(ValueError):
    """配置值不合法。消息面向人，面板会原样回给浏览器。"""


@dataclass(frozen=True)
class Cleaned:
    """一项配置校验后的结果：生效值，以及需要打出来的回退原因。"""

    value: Any
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


Cleanable = Callable[[Any, bool, Mapping], Cleaned]


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return Cleaned(_as_text(value))


@dataclass(frozen=True)
class Field:
    """一项配置的声明。``clean`` 默认按文本原样取用。"""

    key: str
    attr: str
    kinds: frozenset[str]
    default: Any = ""
    clean: Cleanable = _clean_text


def _clean_flag(value: Any, strict: bool, context: Mapping) -> Cleaned:
    """开关按 Python 真值判定：``"false"`` 这类字符串与旧行为一样算真。"""
    return Cleaned(bool(value))


def _clean_plain_int(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return Cleaned(_as_int(value, 0))


def _clean_poll_seconds(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return Cleaned(_as_float(value, 1.0))


def _clean_web_port(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return Cleaned(_as_int(value, 4950))


def _clean_ob_port(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return Cleaned(_as_int(value, 7999))


def _clean_cert_file(value: Any, strict: bool, context: Mapping) -> Cleaned:
    text = _as_text(value).strip()
    if text:
        return Cleaned(text)
    return Cleaned(os.path.join(context["data_dir"], "webui_cert.pem"))


def _clean_key_file(value: Any, strict: bool, context: Mapping) -> Cleaned:
    text = _as_text(value).strip()
    if text:
        return Cleaned(text)
    return Cleaned(os.path.join(context["data_dir"], "webui_key.pem"))


def _clean_bind_host(value: Any, strict: bool, context: Mapping) -> Cleaned:
    raw = _as_text(value).strip() or "127.0.0.1"
    if raw in ALLOWED_BIND_HOSTS:
        return Cleaned(raw)
    if strict:
        raise ConfigError("web_bind_host 只能是 127.0.0.1 或 0.0.0.0")
    return Cleaned(
        "127.0.0.1",
        warnings=(f"web_bind_host={raw!r} 只能是 127.0.0.1 或 0.0.0.0，已按 127.0.0.1 处理",),
    )


def _clean_message_format(value: Any, strict: bool, context: Mapping) -> Cleaned:
    raw = _as_text(value).strip().lower() or "array"
    if raw in MESSAGE_FORMATS:
        return Cleaned(raw)
    if strict:
        raise ConfigError("ob_message_format 只能是 array 或 string")
    return Cleaned(
        "array",
        warnings=(f"ob_message_format={raw!r} 不认识，已按 array 处理（可选：array / string）",),
    )


def _clean_heartbeat(value: Any, strict: bool, context: Mapping) -> Cleaned:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        if strict:
            raise ConfigError("ob_heartbeat_seconds 必须是整数") from None
        return Cleaned(30, warnings=(f"ob_heartbeat_seconds={value!r} 不是整数，已按 30 秒处理",))
    if seconds != 0 and not 5 <= seconds <= 3600:
        if strict:
            raise ConfigError("ob_heartbeat_seconds 取 0（不发心跳）或 5–3600 秒")
        return Cleaned(
            30,
            warnings=(f"ob_heartbeat_seconds={seconds} 超出 5–3600（0 表示不发），已按 30 秒处理",),
        )
    return Cleaned(seconds)


def _clean_log_max(value: Any, strict: bool, context: Mapping) -> Cleaned:
    try:
        megabytes = int(value)
    except (TypeError, ValueError):
        if strict:
            raise ConfigError("日志上限与保留份数必须是整数") from None
        return Cleaned(8, warnings=(f"log_max_mb={value!r} 不是整数，已按 8 MB 处理",))
    if not 1 <= megabytes <= 1024:
        if strict:
            raise ConfigError("日志单份上限取 1–1024（MB）")
        return Cleaned(8, warnings=(f"log_max_mb={megabytes} 超出 1–1024，已按 8 MB 处理",))
    return Cleaned(megabytes)


def _clean_log_backups(value: Any, strict: bool, context: Mapping) -> Cleaned:
    try:
        count = int(value)
    except (TypeError, ValueError):
        if strict:
            raise ConfigError("日志上限与保留份数必须是整数") from None
        return Cleaned(5, warnings=(f"log_backup_count={value!r} 不是整数，已按 5 份处理",))
    if not 1 <= count <= 20:
        if strict:
            raise ConfigError("日志保留份数取 1–20")
        return Cleaned(5, warnings=(f"log_backup_count={count} 超出 1–20，已按 5 份处理",))
    return Cleaned(count)


def _effective_networks(nodes: list, context: Mapping) -> list:
    """入口清空等于什么都不监听：生效值退回按通用设置造的那一个。

    文件里仍然保留用户写的空数组，只有生效值带兜底（面板要如实显示「配空了」）。
    """
    if nodes:
        return list(nodes)
    from .. import ob_network

    return [
        ob_network.default_node(
            context["ob_server_host"], context["ob_server_port"], context["ob_server_token"]
        )
    ]


def _clean_networks(value: Any, strict: bool, context: Mapping) -> Cleaned:
    """网络入口：非法值拒绝或退回通用设置入口，规则两边一致。

    必须惰性导入 ``ob_network``：导入配置本身不该把网络层拉起来。
    """
    from .. import ob_network

    host = context["ob_server_host"]
    port = context["ob_server_port"]
    if strict:
        try:
            nodes = ob_network.normalize_nodes(value)
        except ob_network.NetworkConfigError as exc:
            raise ConfigError(f"网络入口配置不合法：{exc}") from exc
        return Cleaned(_effective_networks(nodes, context))
    if not value:
        return Cleaned(_effective_networks([], context))
    try:
        return Cleaned(_effective_networks(ob_network.normalize_nodes(value), context))
    except Exception as exc:  # 入口写错不能让桥接静默什么都不监听
        return Cleaned(
            _effective_networks([], context),
            errors=(
                f"ob_networks 配置不合法（{type(exc).__name__}: {exc}），"
                f"已退回单个 WS 服务端 {host}:{port}",
            ),
        )


def _clean_group_map(value: Any, strict: bool, context: Mapping) -> Cleaned:
    """群 ID → 群名映射；不是对象时按空表处理（发送到群时退回群 ID）。"""
    return Cleaned(dict(value) if isinstance(value, dict) else {})


FIELDS: tuple[Field, ...] = (
    # OneBot v11 服务端：ob_networks 为空时按这三项造默认入口，所以先净它们
    Field(key="ob_server_host", attr="OB_SERVER_HOST", kinds=_RESTART, default="127.0.0.1"),
    Field(
        key="ob_server_port",
        attr="OB_SERVER_PORT",
        kinds=_RESTART,
        default=7999,
        clean=_clean_ob_port,
    ),
    Field(key="ob_server_token", attr="OB_SERVER_TOKEN", kinds=_RESTART_SECRET),
    Field(
        key="ob_networks", attr="OB_NETWORKS", kinds=_RESTART, default=None, clean=_clean_networks
    ),
    Field(
        key="ob_message_format",
        attr="OB_MESSAGE_FORMAT",
        kinds=_RESTART,
        default="array",
        clean=_clean_message_format,
    ),
    Field(
        key="ob_heartbeat_seconds",
        attr="OB_HEARTBEAT_SECONDS",
        kinds=_RESTART,
        default=30,
        clean=_clean_heartbeat,
    ),
    # 控制面板
    Field(key="web_port", attr="WEB_PORT", kinds=_RESTART, default=4950, clean=_clean_web_port),
    Field(
        key="web_bind_host",
        attr="WEB_BIND_HOST",
        kinds=_RESTART,
        default="127.0.0.1",
        clean=_clean_bind_host,
    ),
    Field(
        key="webui_auto_open",
        attr="WEBUI_AUTO_OPEN",
        kinds=_RESTART,
        default=True,
        clean=_clean_flag,
    ),
    Field(key="webui_password", attr="WEBUI_PASSWORD", kinds=_HOT_SECRET),
    Field(key="webui_tls", attr="WEBUI_TLS", kinds=_RESTART, default=False, clean=_clean_flag),
    Field(
        key="webui_tls_cert_file",
        attr="WEBUI_TLS_CERT_FILE",
        kinds=_RESTART,
        default="",
        clean=_clean_cert_file,
    ),
    Field(
        key="webui_tls_key_file",
        attr="WEBUI_TLS_KEY_FILE",
        kinds=_RESTART,
        default="",
        clean=_clean_key_file,
    ),
    # 微信与收件
    Field(key="client_attachments", attr="CLIENT_ATTACHMENTS", kinds=_RESTART),
    Field(key="wechat_hwnd", attr="WECHAT_HWND", kinds=_HOT, default=0, clean=_clean_plain_int),
    Field(key="wechat_files_root", attr="WECHAT_FILES_ROOT", kinds=_HOT),
    Field(
        key="allow_foreground_steal",
        attr="ALLOW_FOREGROUND_STEAL",
        kinds=_HOT,
        default=False,
        clean=_clean_flag,
    ),
    Field(
        key="group_name_map",
        attr="GROUP_NAME_MAP",
        kinds=_HOT,
        default=None,
        clean=_clean_group_map,
    ),
    Field(key="wx_key_file", attr="WX_KEY_FILE", kinds=_RESTART),
    Field(key="wx_work_dir", attr="WX_WORK_DIR", kinds=_RESTART),
    Field(
        key="wx_backfill_seconds",
        attr="WX_BACKFILL_SECONDS",
        kinds=_RESTART,
        default=0,
        clean=_clean_plain_int,
    ),
    Field(
        key="wx_poll_seconds",
        attr="WX_POLL_SECONDS",
        kinds=_RESTART,
        default=1.0,
        clean=_clean_poll_seconds,
    ),
    Field(key="wx_image_key_file", attr="WX_IMAGE_KEY_FILE", kinds=_RESTART),
    Field(
        key="wxkey_manual_rva",
        attr="WXKEY_MANUAL_RVA",
        kinds=_RESTART,
        default=0,
        clean=_clean_plain_int,
    ),
    Field(
        key="isolated_desktop",
        attr="ISOLATED_DESKTOP",
        kinds=_RESTART,
        default=True,
        clean=_clean_flag,
    ),
    Field(
        key="isolated_desktop_name",
        attr="ISOLATED_DESKTOP_NAME",
        kinds=_RESTART,
        default="FlandoraSandbox",
    ),
    Field(key="wechat_exe", attr="WECHAT_EXE", kinds=_RESTART),
    # 日志
    Field(key="log_max_mb", attr="LOG_MAX_MB", kinds=_HOT, default=8, clean=_clean_log_max),
    Field(
        key="log_backup_count",
        attr="LOG_BACKUP_COUNT",
        kinds=_HOT,
        default=5,
        clean=_clean_log_backups,
    ),
)

_BY_KEY: dict[str, Field] = {spec.key: spec for spec in FIELDS}
_BY_ATTR: dict[str, Field] = {spec.attr: spec for spec in FIELDS}
KEYS: frozenset[str] = frozenset(_BY_KEY)
# 声明顺序：校验要按它走一遍，ob_server_* 必须先于依赖它们的 ob_networks
KEY_ORDER: tuple[str, ...] = tuple(spec.key for spec in FIELDS)

# ob_networks 的兜底入口要按「先净过」的这三项造，所以它们必须先于 ob_networks 校验
SERVER_KEYS: tuple[str, ...] = ("ob_server_host", "ob_server_port", "ob_server_token")


def attr_of(key: str) -> str:
    return _BY_KEY[key].attr


def default_of(key: str) -> Any:
    return _BY_KEY[key].default


def is_hot(key: str) -> bool:
    spec = _BY_KEY.get(key)
    return bool(spec is not None and KIND_HOT in spec.kinds)


def is_restart(key: str) -> bool:
    spec = _BY_KEY.get(key)
    return bool(spec is not None and KIND_RESTART in spec.kinds)


def hot_keys() -> tuple[str, ...]:
    return tuple(spec.key for spec in FIELDS if KIND_HOT in spec.kinds)


def secret_keys() -> tuple[str, ...]:
    return tuple(spec.key for spec in FIELDS if KIND_SECRET in spec.kinds)


def clean_field(
    key: str, value: Any, strict: bool = False, *, context: Mapping | None = None
) -> Cleaned:
    """校验一项配置，返回生效值与回退原因。

    ``strict=True`` 抛 ``ConfigError``（面板保存）；``strict=False`` 按既有规则回退
    （文件加载）。``context`` 提供需要互相参照的取值与数据目录：ob_networks 的兜底
    入口要用净过的 ob_server_host/port/token，证书与私钥的默认值落在数据目录。
    """
    spec = _BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"未声明的配置项：{key}")
    return spec.clean(value, strict, context or {})
