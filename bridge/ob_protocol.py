"""
OneBot v11 协议处理模块（对接 MaiBot 微信适配器）。

- make_message_event() — 构造 OneBot 消息事件 JSON
- push_event() — 广播事件给所有连接的 MaiBot 客户端
- _handle_ob_api() — 处理 MaiBot 发来的 API 请求（send_msg、get_login_info 等）
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
import time

import requests

import state
import config
from ws_pool import broadcast as push_event_broadcast

try:
    from webui import monitor
except Exception:
    monitor = None

log = logging.getLogger("ob11-bridge")


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
    0: "[惊讶]", 1: "[撇嘴]", 2: "[色]", 3: "[发呆]", 4: "[得意]",
    5: "[流泪]", 6: "[害羞]", 7: "[闭嘴]", 8: "[睡]", 9: "[大哭]",
    10: "[尴尬]", 11: "[发怒]", 12: "[调皮]", 13: "[呲牙]", 14: "[微笑]",
    15: "[难过]", 18: "[抓狂]", 19: "[吐]", 20: "[偷笑]", 22: "[白眼]",
    23: "[傲慢]", 26: "[汗]",
}


def push_event(event: dict) -> int:
    """广播 OneBot 事件给所有 MaiBot 客户端。返回成功推送数。"""
    return push_event_broadcast(event)


async def _send_resp_to(ws, resp_data: dict):
    """向指定 WS 连接发送响应（服务端 loop 内直接 send，无跨线程）。"""
    try:
        await ws.send(json.dumps(resp_data, ensure_ascii=False))
        log.info(f"[OB11] 已回响应: {resp_data.get('echo', '')}")
    except Exception as e:
        log.warning(f"[OB11] 回响应失败: {e}")


async def _handle_ob_api(data: dict):
    """处理 MaiBot 微信适配器发来的 API 请求。

    返回响应 dict（由服务端 handler 负责发送）；返回 None 表示无需响应。
    """
    action = data.get("action", "")
    params = data.get("params", {}) or {}
    echo = data.get("echo", "")
    log.info(f"[OB11] API: {action} echo={echo}")

    resp_data = {"status": "ok", "retcode": 0, "data": {}}

    if action in ("send_msg", "send_private_msg", "send_group_msg"):
        is_group = action == "send_group_msg" or params.get("message_type") == "group"
        target_id = params.get("group_id" if is_group else "user_id", 0)
        message = params.get("message", [])
        contact = state._ob_id_to_contact.get(target_id, str(target_id))
        # 群聊：优先用群名映射（微信搜索框搜不到 @chatroom ID）
        if is_group:
            mapped_name = config.GROUP_NAME_MAP.get(str(target_id), "")
            if mapped_name:
                contact = mapped_name
                log.info(f"[OB11] 群名映射: {target_id} -> {mapped_name}")
            elif str(target_id).endswith("@chatroom"):
                log.warning(f"[OB11] 群 {target_id} 未配置群名映射，微信搜索可能失败。请在 config.json 的 group_name_map 中配置群名。")

        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            seg_data = seg.get("data", {})

            if seg_type == "reply":
                # 引用回复段：微信 UIA 无法直接构造引用，跳过（后续 text 段正常发送）
                log.info(f"[OB11] 跳过 reply 段")
                continue

            if seg_type == "text":
                text = seg_data.get("text", "")
                if text:
                    await asyncio.to_thread(state.sender_instance.send_text, contact, text)
                    log.info(f"[OB11] 文字已发送至 {contact}: {text[:50]}")
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
                        await asyncio.to_thread(state.sender_instance.send_image, contact, img_path)
                        log.info(f"[OB11] 图片已发送至 {contact}")
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
                        await asyncio.to_thread(state.sender_instance.send_text, contact, face_code)
                        log.info(f"[OB11] QQ表情已转为微信表情发送至 {contact}: {face_code}")
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
                        await asyncio.to_thread(state.sender_instance.send_image, contact, face_path)
                        log.info(f"[OB11] 表情已发送至 {contact}")
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
                #   1. {"file": "base64://<mp3 base64>"} — MaiBot voice segment（base64 内联）
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
                        log.info(f"[OB11] voice 已解码: {os.path.basename(voice_path)} ({len(audio_bytes)} bytes)")
                    else:
                        # 已经是本地路径（兼容场景：bridge 之前做过 ASR 等）
                        if os.path.isfile(file_val):
                            voice_path = file_val
                        else:
                            log.warning(f"[OB11] voice 文件未找到: {file_val}")
                            continue

                    if voice_path:
                        await asyncio.to_thread(state.sender_instance.send_file, contact, voice_path)
                        log.info(f"[OB11] 语音（文件形式）已发送至 {contact}")
                        _record_out(contact, is_group, "[语音]")
                finally:
                    # 清理我们写出的临时 mp3（bridge_voice_ 前缀的才是我们的）
                    if voice_path and "bridge_voice_" in voice_path:
                        try:
                            os.unlink(voice_path)
                        except Exception:
                            pass

        resp_data["data"] = {"message_id": int(time.time() * 1000) % 1000000000}

    elif action == "get_login_info":
        resp_data["data"] = {
            "user_id": state._self_id_int,
            "nickname": state._self_name or "wechat-bot",
        }

    elif action == "get_friend_list":
        resp_data["data"] = []

    elif action == "get_group_list":
        resp_data["data"] = []

    elif action == "get_group_info":
        gid = params.get("group_id", "")
        resp_data["data"] = {
            "group_id": gid,
            "group_name": state._ob_id_to_contact.get(gid, str(gid)),
        }

    elif action in ("get_group_msg_history", "get_friend_msg_history"):
        # 从 WeFlow REST 拉取聊天历史（插件可主动读取上下文）
        is_g = action == "get_group_msg_history"
        talker = str(params.get("group_id" if is_g else "user_id", "") or "")
        try:
            count = int(params.get("count", 20) or 20)
        except (TypeError, ValueError):
            count = 20
        msgs = await asyncio.to_thread(_fetch_history_ob, talker, is_g, count)
        resp_data["data"] = {"messages": msgs}

    elif action in ("get_group_member_info", "get_group_member_list", "get_stranger_info",
                    "get_group_honor_info", "get_group_at_all_remain", "get_friend_info",
                    "get_msg", "get_forward_msg",
                    "get_cookies", "get_csrf_token", "get_credentials", "get_record", "get_image"):
        resp_data["data"] = {}

    elif action in ("send_group_forward_msg", "send_private_forward_msg"):
        log.warning(f"[OB11] 转发消息暂不支持: {action}")
        resp_data["data"] = {}

    elif action == "set_msg_emoji_like":
        resp_data["data"] = {}

    elif action in ("set_group_kick", "set_group_ban", "set_group_anonymous_ban",
                    "set_group_whole_ban", "set_group_admin", "set_group_anonymous",
                    "set_group_card", "set_group_name", "set_group_leave",
                    "set_group_special_title", "set_friend_add_request",
                    "set_group_add_request", "set_essence_msg", "delete_msg",
                    "send_like", "set_friend_add_request"):
        resp_data["data"] = {}

    else:
        log.debug(f"[OB11] 未处理 API: {action}")

    if echo:
        resp_data["echo"] = echo
    return resp_data


def make_message_event(message_type: str, user_id: int, message: list,
                       group_id: int = 0, group_name: str = "",
                       nickname: str = "") -> dict:
    """构造 OneBot v11 消息事件"""
    event = {
        "time": int(time.time()),
        "self_id": state._self_id_int,
        "post_type": "message",
    }
    if message_type == "group":
        event["message_type"] = "group"
        event["group_id"] = group_id
        event["user_id"] = user_id
        event["message"] = message
        event["raw_message"] = "".join(
            seg.get("data", {}).get("text", "") for seg in message
            if seg.get("type") == "text"
        )
        event["sender"] = {"user_id": user_id, "nickname": nickname or str(user_id)}
        event["group_name"] = group_name or str(group_id)
    else:
        event["message_type"] = "private"
        event["user_id"] = user_id
        event["message"] = message
        event["raw_message"] = "".join(
            seg.get("data", {}).get("text", "") for seg in message
            if seg.get("type") == "text"
        )
        event["sender"] = {"user_id": user_id, "nickname": nickname or str(user_id)}
    return event


def _fetch_history_ob(talker: str, is_group: bool, count: int) -> list:
    """从 WeFlow REST 拉取会话历史，转换为 OneBot v11 消息格式列表。

    在线程池中执行（asyncio.to_thread）。失败返回空列表，不抛异常。
    """
    out = []
    if not talker:
        return out
    count = max(1, min(count, 100))
    try:
        resp = requests.get(
            f"{config.WE_FLOW_BASE_URL}/api/v1/messages",
            params={"access_token": config.ACCESS_TOKEN, "talker": talker, "limit": count},
            timeout=8,
        )
        if resp.status_code != 200:
            log.warning(f"[OB11] 拉取历史 HTTP {resp.status_code} (talker={talker})")
            return out
        data = resp.json()
        rows = data if isinstance(data, list) else data.get("messages", data.get("data", []))
        if not isinstance(rows, list):
            return out

        # 复用 bridge 实例的成员昵称缓存（没有则用 wxid 兜底）
        names = {}
        bi = state.bridge_instance
        if bi is not None and hasattr(bi, "_resolve_names"):
            try:
                names = bi._resolve_names(talker)
            except Exception:
                names = {}

        type_placeholder = {3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情]", 49: "[链接/文件]"}
        rows = sorted(rows, key=lambda m: m.get("createTime", 0) or 0)
        for m in rows[-count:]:
            lt = m.get("localType", 1)
            if lt == 1:
                text = (m.get("content") or "").strip()
            else:
                text = type_placeholder.get(lt, "")
            if not text:
                continue
            sender_wxid = m.get("senderUsername", "") or ""
            if m.get("isSend"):
                uid = state._self_id_int
                nick = config.SELF_NAME or "bot"
            else:
                uid = sender_wxid
                nick = names.get(sender_wxid) or sender_wxid or "未知"
            msg = {
                "time": m.get("createTime", 0) or 0,
                "message_type": "group" if is_group else "private",
                "message_id": m.get("serverId") or m.get("localId", 0),
                "user_id": uid,
                "sender": {"user_id": uid, "nickname": nick},
                "raw_message": text,
                "message": [{"type": "text", "data": {"text": text}}],
            }
            if is_group:
                msg["group_id"] = talker
            out.append(msg)
        log.info(f"[OB11] 历史拉取 {talker}: {len(out)} 条")
    except Exception as e:
        log.warning(f"[OB11] 拉取历史失败 (talker={talker}): {e}")
    return out


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
