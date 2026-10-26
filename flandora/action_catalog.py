"""OneBot v11 动作目录：Flandora 支持哪些动作、各自要什么参数、能不能真正执行。

本模块是动作面的唯一真相源。派发层（ob_protocol）用它判断「已知 / 未实现 / 未知」，
面板的调试台、Python SDK 与 MCP Server 用它生成表单与工具清单，因此新增或下线
动作只改这里一处，三处消费方自动跟上。

口径：
- ``implemented=False`` 的动作由派发层统一回 ``failed``，绝不回假成功。微信 PC
  客户端确实没有对应能力（例如群管理、群文件）的，理由写在 ``wip`` 里如实返回给
  调用方，而不是让它以为操作生效了。
- ``read_only`` 只描述「有没有副作用」，MCP 的只读模式据此拒绝写动作。
- ``id`` 类型是 OneBot 的 ``user_id`` / ``group_id``：填整数 ID，也可以直接填微信标识
  （wxid、``xxx@chatroom``），由派发层统一解析，客户端不必自己去换。
- 参数声明只做存在性与类型校验；业务校验（例如群名映射、文件是否存在）留在各自
  handler 里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# retcode 取值（OneBot v11 只约定 0 表示成功，其余由实现方定义，这里固定下来供客户端与文档引用）
RETCODE_OK = 0
RETCODE_BAD_PARAM = 1400
RETCODE_AUTH_FAILED = 1401
RETCODE_NOT_IMPLEMENTED = 1404
RETCODE_UNSUPPORTED = 1405
RETCODE_NOT_FOUND = 1406
RETCODE_INTERNAL = 1500

CAT_INFO = "信息"
CAT_MESSAGE = "消息"
CAT_FRIEND = "好友"
CAT_GROUP_INFO = "群信息"
CAT_GROUP_ADMIN = "群管理"
CAT_REQUEST = "请求"
CAT_EXTEND = "扩展"

CATEGORIES = (
    CAT_INFO,
    CAT_MESSAGE,
    CAT_FRIEND,
    CAT_GROUP_INFO,
    CAT_GROUP_ADMIN,
    CAT_REQUEST,
    CAT_EXTEND,
)

PARAM_TYPES = ("int", "id", "string", "bool", "list", "message")

# 类型的中文说法：`id` 是这一层的专用类型，报错时说清楚它接受什么
TYPE_LABELS = {"id": "整数 ID 或微信标识（wxid / xxx@chatroom）"}


@dataclass(frozen=True)
class Param:
    """一个动作参数的契约。"""

    name: str
    type: str
    required: bool = False
    default: Any = None
    desc: str = ""

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"参数 {self.name} 的类型 {self.type!r} 不在 {PARAM_TYPES} 内")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "desc": self.desc,
        }


@dataclass(frozen=True)
class Action:
    """一个动作的完整定义。"""

    name: str
    category: str
    summary: str
    params: tuple[Param, ...] = ()
    read_only: bool = False
    implemented: bool = True
    wip: str = ""

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"动作 {self.name} 的分类 {self.category!r} 未登记")
        if not self.implemented and not self.wip:
            raise ValueError(f"动作 {self.name} 未实现，必须写明 wip 理由")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "summary": self.summary,
            "readOnly": self.read_only,
            "implemented": self.implemented,
            "wip": self.wip,
            "params": [p.to_dict() for p in self.params],
        }


def P(name: str, type_: str, required: bool = False, default: Any = None, desc: str = "") -> Param:
    """Param 的简写构造，让动作表读起来像一张表。"""
    return Param(name=name, type=type_, required=required, default=default, desc=desc)


NOT_QQ = "微信 PC 客户端不提供对应接口"
NOT_YET = "尚未接入，见 docs/wip.md"

ACTIONS: tuple[Action, ...] = (
    # ---------------------------------------------------------------- 信息
    Action(
        name="get_login_info",
        category=CAT_INFO,
        summary="获取当前登录的微信号与本程序使用的机器人昵称",
        read_only=True,
    ),
    Action(
        name="get_status",
        category=CAT_INFO,
        summary="获取运行状态（在线、收件源与发送器是否就绪）",
        read_only=True,
    ),
    Action(
        name="get_version_info",
        category=CAT_INFO,
        summary="获取版本信息（应用名、版本号、OneBot 协议版本）",
        read_only=True,
    ),
    Action(
        name="can_send_image",
        category=CAT_INFO,
        summary="是否支持发送图片",
        read_only=True,
    ),
    Action(
        name="can_send_record",
        category=CAT_INFO,
        summary="是否支持发送语音",
        read_only=True,
    ),
    Action(
        name="get_contact_id",
        category=CAT_INFO,
        summary="微信标识换成 OneBot 整数 ID（没见过的标识会分配一个并落盘）",
        params=(P("handle", "string", required=True, desc="wxid 或 xxx@chatroom"),),
    ),
    Action(
        name="get_contact_handle",
        category=CAT_INFO,
        summary="OneBot 整数 ID 反查微信标识",
        params=(P("id", "id", required=True),),
        read_only=True,
    ),
    Action(
        name="get_contact_ids",
        category=CAT_INFO,
        summary="列出全部「微信标识 ↔ 整数 ID」映射",
        read_only=True,
    ),
    # ---------------------------------------------------------------- 消息
    Action(
        name="send_msg",
        category=CAT_MESSAGE,
        summary="发送消息，按 message_type 自动选择私聊或群聊",
        params=(
            P("message_type", "string", desc="private 或 group；不传时按 *_id 推断"),
            P("user_id", "id", desc="私聊对象"),
            P("group_id", "id", desc="群聊对象"),
            P("message", "message", required=True, desc="消息段数组或 CQ 码字符串"),
            P("auto_escape", "bool", default=False, desc="为真时按纯文本发送，不解析 CQ 码"),
        ),
    ),
    Action(
        name="send_private_msg",
        category=CAT_MESSAGE,
        summary="发送私聊消息",
        params=(
            P("user_id", "id", required=True),
            P("message", "message", required=True),
            P("auto_escape", "bool", default=False),
        ),
    ),
    Action(
        name="send_group_msg",
        category=CAT_MESSAGE,
        summary="发送群消息",
        params=(
            P("group_id", "id", required=True),
            P("message", "message", required=True),
            P("auto_escape", "bool", default=False),
        ),
    ),
    Action(
        name="delete_msg",
        category=CAT_MESSAGE,
        summary="撤回已发送的消息",
        params=(P("message_id", "int", required=True),),
        implemented=False,
        wip="撤回要右键会话里的消息气泡，UIA 无法按 message_id 定位到某一条；见 docs/needs-user.md",
    ),
    Action(
        name="get_msg",
        category=CAT_MESSAGE,
        summary="按 message_id 取回消息原文（收件与发出去的消息都在存档里）",
        params=(P("message_id", "int", required=True),),
        read_only=True,
    ),
    Action(
        name="get_group_msg_history",
        category=CAT_MESSAGE,
        summary="拉取群聊历史消息",
        params=(
            P("group_id", "id", required=True),
            P("count", "int", default=20, desc="最多 100 条"),
        ),
        read_only=True,
    ),
    Action(
        name="get_friend_msg_history",
        category=CAT_MESSAGE,
        summary="拉取私聊历史消息",
        params=(
            P("user_id", "id", required=True),
            P("count", "int", default=20, desc="最多 100 条"),
        ),
        read_only=True,
    ),
    Action(
        name="send_group_forward_msg",
        category=CAT_MESSAGE,
        summary="发送群聊合并转发",
        params=(
            P("group_id", "id", required=True),
            P("messages", "list", required=True),
        ),
        implemented=False,
        wip=f"{NOT_YET}（微信没有可由外部构造的合并转发入口）",
    ),
    Action(
        name="send_private_forward_msg",
        category=CAT_MESSAGE,
        summary="发送私聊合并转发",
        params=(
            P("user_id", "id", required=True),
            P("messages", "list", required=True),
        ),
        implemented=False,
        wip=f"{NOT_YET}（微信没有可由外部构造的合并转发入口）",
    ),
    Action(
        name="get_forward_msg",
        category=CAT_MESSAGE,
        summary="展开合并转发内容",
        params=(P("message_id", "string", required=True),),
        read_only=True,
        implemented=False,
        wip=NOT_YET,
    ),
    Action(
        name="set_msg_emoji_like",
        category=CAT_MESSAGE,
        summary="给消息贴表情回应",
        params=(P("message_id", "int", required=True), P("emoji_id", "string", required=True)),
        implemented=False,
        wip=NOT_QQ,
    ),
    # ---------------------------------------------------------------- 好友
    Action(
        name="get_friend_list",
        category=CAT_FRIEND,
        summary="获取好友列表（通讯录里的好友；群聊用 get_group_list，公众号与文件传输助手另有动作）",
        read_only=True,
    ),
    Action(
        name="get_stranger_info",
        category=CAT_FRIEND,
        summary="获取任意联系人资料",
        params=(P("user_id", "id", required=True),),
        read_only=True,
    ),
    Action(
        name="get_friend_info",
        category=CAT_FRIEND,
        summary="获取好友资料（get_stranger_info 的别名）",
        params=(P("user_id", "id", required=True),),
        read_only=True,
    ),
    Action(
        name="send_like",
        category=CAT_FRIEND,
        summary="给好友资料页点赞",
        params=(P("user_id", "id", required=True), P("times", "int", default=1)),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="delete_friend",
        category=CAT_FRIEND,
        summary="删除好友",
        params=(P("user_id", "id", required=True),),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_friend_remark",
        category=CAT_FRIEND,
        summary="设置好友备注",
        params=(P("user_id", "id", required=True), P("remark", "string", required=True)),
        implemented=False,
        wip=NOT_QQ,
    ),
    # ---------------------------------------------------------------- 群信息
    Action(
        name="get_group_list",
        category=CAT_GROUP_INFO,
        summary="获取群列表（读通讯录里的群会话）",
        params=(),
        read_only=True,
    ),
    Action(
        name="get_group_info",
        category=CAT_GROUP_INFO,
        summary="获取群资料（群名与成员数来自本地库）",
        params=(P("group_id", "id", required=True),),
        read_only=True,
    ),
    Action(
        name="get_group_member_list",
        category=CAT_GROUP_INFO,
        summary="获取群成员列表（含群主标记）",
        params=(P("group_id", "id", required=True),),
        read_only=True,
    ),
    Action(
        name="get_group_member_info",
        category=CAT_GROUP_INFO,
        summary="获取单个群成员资料",
        params=(
            P("group_id", "id", required=True),
            P("user_id", "id", required=True),
        ),
        read_only=True,
    ),
    Action(
        name="get_group_at_all_remain",
        category=CAT_GROUP_INFO,
        summary="查询剩余 @全体成员 次数",
        params=(P("group_id", "id", required=True),),
        read_only=True,
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="get_group_honor_info",
        category=CAT_GROUP_INFO,
        summary="获取群荣誉（龙王、群聊之火等）",
        params=(P("group_id", "id", required=True), P("type", "string", default="all")),
        read_only=True,
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="get_group_system_msg",
        category=CAT_GROUP_INFO,
        summary="获取群系统消息（待处理入群邀请）",
        read_only=True,
        implemented=False,
        wip=NOT_QQ,
    ),
    # ---------------------------------------------------------------- 群管理
    Action(
        name="set_group_kick",
        category=CAT_GROUP_ADMIN,
        summary="踢出群成员",
        params=(
            P("group_id", "id", required=True),
            P("user_id", "id", required=True),
            P("reject_add_request", "bool", default=False),
        ),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_ban",
        category=CAT_GROUP_ADMIN,
        summary="禁言群成员",
        params=(
            P("group_id", "id", required=True),
            P("user_id", "id", required=True),
            P("duration", "int", default=1800, desc="秒，0 表示解除"),
        ),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_whole_ban",
        category=CAT_GROUP_ADMIN,
        summary="全员禁言",
        params=(P("group_id", "id", required=True), P("enable", "bool", default=True)),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_admin",
        category=CAT_GROUP_ADMIN,
        summary="设置或取消群管理员",
        params=(
            P("group_id", "id", required=True),
            P("user_id", "id", required=True),
            P("enable", "bool", default=True),
        ),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_card",
        category=CAT_GROUP_ADMIN,
        summary="设置群名片",
        params=(
            P("group_id", "id", required=True),
            P("user_id", "id", required=True),
            P("card", "string", default="", desc="空字符串表示清除"),
        ),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_name",
        category=CAT_GROUP_ADMIN,
        summary="修改群名",
        params=(P("group_id", "id", required=True), P("group_name", "string", required=True)),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_leave",
        category=CAT_GROUP_ADMIN,
        summary="退出群聊或解散群",
        params=(P("group_id", "id", required=True), P("is_dismiss", "bool", default=False)),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_special_title",
        category=CAT_GROUP_ADMIN,
        summary="设置群头衔",
        params=(
            P("group_id", "id", required=True),
            P("user_id", "id", required=True),
            P("special_title", "string", default=""),
        ),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_anonymous",
        category=CAT_GROUP_ADMIN,
        summary="开启或关闭匿名聊天",
        params=(P("group_id", "id", required=True), P("enable", "bool", default=True)),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_anonymous_ban",
        category=CAT_GROUP_ADMIN,
        summary="禁言匿名成员",
        params=(P("group_id", "id", required=True), P("flag", "string", default="")),
        implemented=False,
        wip=NOT_QQ,
    ),
    # ---------------------------------------------------------------- 请求
    Action(
        name="set_friend_add_request",
        category=CAT_REQUEST,
        summary="处理好友添加请求",
        params=(
            P("flag", "string", required=True),
            P("approve", "bool", default=True),
            P("remark", "string", default=""),
        ),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="set_group_add_request",
        category=CAT_REQUEST,
        summary="处理入群请求或邀请",
        params=(
            P("flag", "string", required=True),
            P("sub_type", "string", default="add"),
            P("approve", "bool", default=True),
            P("reason", "string", default=""),
        ),
        implemented=False,
        wip=NOT_QQ,
    ),
    # ---------------------------------------------------------------- 扩展
    Action(
        name="send_poke",
        category=CAT_EXTEND,
        summary="拍一拍",
        params=(
            P("user_id", "id", required=True),
            P("group_id", "id", default=0, desc="0 表示私聊窗口"),
        ),
        implemented=False,
        wip="拍一拍要双击成员头像，UIA 无法稳定定位头像；见 docs/needs-user.md",
    ),
    Action(
        name="friend_poke",
        category=CAT_EXTEND,
        summary="拍一拍好友（私聊形态）",
        params=(P("user_id", "id", required=True),),
        implemented=False,
        wip="拍一拍要双击成员头像，UIA 无法稳定定位头像；见 docs/needs-user.md",
    ),
    Action(
        name="set_essence_msg",
        category=CAT_EXTEND,
        summary="设为群精华消息",
        params=(P("message_id", "int", required=True),),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="delete_essence_msg",
        category=CAT_EXTEND,
        summary="移除群精华消息",
        params=(P("message_id", "int", required=True),),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="get_essence_msg_list",
        category=CAT_EXTEND,
        summary="获取群精华消息列表",
        params=(P("group_id", "id", required=True),),
        read_only=True,
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="get_recent_contact",
        category=CAT_EXTEND,
        summary="获取最近会话列表",
        params=(P("count", "int", default=10),),
        read_only=True,
        implemented=False,
        wip=NOT_YET,
    ),
    Action(
        name="set_online_status",
        category=CAT_EXTEND,
        summary="设置在线状态",
        params=(P("status", "int", default=11), P("ext_status", "int", default=0)),
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="get_online_clients",
        category=CAT_EXTEND,
        summary="获取当前在线终端",
        read_only=True,
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="ocr_image",
        category=CAT_EXTEND,
        summary="图片文字识别",
        params=(P("image", "string", required=True),),
        read_only=True,
        implemented=False,
        wip=NOT_QQ,
    ),
    Action(
        name="translate_en2zh",
        category=CAT_EXTEND,
        summary="英译中",
        params=(
            P("words", "list", required=True),
            P("message_id", "int", default=0),
        ),
        read_only=True,
        implemented=False,
        wip=NOT_QQ,
    ),
)


def _build_index() -> dict[str, Action]:
    """建立「名字 → 动作」的索引，重名在导入期就报错。"""
    index: dict[str, Action] = {}
    for action in ACTIONS:
        for key in (action.name,):
            existing = index.get(key)
            if existing is not None:
                raise ValueError(
                    f"动作名冲突: {key!r} 同时属于 {existing.name!r} 与 {action.name!r}"
                )
            index[key] = action
    return index


_INDEX = _build_index()


def lookup(name: str) -> Action | None:
    """按名字或别名查动作定义，未登记返回 None。"""
    if not isinstance(name, str):
        return None
    return _INDEX.get(name.strip())


_TYPE_CHECKS = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    # user_id / group_id：既能填 OneBot 整数 ID，也能填微信那边的原始标识。
    # 只查「是不是整数或非空字符串」，ID 认不认得出来留给派发层（那里才知道上下文）。
    "id": lambda v: (
        (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, str) and bool(v.strip()))
    ),
    "string": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    # 消息既可以是消息段数组，也可以是 CQ 码字符串（auto_escape 为真时按纯文本）
    "message": lambda v: isinstance(v, (str, list)),
}


def _coerce(kind: str, value):
    """把客户端的等价写法收敛成本类型（只收敛、不报错）。

    客户端的字段类型并不总严格：AstrBot 的引用解析会把 ``message_id`` 先按字符串
    试一遍（``{"message_id": "402"}``，见它的 ``quoted_message/onebot_client.py``），
    纯数字字符串没有歧义——收下来比让客户端白试三轮更省事。
    """
    if kind == "int" and isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return int(text)
    return value


def validate_params(action: Action | str, params: dict | None) -> list[str]:
    """校验参数，返回错误描述列表（空列表表示通过）。

    只查声明的参数：未声明的额外参数一律放行，避免客户端的多余字段把请求打回。
    纯数字字符串按整数收下，并写回同一份 ``params``——派发层拿到的是收敛后的值。
    """
    spec = lookup(action) if isinstance(action, str) else action
    if spec is None:
        return ["未知动作"]
    values = params if isinstance(params, dict) else {}
    errors: list[str] = []
    for p in spec.params:
        if p.name not in values or values[p.name] is None:
            if p.required:
                errors.append(f"缺少参数 {p.name}（{p.type}）")
            continue
        values[p.name] = _coerce(p.type, values[p.name])
        check = _TYPE_CHECKS[p.type]
        if not check(values[p.name]):
            errors.append(
                f"参数 {p.name} 应为 {TYPE_LABELS.get(p.type, p.type)}，"
                f"收到 {type(values[p.name]).__name__}"
            )
    return errors


def catalog_dict() -> dict:
    """整份目录的 JSON 形状，供面板、SDK 与 MCP 生成清单。"""
    return {
        "categories": list(CATEGORIES),
        "actions": [action.to_dict() for action in ACTIONS],
    }
