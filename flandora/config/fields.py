"""配置字段声明与唯一一份取值校验。

每项配置在这里声明五件事：``config.json`` 里的键名、``Settings`` 上的属性名、
策略标签、默认值与一句话说明。策略标签决定「改完怎么生效」：HOT 立刻生效，RESTART
要重启桥接才成立，SECRET 表示算凭据（备份默认剔除、恢复时不覆盖本地现值）；HOT 与
RESTART 互斥，SECRET 与任一者叠加。

``desc`` 是配置说明的唯一来源：``config.example.json`` 的 ``_comment_<key>`` 与面板
「高级」页的说明都从它派生，改说明只改这里。

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
# 发送方式：auto 先按回车发，发不出去再点「发送」按钮并记住结论
SEND_KEYS = ("auto", "enter", "button")

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


def _clean_text(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return Cleaned(_as_text(value))


@dataclass(frozen=True)
class Field:
    """一项配置的声明。``clean`` 默认按文本原样取用，``desc`` 是唯一一份说明。"""

    key: str
    attr: str
    kinds: frozenset[str]
    default: Any = ""
    clean: Cleanable = _clean_text
    desc: str = ""


_FLAG_ON = frozenset({"1", "true", "yes", "on"})
_FLAG_OFF = frozenset({"0", "false", "no", "off"})


def _where(context: Mapping) -> str:
    """回退原因里带上字段名，读日志的人不用猜是哪一项。"""
    key = context.get("_key")
    return f"{key} " if key else ""


def _number(
    value: Any,
    default: Any,
    *,
    what: str,
    strict: bool,
    context: Mapping,
    low: float | None = None,
    high: float | None = None,
    convert: Callable[[Any], Any] = int,
) -> Cleaned:
    """数值字段的统一校验：布尔与空值不算数字，越界与解析失败都算非法。

    严格模式（面板保存）直接拒绝；宽松模式（读文件）退回这一项声明的默认值并记一条
    原因——静默把 ``"abc"`` 变成端口 4950，用户会以为保存成功了。
    """
    parsed: Any = None
    ok = not isinstance(value, bool) and value is not None and value != ""
    if ok:
        try:
            parsed = convert(value)
        except (TypeError, ValueError):
            ok = False
    if ok and low is not None and parsed < low:
        ok = False
    if ok and high is not None and parsed > high:
        ok = False
    if ok:
        return Cleaned(parsed)
    message = f"{_where(context)}{what}不合法：{value!r}"
    if strict:
        raise ConfigError(message)
    return Cleaned(default, warnings=(f"{message}，已按 {default!r} 处理",))


def _clean_flag(value: Any, strict: bool, context: Mapping) -> Cleaned:
    """开关只认明确的真假写法：布尔、0/1、true/false（不分大小写）。

    宽松模式（读文件）下别的取值退回声明的默认值并记一条原因；严格模式（面板保存）
    直接拒绝——``"false"`` 在 Python 里是真值，静默按真处理会与用户写的相反。
    """
    if isinstance(value, bool):
        return Cleaned(value)
    if isinstance(value, int) and value in (0, 1):
        return Cleaned(bool(value))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _FLAG_ON:
            return Cleaned(True)
        if text in _FLAG_OFF:
            return Cleaned(False)
    default = bool(context.get("_default"))
    message = f"{_where(context)}开关只认 true/false（或 1/0），收到 {value!r}"
    if strict:
        raise ConfigError(message)
    return Cleaned(default, warnings=(f"{message}，已按{'开' if default else '关'}处理",))


def _clean_plain_int(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return _number(value, context.get("_default"), what="整数", strict=strict, context=context)


def _clean_poll_seconds(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return _number(
        value,
        context.get("_default"),
        what="秒数",
        strict=strict,
        context=context,
        low=0.1,
        convert=float,
    )


def _clean_web_port(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return _number(
        value,
        context.get("_default"),
        what="端口",
        strict=strict,
        context=context,
        low=1,
        high=65535,
    )


def _clean_ob_port(value: Any, strict: bool, context: Mapping) -> Cleaned:
    return _number(
        value,
        context.get("_default"),
        what="端口",
        strict=strict,
        context=context,
        low=1,
        high=65535,
    )


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


def _clean_send_key(value: Any, strict: bool, context: Mapping) -> Cleaned:
    raw = _as_text(value).strip().lower() or "auto"
    if raw in SEND_KEYS:
        return Cleaned(raw)
    if strict:
        raise ConfigError("send_key 只能是 auto、enter 或 button")
    return Cleaned(
        "auto",
        warnings=(
            f"send_key={raw!r} 不认识，已按 auto 处理"
            "（可选：auto = 自动试出来 / enter = 回车 / button = 点发送按钮）",
        ),
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
    """群 ID → 群名映射；留空按空表，写了别的类型严格模式拒绝（发送到群会退回群 ID）。"""
    if isinstance(value, dict):
        return Cleaned(dict(value))
    if value is None:
        return Cleaned({})
    message = f"{_where(context)}应为对象（群 ID → 群名），收到 {type(value).__name__}"
    if strict:
        raise ConfigError(message)
    return Cleaned({}, warnings=(f"{message}，已按空表处理",))


FIELDS: tuple[Field, ...] = (
    # OneBot v11 服务端：ob_networks 为空时按这三项造默认入口，所以先净它们
    Field(
        key="ob_server_host",
        attr="OB_SERVER_HOST",
        kinds=_HOT,
        default="127.0.0.1",
        desc="OneBot v11 服务端的监听地址；ob_networks 为空时按 host / port / token 起一个"
        "默认入口，都在本机就保持回环。",
    ),
    Field(
        key="ob_server_port",
        attr="OB_SERVER_PORT",
        kinds=_HOT,
        default=7999,
        clean=_clean_ob_port,
        desc="OneBot v11 服务端端口；ob_networks 为空时用它起默认入口，客户端连"
        " ws://127.0.0.1:<端口>/。",
    ),
    Field(
        key="ob_server_token",
        attr="OB_SERVER_TOKEN",
        kinds=_HOT_SECRET,
        desc="握手 token，留空 = 不校验鉴权（默认只监听回环，保持留空即可）；把入口放到对外"
        "地址时填一串随机字符，并在客户端的 OneBot 配置里填同一个值。",
    ),
    Field(
        key="ob_networks",
        attr="OB_NETWORKS",
        kinds=_HOT,
        default=None,
        clean=_clean_networks,
        desc="OneBot v11 网络入口数组，可同时挂多个。type 取 ws_server（等客户端连进来）、"
        "http_server（HTTP API）、ws_client（主动连框架的反向 WS，填 url）、http_client"
        "（把事件 POST 到框架，填 url）；"
        "role 取 api 只收动作 / event 只推事件 / universal 两者都做；message_format 留空"
        "跟随 ob_message_format。留空或删掉时按 ob_server_host / ob_server_port / "
        "ob_server_token 起一个 WS 服务端。改完保存即刻生效：新增的入口起、删掉的断开、"
        "改过的按新配置重建，没动的连接不受影响。",
    ),
    Field(
        key="ob_message_format",
        attr="OB_MESSAGE_FORMAT",
        kinds=_HOT,
        default="array",
        clean=_clean_message_format,
        desc="事件里 message 字段的格式：array = 消息段数组（多数客户端用这个），string = "
        "CQ 码字符串；raw_message 始终是 CQ 码。发送方向两种都收，另可用 auto_escape "
        "把字符串按纯文本发。",
    ),
    Field(
        key="ob_heartbeat_seconds",
        attr="OB_HEARTBEAT_SECONDS",
        kinds=_HOT,
        default=30,
        clean=_clean_heartbeat,
        desc="心跳间隔（秒），0 = 不发心跳，非 0 时取 5–3600。连上先推一条 "
        "lifecycle(connect)，之后按这个间隔推 meta_event/heartbeat，桥接停止时推 "
        "lifecycle(disable)。",
    ),
    # 控制面板
    Field(
        key="web_port",
        attr="WEB_PORT",
        kinds=_RESTART,
        default=4950,
        clean=_clean_web_port,
        desc="面板端口，默认 4950。",
    ),
    Field(
        key="web_bind_host",
        attr="WEB_BIND_HOST",
        kinds=_RESTART,
        default="127.0.0.1",
        clean=_clean_bind_host,
        desc="面板监听地址。127.0.0.1 = 只有本机能打开（默认）；0.0.0.0 = 局域网里其它机器"
        "也能打开，此时必须先设面板密码，否则谁都能改这份配置。改这里需要重启桥接。",
    ),
    Field(
        key="webui_password",
        attr="WEBUI_PASSWORD",
        kinds=_HOT_SECRET,
        desc="面板登录密码的 PBKDF2 哈希，由面板保存时写入；留空 = 不校验（本机任何进程"
        "都能打开面板）。不要手填明文，面板认不出明文会当成没设密码。",
    ),
    Field(
        key="webui_tls",
        attr="WEBUI_TLS",
        kinds=_RESTART,
        default=False,
        clean=_clean_flag,
        desc="面板是否走 HTTPS。开启前要先把证书与私钥装好（在 面板 的「安全与服务」里"
        "粘贴 PEM），或在这里填 webui_tls_cert_file / webui_tls_key_file 指定已有的 PEM "
        "文件。开启后证书加载不了时面板不会启动，也不会退回 HTTP。改这里需要重启桥接。",
    ),
    Field(
        key="webui_tls_cert_file",
        attr="WEBUI_TLS_CERT_FILE",
        kinds=_RESTART,
        default="",
        clean=_clean_cert_file,
        desc="证书 PEM 路径，留空 = 数据目录下的 webui_cert.pem。",
    ),
    Field(
        key="webui_tls_key_file",
        attr="WEBUI_TLS_KEY_FILE",
        kinds=_RESTART,
        default="",
        clean=_clean_key_file,
        desc="私钥 PEM 路径，留空 = 数据目录下的 webui_key.pem（写入时权限收到 0600）。",
    ),
    # 微信与收件
    Field(
        key="client_attachments",
        attr="CLIENT_ATTACHMENTS",
        kinds=_HOT,
        desc="发送方向的附件目录：图片段与文件段只给文件名时到这里找（也找它的 "
        "wechat_images 子目录）。留空 = 图片段只认 base64://，文件段只认本机路径、"
        "file:// 或 base64://。",
    ),
    Field(
        key="wechat_hwnd",
        attr="WECHAT_HWND",
        kinds=_HOT,
        default=0,
        clean=_clean_plain_int,
        desc="多开微信时指定机器人所在窗口的句柄，0 = 自动取第一个微信窗口。微信重启后句柄"
        "会变，需要在 面板 里重新绑定。",
    ),
    Field(
        key="wechat_files_root",
        attr="WECHAT_FILES_ROOT",
        kinds=_HOT,
        desc="微信数据根目录，用于识别当前登录账号并在切换账号时作废缓存。留空 = 自动定位："
        "先看系统「文档」目录下的 xwechat_files（文档库被重定向到别的盘也能正确解析），"
        "再看微信自己在「设置 → 文件管理」里记下的存储位置（改到别的盘也认得出）。"
        "自动定位失手或同时跑多个微信号时才需要手填。",
    ),
    Field(
        key="allow_foreground_steal",
        attr="ALLOW_FOREGROUND_STEAL",
        kinds=_HOT,
        default=False,
        clean=_clean_flag,
        desc="发送消息时是否允许把微信窗口提到前台。默认 false 时发送功能整体不可用（日志里"
        "带失败原因），微信不会被任何窗口消息打扰；设为 true 后发送可用，代价是每条消息"
        "发出时前台会短暂切到微信，发完立刻切回你刚才在用的窗口（实测切回耗时 3–11 毫秒）。"
        "微信只要收到投递进来的鼠标或键盘消息就会升到前台，所以这个开关管的是整个发送"
        "通道，文本、图片、文件都走它。开了 isolated_desktop 之后不需要：那种模式下前台切换"
        "只发生在另一张桌面上。",
    ),
    Field(
        key="send_key",
        attr="SEND_KEY",
        kinds=_HOT,
        default="auto",
        clean=_clean_send_key,
        desc="发送消息时用哪种方式。auto（默认）先按回车，没发出去就清掉输入框、重新"
        "输入后点输入框右下角的「发送」按钮，成功就记住、后续照此发送；enter 固定按回车；"
        "button 固定点「发送」按钮。微信「设置 → 快捷键」里可以把发送键换成 Ctrl+回车，"
        "换了之后回车只会插一个换行、消息留在输入框里——点按钮与这条设置无关，所以选 "
        "button 就不必每次都等一次失败。",
    ),
    Field(
        key="group_name_map",
        attr="GROUP_NAME_MAP",
        kinds=_HOT,
        default=None,
        clean=_clean_group_map,
        desc="群 ID → 微信里显示的群名。微信搜索框搜不到 @chatroom 结尾的群 ID，发送到群时"
        "要靠这张表换成群名。留空即可：群里来过一条消息后会自动学会群名，这里只用于预先"
        "指定或纠正。",
    ),
    Field(
        key="wx_key_file",
        attr="WX_KEY_FILE",
        kinds=_RESTART,
        desc="微信数据库口令文件，留空 = 配置文件同目录下的 wx_key.dat。该文件由取钥器写入，"
        "口令用 Windows DPAPI 加密（只能在同一台机器的同一用户下解开，拷到别的机器无效）。"
        "口令与登录的微信号一一对应，换号后要重新取钥。",
    ),
    Field(
        key="wx_work_dir",
        attr="WX_WORK_DIR",
        kinds=_RESTART,
        desc="解密结果与游标的存放目录，留空 = 配置文件同目录下的 wx_cache。这里只放解密后"
        "的副本，原始库始终是只读的。",
    ),
    Field(
        key="wx_backfill_seconds",
        attr="WX_BACKFILL_SECONDS",
        kinds=_RESTART,
        default=0,
        clean=_clean_plain_int,
        desc="启动时把最近多少秒内的历史消息也收进来（按会话逐个从尾部判断）。0 = 不回放，"
        "不会把旧聊天记录灌给机器人。",
    ),
    Field(
        key="wx_poll_seconds",
        attr="WX_POLL_SECONDS",
        kinds=_RESTART,
        default=1.0,
        clean=_clean_poll_seconds,
        desc="轮询本地库的间隔（秒）。默认 1 秒，新消息通常在这个延迟内被发现。",
    ),
    Field(
        key="wx_image_key_file",
        attr="WX_IMAGE_KEY_FILE",
        kinds=_RESTART,
        desc="微信图片密钥文件，留空 = 配置文件同目录下的 wx_image_key.json。缺它时图片消息"
        "退回占位符（文字消息不受影响）。密钥只在微信解码图片的那几秒驻留内存，所以要在 "
        "面板 的取钥向导里按提示让微信打开一张没看过的图片时抓（源码开发者也可以直接跑 "
        "tools/imagekey.py）。",
    ),
    Field(
        key="wxkey_manual_rva",
        attr="WXKEY_MANUAL_RVA",
        kinds=_RESTART,
        default=0,
        clean=_clean_plain_int,
        desc="取数据库口令时手动指定的函数地址，0 = 自动定位。自动定位靠识别微信 DLL 里的"
        "调用形态，微信改版后可能失手；那时在取钥向导的「高级」里填一个地址即可继续，"
        "形式同 0x6148a0，不必正好是函数开头，落在函数里就行。地址在向导定位失败时会"
        "连同当时的 DLL 版本一起显示，便于排查。",
    ),
    Field(
        key="isolated_desktop",
        attr="ISOLATED_DESKTOP",
        kinds=_RESTART,
        default=True,
        clean=_clean_flag,
        desc="让微信跑在一张独立的 Windows 桌面上，这样向它投递输入时切换的前台是那张桌面"
        "的，用户自己的桌面完全不受打扰，发送通道因此可以长期开着。这是默认值。前提是"
        "微信必须由本程序启动（Windows 上微信单实例互斥，用户自己先开了微信时隔离实例"
        "只会停在登录页）；隔离桌面用户看不见，首次登录要在 面板 里看实况截图、用手机"
        "扫码或确认完成；发送期间微信不显示在用户屏幕上（这正是目的）。设为 false 则"
        "微信由用户自己启动，发送时它会短暂到前台、发完归还。",
    ),
    Field(
        key="isolated_desktop_name",
        attr="ISOLATED_DESKTOP_NAME",
        kinds=_RESTART,
        default="FlandoraSandbox",
        desc="隔离桌面的名字，一般不用改；要同时跑多份互不干扰的实例时才需要区分。",
    ),
    Field(
        key="wechat_exe",
        attr="WECHAT_EXE",
        kinds=_RESTART,
        desc="微信主程序路径（Weixin.exe）。留空 = 自动探测常见安装位置。装到不常见的"
        "位置时手填完整路径，例如 D:\\Program Files\\Tencent\\Weixin\\Weixin.exe。",
    ),
    # 日志
    Field(
        key="log_max_mb",
        attr="LOG_MAX_MB",
        kinds=_HOT,
        default=8,
        clean=_clean_log_max,
        desc="单份日志的上限（1–1024 MB）。写满就轮转成 flandora.log.1，旧的依次后退。"
        "留空或删掉按 8 MB 处理，在 面板 的「存储」里改即时生效，不必重启。",
    ),
    Field(
        key="log_backup_count",
        attr="LOG_BACKUP_COUNT",
        kinds=_HOT,
        default=5,
        clean=_clean_log_backups,
        desc="轮转后保留几份历史日志（1–20）。总占用上限约为「单份上限 ×（保留份数 + 1）」。"
        "在 面板 的「存储」里改即时生效。",
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


def _schema_type(value: Any) -> str:
    """默认值 → 面板用的类型标签：先判 bool 再判 int（bool 是 int 的子类）。

    浮点与尚未定值的 None 没有单独的标签，和 list/dict 一样归 "json"。
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, str):
        return "str"
    return "json"


def schema() -> list[dict[str, Any]]:
    """按声明顺序给出面板「高级」页要用的字段说明与生效方式。

    说明只从这里取：``config.example.json`` 的 ``_comment_<key>`` 与
    ``GET /api/config/schema`` 都由它派生。
    """
    return [
        {
            "key": spec.key,
            "type": _schema_type(spec.default),
            "default": spec.default,
            "hot": KIND_HOT in spec.kinds,
            "restart": KIND_RESTART in spec.kinds,
            "secret": KIND_SECRET in spec.kinds,
            "desc": spec.desc,
        }
        for spec in FIELDS
    ]


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
    # 把这一项自己的键名与声明默认值一并交给 cleaner：非法值该退回哪个值、原因里写哪个
    # 字段名，由 cleaner 说清楚（见 _clean_flag / _number）。
    here = dict(context or {})
    here["_key"] = key
    here["_default"] = spec.default
    return spec.clean(value, strict, here)
