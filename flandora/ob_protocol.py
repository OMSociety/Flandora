"""
OneBot v11 协议处理模块（对接 OneBot v11 客户端）。

- render_message_event() — 把领域消息渲染成 OneBot 消息事件 JSON
- to_segment_dict() / to_cq() — 单个消息段与整条消息的 CQ 码渲染
- push_event() — 广播事件给所有已连接的 OneBot v11 客户端
- _handle_ob_api() — 处理客户端发来的 API 请求（send_msg、get_login_info 等）
"""

import asyncio
import base64
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from . import (
    __version__,
    action_catalog,
    config,
    ob_ids,
    ob_segments,
    ob_store,
    send_plan,
)
from .domain import send as domain_send
from .domain.message import (
    Emoji,
    Face,
    File,
    Image,
    Mention,
    Message,
    Reply,
    Segment,
    Text,
    Video,
    Voice,
)
from .domain.send import MentionTarget, SendStatus
from .ob_network import broadcast as push_event_broadcast
from .runtime import RUNTIME, events

log = logging.getLogger("flandora")


def _failure(message: str, retcode: int, echo=None) -> dict:
    """构造失败响应。

    失败必须对客户端可见：status=failed、非零 retcode、一句人话原因。
    回 ``{"status": "ok"}`` 搪塞会让客户端把没发生的事记进上下文。
    """
    resp = {"status": "failed", "retcode": retcode, "data": {}, "message": message}
    if echo not in (None, ""):
        resp["echo"] = echo
    return resp


def _resolve_self_name() -> str:
    """机器人显示名：优先本地通讯录里自己那行的微信昵称，读不到退回 wechat-bot。

    识别成功就记进 ``ob_ids.SELF``（账号切换时昵称缓存已作废，会按新账号重识别）；
    失败不缓存，下次调用再试。昵称的读库与缓存由 bridge 实例统一负责，
    与 @ 识别、防自回环用的是同一个来源。
    """
    cached = ob_ids.SELF.nickname()
    if cached:
        return cached
    bridge = RUNTIME.bridge
    if bridge is None:
        return "wechat-bot"
    try:
        nick = bridge.self_nickname() or ""
    except Exception:  # noqa: BLE001
        nick = ""
    if nick:
        ob_ids.SELF.set_nickname(nick)
        return nick
    return "wechat-bot"


def _record_out(contact, is_group, text):
    """面板监控：记录发出的消息（事件总线在核心侧 ``runtime.events``）。"""
    events.record_outbound(contact, "group" if is_group else "private", text)


def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# QQ 表情 id → 微信自带表情文字代码（常见款；微信端发送代码文本即渲染为小黄豆）
QQ_FACE_TO_WECHAT = {
    0: "[惊讶]",
    1: "[撇嘴]",
    2: "[色]",
    3: "[发呆]",
    4: "[得意]",
    5: "[流泪]",
    6: "[害羞]",
    7: "[闭嘴]",
    8: "[睡]",
    9: "[大哭]",
    10: "[尴尬]",
    11: "[发怒]",
    12: "[调皮]",
    13: "[呲牙]",
    14: "[微笑]",
    15: "[难过]",
    18: "[抓狂]",
    19: "[吐]",
    20: "[偷笑]",
    22: "[白眼]",
    23: "[傲慢]",
    26: "[汗]",
}


def push_event(event: dict) -> int:
    """广播 OneBot 事件给所有已连接的客户端。返回成功推送数。

    推送前先落一份存档：客户端可能在很久之后拿 message_id 回来取原文，
    而微信本地库与内存都留不住它。存档失败只记日志，不阻断推送——
    送达优先级高于归档。
    """
    try:
        ob_store.message_store().save_event(event)
    except Exception as e:
        log.error(f"[OB11] 推送事件未能存档（message_id={event.get('message_id')}）：{e}")
    return push_event_broadcast(event)


async def _handle_ob_api(data: dict):
    """处理客户端发来的 API 请求。

    返回响应 dict（由服务端 handler 负责发送）；返回 None 表示无需响应。
    """
    action = data.get("action", "")
    params = data.get("params", {}) or {}
    echo = data.get("echo", "")
    log.info(f"[OB11] API: {action} echo={echo}")

    # 动作面统一入口：目录里没有的动作、登记了但没接入的、参数不合契约的，
    # 都在这里被拦下并如实回失败，后面的 if/elif 只处理真正能执行的动作。
    spec = action_catalog.lookup(action)
    if spec is None:
        log.warning(f"[OB11] 未知动作: {action!r}")
        return _failure(f"不支持的 action: {action}", action_catalog.RETCODE_UNSUPPORTED, echo)
    if not spec.implemented:
        log.info(f"[OB11] 动作未实现: {action}（{spec.wip}）")
        return _failure(
            f"{spec.name} 暂不可用：{spec.wip}", action_catalog.RETCODE_NOT_IMPLEMENTED, echo
        )
    param_errors = action_catalog.validate_params(spec, params)
    if param_errors:
        log.warning(f"[OB11] 参数不合契约 {action}: {param_errors}")
        return _failure("；".join(param_errors), action_catalog.RETCODE_BAD_PARAM, echo)
    action = spec.name

    resp_data = {"status": "ok", "retcode": 0, "data": {}}

    if action in ("send_msg", "send_private_msg", "send_group_msg"):
        # message_type 可以不传：按 OneBot v11 的约定，从动作名或 *_id 参数推出来。
        # 传了不认识的类型、或与动作名矛盾的值，一律如实报错——发错地方比报错糟得多。
        msg_type = str(params.get("message_type") or "").strip().lower()
        if msg_type and msg_type not in ("private", "group"):
            return _failure(
                f"message_type 只能是 private 或 group，收到 {params.get('message_type')!r}",
                action_catalog.RETCODE_BAD_PARAM,
                echo,
            )
        if action == "send_group_msg" and msg_type == "private":
            return _failure(
                "send_group_msg 与 message_type='private' 矛盾",
                action_catalog.RETCODE_BAD_PARAM,
                echo,
            )
        if action == "send_private_msg" and msg_type == "group":
            return _failure(
                "send_private_msg 与 message_type='group' 矛盾",
                action_catalog.RETCODE_BAD_PARAM,
                echo,
            )
        if action == "send_group_msg":
            is_group = True
        elif action == "send_private_msg":
            is_group = False
        else:
            is_group = (
                msg_type == "group" if msg_type else params.get("group_id") not in (None, "", 0)
            )
        target_key = "group_id" if is_group else "user_id"
        target = params.get(target_key, 0)
        message = params.get("message", [])
        try:
            message = ob_segments.normalize_message(
                message, auto_escape=bool(params.get("auto_escape", False))
            )
        except ob_segments.SegmentError as e:
            return _failure(str(e), action_catalog.RETCODE_BAD_PARAM, echo)
        if not message:
            return _failure(
                "message 为空，没有可发送的内容", action_catalog.RETCODE_BAD_PARAM, echo
            )
        if target in (None, "", 0):
            return _failure(f"缺少 {target_key}", action_catalog.RETCODE_BAD_PARAM, echo)
        # 目标既可以是整数 ID，也可以是微信标识（wxid / xxx@chatroom）。整数认不出来
        # 就是没分配过的 ID，如实报错：拿这个数字去微信里搜只会搜到别人。
        contact_handle = ob_ids.resolve_handle(target)
        if contact_handle is None:
            return _failure(
                f"未知的 {target_key}：{target!r}（填 Flandora 分配过的整数 ID，"
                "或直接填 wxid / xxx@chatroom）",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        known_id = ob_ids.find_contact_id(contact_handle)
        contact = ob_ids.lookup_name(known_id) if known_id is not None else ""
        # 群聊：微信搜索框搜不到 @chatroom ID，必须有群名。
        # 名字来源按「本次运行收到的群消息 → 本地库群名册」取，配置里的映射只作覆盖。
        if is_group:
            if not contact:
                contact = await asyncio.to_thread(_group_name, contact_handle)
            mapped_name = config.GROUP_NAME_MAP.get(contact_handle) or config.GROUP_NAME_MAP.get(
                str(target)
            )
            if mapped_name:
                contact = mapped_name
                log.info(f"[OB11] 群名映射: {contact_handle} -> {mapped_name}")
            if not contact:
                return _failure(
                    f"无法定位群 {contact_handle}：本地库里没有这个群，也没有收到过它的消息。"
                    "让该群发一条消息即可自动学会群名，或在 config.json 的 group_name_map 中手动指定。",
                    action_catalog.RETCODE_BAD_PARAM,
                    echo,
                )
        else:
            # 私聊：先用学过的名字；没有就问本地库要备注名（把 wxid 直接丢进搜索框，
            # 对「文件传输助手」这类系统会话搜不出来），最后才退回按 wxid 搜索。
            if not contact:
                contact = await asyncio.to_thread(_contact_name, contact_handle) or contact_handle

        # 客户端段 → 领域段：base64 内联数据与客户端附件在这一步落成本地文件，
        # 解不出来的段如实记账，之后再规划动作。
        prep = await asyncio.to_thread(_prepare_message, message)
        actions = send_plan.plan_message(prep.segments)
        failures: list[str] = list(prep.failures)
        skipped: list[str] = []
        for seg in prep.segments:
            if isinstance(seg, Reply):
                # 引用回复段：微信 UIA 无法直接构造引用，跳过（其余段正常发送）
                log.info("[OB11] 跳过 reply 段")
                skipped.append("引用回复段")

        def _resolve(targets: tuple[MentionTarget, ...]) -> tuple[list[str], str]:
            return _resolve_mentions(targets, is_group, contact_handle)

        try:
            outcomes = await asyncio.to_thread(
                domain_send.execute, actions, RUNTIME.sender, _resolve, contact
            )
        finally:
            # 本次解出来的临时文件是我们写的，发完就删；调用方给的文件不动。
            for temp_path in prep.temp_files:
                try:
                    os.unlink(temp_path)
                except OSError as exc:
                    # 临时文件占了临时目录，删不掉不影响本次发送结果，留痕即可。
                    log.debug(f"[OB11] 临时文件未能删除 {temp_path}: {exc}")
                    continue
                # 文件段为了保住卡片上的名字会单独建一个临时目录，顺手收掉
                parent = os.path.dirname(temp_path)
                if os.path.basename(parent).startswith("bridge_file_"):
                    try:
                        os.rmdir(parent)
                    except OSError:
                        pass

        sent_count = 0
        for outcome in outcomes:
            if outcome.status is SendStatus.SENT:
                sent_count += 1
                log.info(f"[OB11] {outcome.label}已发送至 {contact}")
                _record_out(contact, is_group, outcome.text)
            elif outcome.status is SendStatus.FAILED:
                reason = _sender_error()
                failures.append(f"{outcome.label}发送失败" + (f"（{reason}）" if reason else ""))
                log.error(f"[OB11] {outcome.label}发送失败（消息未真正发出）: {contact}")
            else:
                failures.append(outcome.text)
                log.warning(f"[OB11] 段未发送：{outcome.text}")

        # message_id 必须是真实发出去的号：客户端拿它做回复引用与撤回，
        # 顺手给的假号会让「撤回自己刚发的消息」永远找不到目标。
        if sent_count:
            resp_data["data"] = {"message_id": ob_ids.allocate_message_id(contact_handle)}

        # 一段都没发出去时必须回报失败，否则调用方会以为消息已送达并把它记进上下文。
        if not sent_count:
            if failures:
                resp_data["status"] = "failed"
                resp_data["retcode"] = action_catalog.RETCODE_INTERNAL
                resp_data["message"] = "消息未发送：" + "；".join(failures)
                log.error(f"[OB11] 消息未发出 [{contact}]：{failures}")
            elif skipped:
                resp_data["status"] = "failed"
                resp_data["retcode"] = action_catalog.RETCODE_NOT_IMPLEMENTED
                resp_data["message"] = (
                    "消息未发送：只有" + "、".join(skipped) + "，微信不支持单独发送这种段"
                )
                log.warning(f"[OB11] 消息只有被跳过的段 [{contact}]：{skipped}")
            else:
                resp_data["status"] = "failed"
                resp_data["retcode"] = action_catalog.RETCODE_BAD_PARAM
                resp_data["message"] = "消息未发送：message 里没有可发送的内容"
                log.warning(f"[OB11] message 里没有可发送的内容 [{contact}]")
        elif failures or skipped:
            resp_data["data"]["failed_segments"] = len(failures) + len(skipped)
            resp_data["data"]["failed_details"] = failures + skipped
            log.warning(
                f"[OB11] 部分消息段未发出 [{contact}]：失败 {len(failures)} 段、跳过 {len(skipped)} 段"
            )

    elif action == "get_login_info":
        resp_data["data"] = {
            "user_id": ob_ids.SELF.user_id(),
            "nickname": _resolve_self_name(),
        }

    elif action == "get_version_info":
        resp_data["data"] = {
            "app_name": "flandora",
            "app_version": __version__,
            "protocol_version": "v11",
        }

    elif action == "get_status":
        flags = RUNTIME.online_flags()
        resp_data["data"] = {
            "online": flags["online"],
            "good": flags["good"],
            "app_name": "flandora",
            "app_version": __version__,
            "protocol_version": "v11",
            "receiver_ready": RUNTIME.store is not None,
            "sender_ready": RUNTIME.sender is not None,
        }

    elif action == "can_send_image":
        resp_data["data"] = {"yes": True}

    elif action == "can_send_record":
        # 微信 PC 客户端只能按住麦克风录音，外部无法提交语音消息；
        # record 段目前以 mp3 文件形式投递，因此这里如实回 false。
        resp_data["data"] = {"yes": False}

    elif action == "get_contact_id":
        handle = str(params.get("handle", "")).strip()
        if not handle:
            return _failure("handle 不能为空", action_catalog.RETCODE_BAD_PARAM, echo)
        resp_data["data"] = {"handle": handle, "id": ob_ids.contact_id(handle)}

    elif action == "get_contact_handle":
        oid = params.get("id")
        handle = ob_ids.resolve_handle(oid)
        if handle is None:
            return _failure(
                f"未知的 id：{oid!r}（这个 ID 不是 Flandora 分配过的联系人）",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        resp_data["data"] = {"id": ob_ids.contact_id(handle), "handle": handle}

    elif action == "get_contact_ids":
        mapping = ob_ids.contact_ids().items()
        resp_data["data"] = {
            "count": len(mapping),
            "items": [
                {"handle": handle, "id": oid}
                for handle, oid in sorted(mapping.items(), key=lambda kv: kv[1])
            ],
        }

    elif action == "get_group_info":
        gid = params.get("group_id")
        handle = ob_ids.resolve_handle(gid)
        if not handle:
            return _failure(
                f"未知的 group_id：{gid}（这个 ID 不是 Flandora 分配过的群）",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        groups = await asyncio.to_thread(_groups_roster)
        found = next((g for g in groups if g["username"] == handle), None)
        if found is None:
            return _failure(
                f"本地库里没有群 {handle} 的资料（可能还没同步到本机）",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        resp_data["data"] = _group_object(found)

    elif action == "get_group_list":
        rows = await asyncio.to_thread(_groups_roster)
        resp_data["data"] = [_group_object(g) for g in rows]

    elif action == "get_friend_list":
        rows = await asyncio.to_thread(_contacts_roster)
        resp_data["data"] = [
            {
                "user_id": ob_ids.contact_id(c["username"]),
                "nickname": c["nick_name"] or c["username"],
                "remark": c["remark"],
            }
            for c in rows
        ]

    elif action in ("get_stranger_info", "get_friend_info"):
        uid = params.get("user_id")
        handle = ob_ids.resolve_handle(uid)
        if not handle:
            return _failure(
                f"未知的 user_id：{uid}（这个 ID 不是 Flandora 分配过的联系人）",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        info = await asyncio.to_thread(_contact_info, handle)
        if info is None:
            return _failure(
                f"本地库里没有 {handle} 的资料（可能还没同步到本机）",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        resp_data["data"] = _user_object(info)

    elif action in ("get_group_member_list", "get_group_member_info"):
        gid = params.get("group_id")
        ghandle = ob_ids.resolve_handle(gid)
        if not ghandle:
            return _failure(
                f"未知的 group_id：{gid}（这个 ID 不是 Flandora 分配过的群）",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        members = await asyncio.to_thread(_members_roster, ghandle)
        if not members:
            return _failure(
                f"本地库里没有群 {ghandle} 的成员名单（可能还没同步到本机）",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        if action == "get_group_member_list":
            resp_data["data"] = [_member_object(ob_ids.contact_id(ghandle), m) for m in members]
        else:
            uid = params.get("user_id")
            handle = ob_ids.resolve_handle(uid)
            found = next((m for m in members if handle and m["username"] == handle), None)
            if found is None:
                return _failure(
                    f"{handle or uid} 不在群 {ghandle} 里",
                    action_catalog.RETCODE_NOT_FOUND,
                    echo,
                )
            resp_data["data"] = _member_object(ob_ids.contact_id(ghandle), found)

    elif action == "get_msg":
        mid = params.get("message_id", 0)
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            return _failure(
                f"message_id 必须是整数：{mid!r}", action_catalog.RETCODE_BAD_PARAM, echo
            )
        event = await asyncio.to_thread(ob_store.message_store().get, mid)
        if event is None:
            return _failure(
                f"没有 message_id={mid} 的消息：存档里只有 Flandora 推送过的事件",
                action_catalog.RETCODE_NOT_FOUND,
                echo,
            )
        resp_data["data"] = event

    elif action in ("get_group_msg_history", "get_friend_msg_history"):
        # 从微信本地库拉取聊天历史（插件可主动读取上下文）
        is_g = action == "get_group_msg_history"
        key = "group_id" if is_g else "user_id"
        target = params.get(key)
        handle = ob_ids.resolve_handle(target)
        if not handle:
            return _failure(
                f"未知的 {key}：{target}（这个 ID 不是 Flandora 分配过的会话）",
                action_catalog.RETCODE_BAD_PARAM,
                echo,
            )
        try:
            count = int(params.get("count", 20) or 20)
        except (TypeError, ValueError):
            count = 20
        msgs = await asyncio.to_thread(_fetch_history_ob, handle, is_g, count)
        resp_data["data"] = {"messages": msgs}

    else:
        # 走到这里说明动作目录登记为「已实现」，派发链却没有对应分支——
        # 是接线缺口，必须报错；回 ok + 空数据会让客户端以为调用成功了。
        log.error(f"[OB11] 动作 {action} 已登记为已实现，但派发层没有处理分支")
        return _failure(
            f"动作 {action} 尚未接线（实现缺口）", action_catalog.RETCODE_INTERNAL, echo
        )

    if echo:
        resp_data["echo"] = echo
    return resp_data


# 超过这个大小就走 file:// 引用：几 MB 的 base64 会把一条事件撑到没有客户端愿意解析
MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024


def _image_data(image_path: str) -> dict | None:
    """把本地图片文件编成 OneBot image 段的 data。

    小图内联成 base64://，客户端不必再读盘；大图退回 file:// 引用——几 MB 的
    base64 会把一条事件撑到没有客户端愿意解析。文件读不出来时返回 None。
    """
    try:
        if not image_path or not os.path.isfile(image_path):
            return None
        size = os.path.getsize(image_path)
        if size <= 0:
            return None
        if size <= MAX_INLINE_IMAGE_BYTES:
            with open(image_path, "rb") as fh:
                encoded = base64.b64encode(fh.read()).decode("ascii")
            return {"file": f"base64://{encoded}"}
        # 过大时改用本地文件引用，文件名取原名，便于客户端缓存与显示
        return {"file": Path(image_path).as_uri()}
    except OSError as exc:
        log.warning(f"读取图片失败，已跳过图片段: {exc}")
        return None


def to_segment_dict(segment: Segment) -> dict | None:
    """领域段 → OneBot v11 消息段。

    图片文件读不出来时返回 None：不造一个空的 image 段，否则客户端会把它当
    坏图去重试，而这里的事实是「这张图没解出来」。
    """
    if isinstance(segment, Text):
        return {"type": "text", "data": {"text": segment.text}}
    if isinstance(segment, Mention):
        if segment.user_id is not None:
            return {"type": "at", "data": {"qq": segment.user_id}}
        return {"type": "at", "data": {"qq": "", "name": segment.name}}
    if isinstance(segment, Reply):
        return {"type": "reply", "data": {"id": segment.message_id}}
    if isinstance(segment, (Image, Emoji)):
        # 表情包本体就是一张图，客户端按 image 段渲染
        data = _image_data(segment.path)
        return None if data is None else {"type": "image", "data": data}
    if isinstance(segment, Face):
        return {"type": "face", "data": {"id": segment.face_id}}
    if isinstance(segment, Voice):
        return {"type": "record", "data": {"file": segment.path}}
    if isinstance(segment, Video):
        return {"type": "video", "data": {"file": segment.path}}
    return {"type": "file", "data": {"file": segment.path, "name": segment.name}}


def to_cq(segments) -> str:
    """领域段 → CQ 码字符串。"""
    return ob_segments.segments_to_cq(
        [data for data in (to_segment_dict(seg) for seg in segments) if data]
    )


def render_message_event(message: Message, message_format: str) -> dict:
    """把一条领域消息渲染成 OneBot v11 消息事件。

    ``message_format`` 是 ``config.OB_MESSAGE_FORMAT``：``array`` 时 ``message``
    是消息段数组，否则是 CQ 码字符串；``raw_message`` 始终是 CQ 码——客户端
    按字符串处理时两边都能用。
    """
    rendered = [data for data in (to_segment_dict(seg) for seg in message.segments) if data]
    raw_cq = ob_segments.segments_to_cq(rendered)
    user_id = ob_ids.contact_id(message.sender.handle or message.chat.handle)
    event = {
        "time": int(message.timestamp or time.time()),
        "self_id": message.self_id or ob_ids.SELF.user_id(),
        "post_type": "message",
        "message_id": message.message_id,
    }
    event["message_type"] = "group" if message.chat.is_group else "private"
    if message.chat.is_group:
        group_id = ob_ids.contact_id(message.chat.handle)
        event["group_id"] = group_id
        # 微信那边的原始群标识：客户端要按标识存取（例如写进自己的白名单）时不必再查一次
        event["group_wxid"] = message.chat.handle
    event["user_id"] = user_id
    event["message"] = rendered if message_format == "array" else raw_cq
    event["raw_message"] = raw_cq
    event["sender"] = {
        "user_id": user_id,
        "nickname": message.sender.nickname or str(user_id),
        "wxid": message.sender.handle or "",
    }
    if message.chat.is_group:
        event["group_name"] = message.chat.name or str(event["group_id"])
        # 是否真实 @ 了机器人：由客户端决定要不要响应，桥接只如实标记
        event["is_mentioned"] = bool(message.mentioned)
    return event


# ============ 客户端消息段准备 ============


@dataclass
class _OutboundPrep:
    """一条客户端消息的准备结果。

    ``segments`` 交给规划层；``failures`` 是转换阶段就注定发不出去的段；
    ``temp_files`` 是本次解出来的临时文件，执行完必须删掉。
    """

    segments: list[Segment] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    temp_files: list[str] = field(default_factory=list)


def _media_file(file_val: str, label: str, prep: _OutboundPrep) -> str | None:
    """把图片/表情的 file 字段落成本地文件路径。

    接受 ``base64://`` 内联数据与客户端附件目录里的文件名。解不出来时返回 None
    并**记进 ``prep.failures``**：这一段没有可发的图，但客户端要能看见原因——只发
    图片的消息因此如实回报失败，混发时也计进 ``failed_segments``。
    """
    if not file_val:
        prep.failures.append(f"{label}段缺少 file 字段")
        return None
    if file_val.startswith("base64://"):
        try:
            path = _decode_base64_image(file_val[9:])
        except Exception as e:  # noqa: BLE001
            prep.failures.append(f"{label}的 base64 数据不合法")
            log.warning(f"[OB11] base64 {label}解码失败: {e}")
            return None
        if not path:
            prep.failures.append(f"{label}解码失败")
            return None
        prep.temp_files.append(path)
        log.info(f"[OB11] {label}已解码: {os.path.basename(path)}")
        return path
    if not config.CLIENT_ATTACHMENTS:
        prep.failures.append(f"{label}文件未找到：{file_val}（client_attachments 未配置）")
        log.warning(f"[OB11] {label}文件未找到（未配置 client_attachments）: {file_val}")
        return None
    # 只按文件名找：`..\..\Windows\...` 这类带路径分隔符的值不许穿出附件目录，
    # 与文件段（`_file_path`）的收窄口径一致。子目录靠下面第二个候选目录覆盖。
    name = os.path.basename(file_val.replace("\\", "/"))
    if not name:
        prep.failures.append(f"{label}文件未找到：{file_val}")
        return None
    for candidate in (
        os.path.join(config.CLIENT_ATTACHMENTS, name),
        os.path.join(config.CLIENT_ATTACHMENTS, "wechat_images", name),
    ):
        if os.path.exists(candidate):
            return candidate
    prep.failures.append(f"{label}文件未找到：{file_val}")
    log.warning(f"[OB11] {label}文件未找到: {file_val}")
    return None


def _local_path(raw: str) -> str:
    """把客户端给的文件位置还原成本机路径（``file://`` URI 会被百分号编码）。"""
    if not raw.startswith("file://"):
        return raw
    path = unquote(raw[7:])
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]  # file:///D:/x 的路径部分是 /D:/x，去掉前导斜杠
    return path


def _file_path(data: dict, prep: _OutboundPrep) -> str | None:
    """文件段（``file``）落成本地路径。

    客户端给的形式不止一种：``base64://`` 内联、``file:///D:/…`` URI（AstrBot 的
    ``File`` 组件就走这种）、本机路径，以及客户端附件目录里的文件名。``http(s)``
    URL 不下载——那是桥接替客户端访问网络，超出这条通道的职责，如实记成失败段。

    文件卡片上的名字跟着实际文件走：内联数据用 ``name`` 命名临时文件，本机路径
    用文件自己的名字。
    """
    raw = str(data.get("file") or "")
    name = str(data.get("name") or "")
    if not raw:
        prep.failures.append("文件段缺少 file 字段")
        return None
    if raw.startswith("base64://"):
        try:
            path = _decode_base64_file(raw[9:], name)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[OB11] base64 文件解码失败: {e}")
            prep.failures.append("文件 base64 解码失败")
            return None
        prep.temp_files.append(path)
        log.info(f"[OB11] 文件已解码: {os.path.basename(path)}")
        return path
    if raw.startswith(("http://", "https://")):
        log.warning(f"[OB11] 不下载文件段给的 URL: {raw}")
        prep.failures.append("文件段不支持 http(s) URL：把文件放本机，用路径或 base64:// 传")
        return None
    path = _local_path(raw)
    if os.path.isfile(path):
        return path
    if config.CLIENT_ATTACHMENTS:
        candidate = os.path.join(config.CLIENT_ATTACHMENTS, os.path.basename(path))
        if os.path.isfile(candidate):
            return candidate
    log.warning(f"[OB11] 文件不存在: {raw}")
    prep.failures.append(f"文件不存在：{raw}")
    return None


def _record_file(data: dict, prep: _OutboundPrep) -> str | None:
    """语音段（``record``）落成本地音频文件。

    微信 PC 端没有「提交语音消息」的接口（语音必须按住麦克风录制），所以 bridge
    把 record 段以文件形式发到聊天窗口（音频文件卡片，双击可播放）。接受
    ``base64://`` 内联数据与已落盘的本地路径两种写法；缺文件与解码失败都要记账，
    客户端否则会以为语音发出去了。
    """
    file_val = str(data.get("file") or data.get("data") or "")
    if not file_val:
        prep.failures.append("语音缺少 file")
        log.warning(f"[OB11] 跳过 record 段：无 file 字段: {data}")
        return None
    if file_val.startswith("base64://"):
        try:
            audio = base64.b64decode(file_val[9:])
        except Exception as e:  # noqa: BLE001
            prep.failures.append(f"语音 base64 解码失败：{e}")
            log.warning(f"[OB11] record base64 解码失败: {e}")
            return None
        with tempfile.NamedTemporaryFile(
            prefix="bridge_record_", suffix=".mp3", delete=False
        ) as tmp:
            tmp.write(audio)
            path = tmp.name
        prep.temp_files.append(path)
        log.info(f"[OB11] record 已解码: {os.path.basename(path)} ({len(audio)} bytes)")
        return path
    if os.path.isfile(file_val):
        return file_val
    prep.failures.append("语音文件未找到")
    log.warning(f"[OB11] record 文件未找到: {file_val}")
    return None


def _video_file(file_val: str, prep: _OutboundPrep) -> str | None:
    """视频段落成本地视频文件。

    微信 PC 端没有「提交视频消息」的入口，只能当文件发；文件一定能到对方手里，
    呈现形态（文件卡片还是视频气泡）由微信按扩展名决定。缺文件与解码失败都要
    记账：客户端否则会以为视频发出去了。
    """
    if not file_val:
        prep.failures.append("视频缺少 file")
        return None
    if file_val.startswith("base64://"):
        try:
            raw = base64.b64decode(file_val[9:])
        except Exception as e:  # noqa: BLE001
            prep.failures.append(f"视频 base64 解码失败：{e}")
            log.warning(f"[OB11] video base64 解码失败: {e}")
            return None
        with tempfile.NamedTemporaryFile(
            prefix="bridge_video_", suffix=".mp4", delete=False
        ) as tmp:
            tmp.write(raw)
            path = tmp.name
        prep.temp_files.append(path)
        log.info(f"[OB11] 视频已解码: {os.path.basename(path)} ({len(raw)} bytes)")
        return path
    if os.path.isfile(file_val):
        return file_val
    prep.failures.append("视频文件未找到")
    log.warning(f"[OB11] 视频文件未找到: {file_val}")
    return None


def _prepare_message(message: list) -> _OutboundPrep:
    """OneBot 消息段 → 领域段，并把需要落盘的媒体先写成本地文件。

    不认识的段类型绝不静默跳过：跳过会让「零段发送」看起来像成功，客户端随即
    把内容记进上下文。这里记成失败段，响应里如实回报。
    """
    prep = _OutboundPrep()
    for seg in message:
        seg_type = str(seg.get("type") or "")
        data = seg.get("data") or {}
        if seg_type == "text":
            prep.segments.append(Text(text=str(data.get("text") or "")))
        elif seg_type == "at":
            raw_qq = data.get("qq")
            if str(raw_qq) in ("all", "all_pending"):
                # v11 用 qq="all" 表示 @全体；微信没有这个入口，归一成显示名
                # 交给解析层如实拒绝
                prep.segments.append(Mention(user_id=None, name="全体成员"))
                continue
            qq = _safe_int(raw_qq)
            prep.segments.append(
                Mention(
                    user_id=qq if qq and qq > 0 else None,
                    name=str(data.get("name") or ""),
                )
            )
        elif seg_type == "reply":
            prep.segments.append(Reply(message_id=int(_safe_int(data.get("id")) or 0)))
        elif seg_type == "image":
            path = _media_file(str(data.get("file") or ""), "图片", prep)
            if path:
                prep.segments.append(Image(path=path))
        elif seg_type == "file":
            path = _file_path(data, prep)
            if path:
                prep.segments.append(File(path=path, name=str(data.get("name") or "")))
        elif seg_type == "face":
            file_val = str(data.get("file") or "")
            if file_val:
                path = _media_file(file_val, "表情", prep)
                if path:
                    prep.segments.append(Emoji(path=path))
                continue
            # 无文件：把 QQ 表情 id 换成微信自带表情代码（发这段文本即渲染成小黄豆）
            face_code = QQ_FACE_TO_WECHAT.get(_safe_int(data.get("id")))
            if face_code:
                prep.segments.append(Face(face_id=face_code))
            else:
                log.debug(f"[OB11] 跳过未知 face 段: {data}")
        elif seg_type == "record":
            path = _record_file(data, prep)
            if path:
                prep.segments.append(Voice(path=path))
        elif seg_type == "video":
            path = _video_file(str(data.get("file") or ""), prep)
            if path:
                prep.segments.append(Video(path=path))
        else:
            log.warning(f"[OB11] 不支持的消息段类型: {seg_type!r}")
            prep.failures.append(f"不支持的消息段 {seg_type or '（空类型）'}")
    return prep


def make_heartbeat_event(interval: int, *, online: bool = True, good: bool = True) -> dict:
    """OneBot v11 心跳元事件。

    框架靠它判断协议端还活着、账号还在线——连接不断和账号在线是两件事，
    没有心跳时框架只能把「掉登录」当成「网络抖动」。
    """
    return {
        "time": int(time.time()),
        "self_id": ob_ids.SELF.user_id(),
        "post_type": "meta_event",
        "meta_event_type": "heartbeat",
        "status": {"online": bool(online), "good": bool(good)},
        "interval": int(interval),
    }


def make_lifecycle_event(sub_type: str) -> dict:
    """OneBot v11 生命周期元事件（connect / enable / disable）。"""
    return {
        "time": int(time.time()),
        "self_id": ob_ids.SELF.user_id(),
        "post_type": "meta_event",
        "meta_event_type": "lifecycle",
        "sub_type": sub_type,
    }


def make_recall_event(
    *,
    group_id: int = 0,
    user_id: int = 0,
    operator_id: int = 0,
    message_id: int = 0,
    nickname: str = "",
) -> dict:
    """撤回通知。

    微信 PC 的撤回提示不带被撤回消息的服务端号，`message_id` 只能给 0；
    `message_id_known` 把这个事实明说出来，客户端才不会以为是自己查漏了。
    """
    event = {
        "time": int(time.time()),
        "self_id": ob_ids.SELF.user_id(),
        "post_type": "notice",
        "notice_type": "group_recall" if group_id else "friend_recall",
        "user_id": int(user_id),
        "operator_id": int(operator_id or user_id),
        "message_id": int(message_id),
        "message_id_known": bool(message_id),
        "nickname": nickname,
    }
    if group_id:
        event["group_id"] = int(group_id)
    return event


def make_poke_event(
    *, group_id: int = 0, user_id: int = 0, target_id: int = 0, suffix: str = ""
) -> dict:
    """拍一拍通知（notice/notify/poke）。

    `user_id` 是拍的人，`target_id` 是被拍的人，`suffix` 是对方设置的拍一拍文案。
    """
    event = {
        "time": int(time.time()),
        "self_id": ob_ids.SELF.user_id(),
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "poke",
        "user_id": int(user_id),
        "target_id": int(target_id),
        "suffix": suffix,
    }
    if group_id:
        event["group_id"] = int(group_id)
    return event


def _history_message_id(talker: str, local_id) -> int:
    """历史消息的 message_id：有本地行号就复用，没有就现发一个。"""
    try:
        lid = int(local_id or 0)
    except (TypeError, ValueError):
        lid = 0
    if lid > 0:
        return ob_ids.ensure_message_id(talker, lid)
    return ob_ids.allocate_message_id(talker)


def _fetch_history_ob(talker: str, is_group: bool, count: int) -> list:
    """从微信本地库取会话历史，转换为 OneBot v11 消息格式列表。

    ``talker`` 是微信会话原始标识（wxid 或 ``xxx@chatroom``）；返回的 user_id /
    group_id 与实时事件一样走 ID 层，因此客户端拿到的是同一套整数 ID。
    在线程池中执行（asyncio.to_thread）。失败返回空列表，不抛异常。
    """
    out = []
    if not talker:
        return out
    count = max(1, min(count, 100))

    store = RUNTIME.store
    if store is None:
        log.warning(f"[OB11] 收件源尚未就绪，历史拉取跳过 (talker={talker})")
        return out

    # 复用 bridge 实例的成员昵称缓存（没有则用 wxid 兜底）
    names: dict[str, str] = {}
    bridge = RUNTIME.bridge
    if bridge is not None:
        try:
            names = bridge.resolve_names(talker)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[OB11] 读取成员名册失败 (talker={talker}): {e}")
            names = {}

    group_id = ob_ids.contact_id(talker) if is_group else 0
    try:
        for m in store.recent_messages(talker, limit=count):
            text = (m.get("text") or "").strip()
            if not text:
                continue
            if m.get("outgoing"):
                uid = ob_ids.SELF.user_id()
                nick = _resolve_self_name()
            else:
                sender_wxid = m.get("sender") or ""
                uid = ob_ids.contact_id(sender_wxid) if sender_wxid else 0
                nick = names.get(sender_wxid) or sender_wxid or "未知"
            msg = {
                "time": m.get("create_time", 0) or 0,
                "message_type": "group" if is_group else "private",
                "message_id": _history_message_id(talker, m.get("local_id")),
                "user_id": uid,
                "sender": {"user_id": uid, "nickname": nick},
                "raw_message": text,
                "message": [{"type": "text", "data": {"text": text}}],
            }
            if is_group:
                msg["group_id"] = group_id
            out.append(msg)
        log.info(f"[OB11] 历史拉取 {talker}: {len(out)} 条")
    except Exception as e:  # noqa: BLE001
        log.warning(f"[OB11] 拉取历史失败 (talker={talker}): {e}")
    return out


def _sender_error() -> str:
    """发送器记下的最后一条失败原因（没有或取不到就返回空串）。

    原因留在发送器里（日志与面板都读它），消息响应把它带上，客户端才知道是
    「输入框有清不掉的残留」还是「发送通道没起来」，不必去翻运行日志。
    """
    getter = getattr(RUNTIME.sender, "last_error", None)
    if getter is None:
        return ""
    try:
        return str(getter() or "")
    except Exception as e:  # noqa: BLE001
        # 取原因本身不该把发送结果搞坏
        log.debug(f"[OB11] 读取发送失败原因失败: {e}")
        return ""


def _store():
    """取当前收件源的本地库只读视图；收件源未就绪时返回 None。"""
    return RUNTIME.store


def _contacts_roster() -> list:
    """在线程池中执行：通讯录里的好友（不含群、公众号、文件传输助手）。"""
    store = _store()
    if store is None:
        return []
    return store.contacts()


def _groups_roster() -> list:
    """在线程池中执行：通讯录里的群会话。"""
    store = _store()
    if store is None:
        return []
    return store.groups()


def _group_name(handle: str) -> str:
    """在线程池中执行：按群 wxid 取微信里显示的群名，找不到返回空串。"""
    for group in _groups_roster():
        if group.get("username") == handle:
            return str(group.get("name") or "")
    return ""


def _contact_name(handle: str) -> str:
    """在线程池中执行：按 wxid 取微信里显示的备注名/昵称，找不到返回空串。

    名字还没学过时不能拿 wxid 去搜索框碰运气：「文件传输助手」这类系统会话在
    微信搜索框里搜 ``filehelper`` 搜不出结果（实测），得先换成显示名。
    """
    store = _store()
    if store is None:
        return ""
    name = str(store.display_names().get(handle) or "")
    return "" if name == handle else name


def _members_roster(group_handle: str) -> list:
    """在线程池中执行：某个群的成员名单。"""
    store = _store()
    if store is None:
        return []
    return store.group_members(group_handle)


def _contact_info(handle: str) -> dict | None:
    """在线程池中执行：按 wxid 在通讯录里找一个人，找不到返回 None。

    ``contacts()`` 会把自己过滤掉（自己不是自己的好友），但客户端会按 ``self_id``
    查机器人自己的资料：群里 @ 机器人时它就要用这个昵称补 ``at`` 段，答不上来就
    把整段丢掉。所以这里对自己单独认一次。
    """
    for c in _contacts_roster():
        if c["username"] == handle:
            return c
    if handle and handle == ob_ids.SELF.wxid():
        return {"username": handle, "nick_name": _self_nickname(), "remark": ""}
    return None


def _self_nickname() -> str:
    """机器人自己的昵称：本地库里那一行优先，其次是登录信息里学到的。"""
    store = _store()
    nick = store.self_nickname() if store is not None else ""
    return nick or ob_ids.SELF.nickname()


def _group_object(g: dict) -> dict:
    """群资料 → OneBot v11 群对象。

    微信客户端只在本地库里存成员数，没有独立的「群容量」，因此
    ``max_member_count`` 与 ``member_count`` 同值，不编造上限。
    """
    gid = ob_ids.contact_id(g["username"])
    return {
        "group_id": gid,
        "group_name": g["name"],
        "member_count": int(g.get("member_count") or 0),
        "max_member_count": int(g.get("member_count") or 0),
    }


def _user_object(info: dict) -> dict:
    """联系人资料 → OneBot v11 好友/陌生人对象。

    本地库里没有性别与年龄，如实回 unknown / 0，不猜。
    """
    return {
        "user_id": ob_ids.contact_id(info["username"]),
        "nickname": info["nick_name"] or info["username"],
        "remark": info.get("remark") or "",
        "sex": "unknown",
        "age": 0,
    }


def _member_object(group_id: int, m: dict) -> dict:
    """群成员资料 → OneBot v11 群成员对象。

    ``card`` 是群昵称，微信 4.x 的 contact.db 里没有独立字段，留空；
    显示名已经把备注名优先合并进 ``nickname``。
    """
    return {
        "group_id": group_id,
        "user_id": ob_ids.contact_id(m["username"]),
        "nickname": m["nick_name"] or m["username"],
        "card": "",
        "sex": "unknown",
        "age": 0,
        "join_time": 0,
        "last_sent_time": 0,
        "level": "0",
        "role": m.get("role") or "member",
        "unfriendly": False,
        "title": "",
        "title_expire_time": 0,
        "card_changeable": False,
    }


def _resolve_mentions(
    targets: tuple[MentionTarget, ...], is_group: bool, group_handle: str
) -> tuple[list[str], str]:
    """把 @ 段的目标解析成群成员的显示名。

    返回 ``(names, problem)``：``problem`` 非空表示这一条 @ 不能发，原因直接
    回给客户端（未知 ID、@全体、名册不可用）；``names`` 是成员选择弹层里用来
    筛人的名字。解析必须落在真实名册上，否则会 @ 到错的人。
    """
    if not is_group:
        return [], "@ 只能用在群消息里"
    members = _members_roster(group_handle)
    if not members:
        return [], "拿不到群成员名册（微信库未就绪），无法 @ 成员"
    by_handle = {m["username"]: m for m in members}

    names: list[str] = []
    for target in targets or ():
        name = (target.name or "").strip()
        if name in ("全体成员", "all"):
            return [], "微信 PC 端没有 @ 全体成员的入口"
        member = None
        if target.qq:
            member = by_handle.get(ob_ids.handle_of(target.qq) or "")
            if member is None:
                return [], f"未知的 user_id：{target.qq}（不是这个群的成员）"
        elif name:
            for candidate in members:
                if name in (
                    candidate.get("name"),
                    candidate.get("remark"),
                    candidate.get("nick_name"),
                    candidate.get("username"),
                ):
                    member = candidate
                    break
            if member is None:
                return [], f"群成员里没有「{name}」"
        else:
            continue
        display = (
            member.get("name") or member.get("nick_name") or member.get("username") or ""
        ).strip()
        if not display:
            return [], "这个群成员没有可用的显示名"
        names.append(display)
    if not names:
        return [], "@ 段里没有可识别的成员"
    return names, ""


def _decode_base64_file(b64_data: str, name: str = "") -> str:
    """解码 base64 文件并落成临时文件，尽量保住客户端给的文件名。

    发送器投递的是附件卡片，卡片上的名字就是文件的名字，所以这里单独建一个临时
    目录放它（发送完由调用方删文件、收目录），而不是像图片那样按魔数改扩展名。
    """
    data = base64.b64decode(b64_data)
    safe = os.path.basename(name).strip() or "file.bin"
    directory = tempfile.mkdtemp(prefix="bridge_file_")
    path = os.path.join(directory, safe)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _decode_base64_image(b64_data: str) -> str | None:
    """在线程池中执行：解码 base64 图片并保存为临时文件（按内容识别格式）。"""
    import tempfile

    img_data = base64.b64decode(b64_data)
    # 按文件头识别图片格式，避免 gif 被存成 png
    suffix = ".png"
    if img_data[:6] in (b"\x47\x49\x46\x38\x37\x61", b"\x47\x49\x46\x38\x39\x61"):
        suffix = ".gif"
    elif img_data[:2] == b"\xff\xd8":
        suffix = ".jpg"
    elif img_data[:8] == b"\x89PNG\r\n\x1a\n":
        suffix = ".png"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(img_data)
    tmp.close()
    return tmp.name
