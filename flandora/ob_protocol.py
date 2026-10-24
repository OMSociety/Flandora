"""
OneBot v11 协议处理模块（对接 OneBot v11 客户端）。

- make_message_event() — 构造 OneBot 消息事件 JSON
- push_event() — 广播事件给所有已连接的 OneBot v11 客户端
- _handle_ob_api() — 处理客户端发来的 API 请求（send_msg、get_login_info 等）
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
import time

from . import (
    __version__,
    action_catalog,
    config,
    ob_ids,
    ob_segments,
    ob_store,
    send_plan,
    state,
)
from .ob_network import broadcast as push_event_broadcast

try:
    from .webui import monitor
except Exception:
    monitor = None

log = logging.getLogger("ob11-bridge")


def _failure(message: str, retcode: int, echo=None) -> dict:
    """构造失败响应。

    失败必须对客户端可见：status=failed、非零 retcode、一句人话原因。
    回 ``{"status": "ok"}`` 搪塞会让客户端把没发生的事记进上下文。
    """
    resp = {"status": "failed", "retcode": retcode, "data": {}, "message": message}
    if echo not in (None, ""):
        resp["echo"] = echo
    return resp


def _record_out(contact, is_group, text):
    """WebUI 监控：记录发出的消息。"""
    if monitor is not None:
        monitor.record_outbound(contact, "group" if is_group else "private", text)


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


async def _send_resp_to(ws, resp_data: dict):
    """向指定 WS 连接发送响应（服务端 loop 内直接 send，无跨线程）。"""
    try:
        await ws.send(json.dumps(resp_data, ensure_ascii=False))
        log.info(f"[OB11] 已回响应: {resp_data.get('echo', '')}")
    except Exception as e:
        log.warning(f"[OB11] 回响应失败: {e}")


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
        is_group = action == "send_group_msg" or params.get("message_type") == "group"
        target_id = params.get("group_id" if is_group else "user_id", 0)
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
        contact_handle = ob_ids.handle_of(target_id) or ""
        contact = state._ob_id_to_contact.get(target_id) or ""
        if not target_id:
            return _failure("缺少 user_id / group_id", action_catalog.RETCODE_BAD_PARAM, echo)
        # 群聊：微信搜索框搜不到 @chatroom ID，必须有群名
        if is_group:
            mapped_name = config.GROUP_NAME_MAP.get(contact_handle) or config.GROUP_NAME_MAP.get(
                str(target_id)
            )
            if mapped_name:
                contact = mapped_name
                log.info(f"[OB11] 群名映射: {target_id} -> {mapped_name}")
            if not contact:
                return _failure(
                    f"无法定位群 {target_id}：还没有群名映射。先让该群发一条消息（Flandora 会自动学习群名），"
                    "或在 config.json 的 group_name_map 中手动配置。",
                    action_catalog.RETCODE_BAD_PARAM,
                    echo,
                )
        else:
            # 私聊：缓存里的备注名优先，其次按 wxid 搜索（微信能搜到好友的 wxid）
            contact = contact or contact_handle or str(target_id)

        sent_count = 0
        failures: list[str] = []
        skipped: list[str] = []

        async def _send(kind: str, fn, *args) -> bool:
            """执行一次发送并计数。

            UIA 发送失败（微信未登录、主窗口不可用等）必须在此处如实记账，
            否则后面的日志与响应都会谎报成功。
            """
            nonlocal sent_count
            ok = bool(await asyncio.to_thread(fn, *args))
            if ok:
                sent_count += 1
                log.info(f"[OB11] {kind}已发送至 {contact}")
            else:
                failures.append(f"{kind}发送失败")
                log.error(f"[OB11] {kind}发送失败（消息未真正发出）: {contact}")
            return ok

        async def _send_mention(kind: str, names: list[str], body: str) -> bool:
            """发一条带 @ 的文字。

            微信的 @ 是「提及」对象而不是正文，发送器必须走成员选择弹层；
            发送器没有这条路径时如实记账，不退回纯文字（那会变成假提及）。
            """
            fn = getattr(state.sender_instance, "send_mention_text", None)
            if not callable(fn):
                failures.append("@ 提及发送失败（发送器不支持）")
                log.error("[OB11] 发送器没有 send_mention_text，@ 未发送")
                return False
            return await _send(kind, fn, contact, names, body)

        for item in send_plan.plan_message(message):
            kind = str(item.get("kind") or "")
            if kind == "mention":
                body = str(item.get("text") or "")
                names, problem = await asyncio.to_thread(
                    _resolve_mentions, item.get("targets") or [], is_group, contact_handle
                )
                if problem:
                    failures.append(problem)
                    log.warning(f"[OB11] @ 未发送：{problem}")
                    continue
                if await _send_mention("文字+@", names, body):
                    _record_out(contact, is_group, f"@{' @'.join(names)} {body}".strip())
                continue
            if kind == "text":
                seg = {"type": "text", "data": {"text": item.get("text") or ""}}
            else:
                seg = item.get("segment") or {}
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            seg_data = seg.get("data", {})

            if seg_type == "reply":
                # 引用回复段：微信 UIA 无法直接构造引用，跳过（后续 text 段正常发送）
                log.info("[OB11] 跳过 reply 段")
                skipped.append("引用回复段")
                continue

            if seg_type == "text":
                text = seg_data.get("text", "")
                if text:
                    if await _send("文字", state.sender_instance.send_text, contact, text):
                        _record_out(contact, is_group, text)

            elif seg_type == "image":
                file_val = seg_data.get("file", "")
                if not file_val:
                    continue
                img_path = None

                if file_val.startswith("base64://"):
                    try:
                        b64_data = file_val[9:]
                        img_path = await asyncio.to_thread(_decode_base64_image, b64_data)
                        if img_path:
                            log.info(f"[OB11] 图片已解码: {os.path.basename(img_path)}")
                    except Exception as e:
                        log.warning(f"[OB11] base64 图片解码失败: {e}")
                else:
                    if config.ASTRBOT_ATTACHMENTS:
                        candidates = [
                            os.path.join(config.ASTRBOT_ATTACHMENTS, file_val),
                            os.path.join(config.ASTRBOT_ATTACHMENTS, "wechat_images", file_val),
                        ]
                        for p in candidates:
                            if os.path.exists(p):
                                img_path = p
                                break
                        if not img_path:
                            log.warning(f"[OB11] 图片文件未找到: {file_val}")

                if img_path:
                    try:
                        if await _send("图片", state.sender_instance.send_image, contact, img_path):
                            _record_out(contact, is_group, "[图片]")
                    finally:
                        if img_path and "tmp" in img_path:
                            try:
                                os.unlink(img_path)
                            except Exception:
                                pass

            elif seg_type == "face":
                # 表情包本质是图片文件，复用图片发送链路
                file_val = seg_data.get("file", "")
                if not file_val:
                    # 无文件：尝试把 QQ 表情 id 映射为微信自带表情代码（[微笑] 等文本即渲染）
                    face_code = QQ_FACE_TO_WECHAT.get(_safe_int(seg_data.get("id")))
                    if face_code:
                        if await _send(
                            "QQ表情", state.sender_instance.send_text, contact, face_code
                        ):
                            _record_out(contact, is_group, face_code)
                    else:
                        log.debug(f"[OB11] 跳过未知 face 段: {seg_data}")
                    continue
                face_path = None

                if file_val.startswith("base64://"):
                    try:
                        b64_data = file_val[9:]
                        face_path = await asyncio.to_thread(_decode_base64_image, b64_data)
                        if face_path:
                            log.info(f"[OB11] 表情已解码: {os.path.basename(face_path)}")
                    except Exception as e:
                        log.warning(f"[OB11] base64 表情解码失败: {e}")
                else:
                    if config.ASTRBOT_ATTACHMENTS:
                        candidates = [
                            os.path.join(config.ASTRBOT_ATTACHMENTS, file_val),
                            os.path.join(config.ASTRBOT_ATTACHMENTS, "wechat_images", file_val),
                        ]
                        for p in candidates:
                            if os.path.exists(p):
                                face_path = p
                                break
                        if not face_path:
                            log.warning(f"[OB11] 表情文件未找到: {file_val}")

                if face_path:
                    try:
                        if await _send(
                            "表情", state.sender_instance.send_image, contact, face_path
                        ):
                            _record_out(contact, is_group, "[表情]")
                    finally:
                        if face_path and "tmp" in face_path:
                            try:
                                os.unlink(face_path)
                            except Exception:
                                pass

            elif seg_type == "voice":
                # 语音消息段。
                #
                # 微信 PC 客户端**没有"上传 mp3 当语音消息"的接口**——语音消息
                # 必须按住麦克风按钮录制。bridge 因此把 voice 段以"文件"形式
                # 发到聊天窗口（mp3 文件卡片，双击可播放）。
                #
                # 接受两种 data 格式：
                #   1. {"file": "base64://<mp3 base64>"} — 客户端 voice 段（base64 内联）
                #   2. {"file": "/abs/path/to/xxx.mp3"}   — 已落盘的本地文件
                file_val = seg_data.get("file", "") or seg_data.get("data", "")
                if not file_val:
                    log.warning(f"[OB11] 跳过 voice 段：无 file 字段: {seg_data}")
                    continue
                voice_path = None
                try:
                    if isinstance(file_val, str) and file_val.startswith("base64://"):
                        b64_data = file_val[9:]
                        try:
                            audio_bytes = base64.b64decode(b64_data)
                        except Exception as e:
                            log.warning(f"[OB11] voice base64 解码失败: {e}")
                            continue
                        # 写到临时文件，让 sender 走 send_file 链路
                        with tempfile.NamedTemporaryFile(
                            prefix="bridge_voice_", suffix=".mp3", delete=False
                        ) as tmp:
                            tmp.write(audio_bytes)
                            voice_path = tmp.name
                        log.info(
                            f"[OB11] voice 已解码: {os.path.basename(voice_path)} ({len(audio_bytes)} bytes)"
                        )
                    else:
                        # 已经是本地路径（兼容场景：bridge 之前做过 ASR 等）
                        if os.path.isfile(file_val):
                            voice_path = file_val
                        else:
                            log.warning(f"[OB11] voice 文件未找到: {file_val}")
                            continue

                    if voice_path:
                        if await _send(
                            "语音（文件形式）", state.sender_instance.send_file, contact, voice_path
                        ):
                            _record_out(contact, is_group, "[语音]")
                finally:
                    # 清理我们写出的临时 mp3（bridge_voice_ 前缀的才是我们的）
                    if voice_path and "bridge_voice_" in voice_path:
                        try:
                            os.unlink(voice_path)
                        except Exception:
                            pass

            elif seg_type == "video":
                # 视频段：微信 PC 端没有「提交视频消息」的入口，只能当文件发。
                # 文件一定能到对方手里；呈现形态（文件卡片还是视频气泡）由微信
                # 客户端按扩展名决定，这里不对它做断言。
                file_val = seg_data.get("file", "")
                if not file_val:
                    failures.append("视频缺少 file")
                    continue
                video_path, tmp_video = None, ""
                if isinstance(file_val, str) and file_val.startswith("base64://"):
                    try:
                        raw = base64.b64decode(file_val[9:])
                    except Exception as e:
                        failures.append(f"视频 base64 解码失败：{e}")
                        log.warning(f"[OB11] video base64 解码失败: {e}")
                        continue
                    with tempfile.NamedTemporaryFile(
                        prefix="bridge_video_", suffix=".mp4", delete=False
                    ) as tmp:
                        tmp.write(raw)
                        video_path = tmp_video = tmp.name
                    log.info(
                        f"[OB11] 视频已解码: {os.path.basename(video_path)} ({len(raw)} bytes)"
                    )
                elif os.path.isfile(file_val):
                    video_path = file_val
                else:
                    failures.append("视频文件未找到")
                    log.warning(f"[OB11] 视频文件未找到: {file_val}")
                    continue
                try:
                    if video_path and await _send(
                        "视频（文件形式）", state.sender_instance.send_file, contact, video_path
                    ):
                        _record_out(contact, is_group, "[视频]")
                finally:
                    if tmp_video:
                        try:
                            os.unlink(tmp_video)
                        except Exception:
                            pass

            else:
                # 未接线的段类型（file / forward 等）绝不静默跳过：
                # 跳过会让「零段发送」看起来像成功，客户端随即把内容记进上下文。
                log.warning(f"[OB11] 不支持的消息段类型: {seg_type!r}")
                failures.append(f"不支持的消息段 {seg_type or '（空类型）'}")

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
            log.warning(
                f"[OB11] 部分消息段未发出 [{contact}]：失败 {len(failures)} 段、跳过 {len(skipped)} 段"
            )

    elif action == "get_login_info":
        resp_data["data"] = {
            "user_id": state._self_id_int,
            "nickname": state._self_name or "wechat-bot",
        }

    elif action == "get_version_info":
        resp_data["data"] = {
            "app_name": "flandora",
            "app_version": __version__,
            "protocol_version": "v11",
        }

    elif action == "get_status":
        source = getattr(state.bridge_instance, "_source", None)
        resp_data["data"] = {
            "online": bool(state.running),
            "good": bool(state.running and state.sender_instance is not None),
            "app_name": "flandora",
            "app_version": __version__,
            "protocol_version": "v11",
            "receiver_ready": source is not None,
            "sender_ready": state.sender_instance is not None,
        }

    elif action == "can_send_image":
        resp_data["data"] = {"yes": True}

    elif action == "can_send_record":
        # 微信 PC 客户端只能按住麦克风录音，外部无法提交语音消息；
        # voice 段目前以 mp3 文件形式投递，因此这里如实回 false。
        resp_data["data"] = {"yes": False}

    elif action == "get_group_info":
        gid = params.get("group_id")
        handle = ob_ids.handle_of(gid) if isinstance(gid, int) else None
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
        handle = ob_ids.handle_of(uid) if isinstance(uid, int) else None
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
        ghandle = ob_ids.handle_of(gid) if isinstance(gid, int) else None
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
            handle = ob_ids.handle_of(uid) if isinstance(uid, int) else None
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
        handle = ob_ids.handle_of(target) if isinstance(target, int) else None
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


def make_message_event(
    message_type: str,
    user_id: int,
    message: list,
    group_id: int = 0,
    group_name: str = "",
    nickname: str = "",
    message_id: int = 0,
) -> dict:
    """构造 OneBot v11 消息事件。

    ``message`` 的形态由 ``ob_message_format`` 决定（消息段数组或 CQ 码字符串），
    ``raw_message`` 始终是 CQ 码字符串——客户端按字符串处理时两边都能用。
    """
    event = {
        "time": int(time.time()),
        "self_id": state._self_id_int,
        "post_type": "message",
        "message_id": message_id,
    }
    raw_cq = ob_segments.segments_to_cq(message)
    message_field = message if config.OB_MESSAGE_FORMAT == "array" else raw_cq
    if message_type == "group":
        event["message_type"] = "group"
        event["group_id"] = group_id
        event["user_id"] = user_id
        event["message"] = message_field
        event["raw_message"] = raw_cq
        event["sender"] = {"user_id": user_id, "nickname": nickname or str(user_id)}
        event["group_name"] = group_name or str(group_id)
    else:
        event["message_type"] = "private"
        event["user_id"] = user_id
        event["message"] = message_field
        event["raw_message"] = raw_cq
        event["sender"] = {"user_id": user_id, "nickname": nickname or str(user_id)}
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

    bi = state.bridge_instance
    store = getattr(getattr(bi, "_source", None), "store", None)
    if store is None:
        log.warning(f"[OB11] 收件源尚未就绪，历史拉取跳过 (talker={talker})")
        return out

    # 复用 bridge 实例的成员昵称缓存（没有则用 wxid 兜底）
    names = {}
    if bi is not None and hasattr(bi, "_resolve_names"):
        try:
            names = bi._resolve_names(talker)
        except Exception:  # noqa: BLE001
            names = {}

    group_id = ob_ids.contact_id(talker) if is_group else 0
    try:
        for m in store.recent_messages(talker, limit=count):
            text = (m.get("text") or "").strip()
            if not text:
                continue
            if m.get("outgoing"):
                uid = state._self_id_int
                nick = config.SELF_NAME or "bot"
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


def _store():
    """取当前桥接实例的只读本地库句柄；收件源未就绪时返回 None。"""
    return getattr(getattr(state.bridge_instance, "_source", None), "store", None)


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


def _members_roster(group_handle: str) -> list:
    """在线程池中执行：某个群的成员名单。"""
    store = _store()
    if store is None:
        return []
    return store.group_members(group_handle)


def _contact_info(handle: str) -> dict | None:
    """在线程池中执行：按 wxid 在通讯录里找一个人，找不到返回 None。"""
    for c in _contacts_roster():
        if c["username"] == handle:
            return c
    return None


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


def _resolve_mentions(targets: list, is_group: bool, group_handle: str) -> tuple[list[str], str]:
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
    for target in targets or []:
        if not isinstance(target, dict):
            continue
        qq = target.get("qq")
        name = str(target.get("name") or "").strip()
        if str(qq) in ("all", "all_pending") or name in ("全体成员", "all"):
            return [], "微信 PC 端没有 @ 全体成员的入口"
        member = None
        if isinstance(qq, int) and qq > 0:
            handle = ob_ids.handle_of(qq)
            member = by_handle.get(handle or "")
            if member is None:
                return [], f"未知的 user_id：{qq}（不是这个群的成员）"
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
