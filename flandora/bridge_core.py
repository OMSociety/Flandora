"""
桥接核心模块：Flandora 类。

职责：
1. 从微信本地库收件（WxLocalSource 轮询，见 wx_source）
2. 消息缓冲合并（BUFFER_SECONDS）
3. 构造 OneBot 事件，推送给已连接的客户端
4. 多层消息去重（rawid、内容、自回复）
"""

import base64
import logging
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from . import config, ob_ids, state, wx_crypto, wx_source
from .ob_protocol import make_message_event, push_event

try:
    from .webui import monitor

except Exception:
    monitor = None

log = logging.getLogger("ob11-bridge")

# OneBot v11 图片段的大小上限受 base64 膨胀与各客户端解析能力约束；超出时退回
# 文件路径引用（file://），让客户端自行读取，避免把几 MB 图塞进一条事件。
MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024


def _image_segment(image_path: str) -> dict | None:
    """把收件侧已解出的图片文件转成 OneBot v11 的 image 段。

    桥接只做协议转换：图片按原样交给客户端，不在这一层理解图片内容。
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
            return {"type": "image", "data": {"file": f"base64://{encoded}"}}
        # 过大时改用本地文件引用，文件名取原名，便于客户端缓存与显示
        return {"type": "image", "data": {"file": Path(image_path).as_uri()}}
    except OSError as exc:
        log.warning(f"读取图片失败，已跳过图片段: {exc}")
        return None


def _buffer_key(data: dict, session_id: str, is_group: bool) -> str:
    """同一会话内按发言人分缓冲。

    键里用 wxid 而不是昵称：昵称一改（或群里重名），同一个人就会被拆成两条
    缓冲、两条事件。
    """
    if not is_group:
        return session_id
    sender = data.get("sender", "") or data.get("senderName", "")
    return f"{session_id}_{sender}" if sender else session_id


# ============ 桥接核心 ============


class Flandora:
    """微信 ↔ OneBot v11 桥接器。"""

    def __init__(self, sender):
        self.sender = sender
        self.processed_ids = set()
        self.start_timestamp = int(time.time())
        self.pending_buffers = {}
        self.buffer_lock = threading.Lock()
        self.chat_histories = defaultdict(list)
        self.contact_map = {}
        self._source = None  # 本地库收件源（listen_local_db 启动时挂上）
        self._recent_seen = {}
        self._sent_recently = {}
        self._pending_image = {}  # talkerId → {"caption": None|str, "event": threading.Event()}
        self._member_cache = {}  # session_id → (缓存时间, {wxid: 显示名})

    def should_ignore(self, data):
        content = data.get("content", "")
        if data.get("sourceName", "") in config.BOT_NICKNAMES:
            return True
        if config.BOT_WXID and data.get("talkerId", "") == config.BOT_WXID:
            return True
        # 语音消息的 content 是 "[语音]" 占位符，按普通文本上报，不走单独分支
        if not content or content.strip() == "":
            return True
        return False

    def _is_mentioned(self, data):
        """检测群消息是否 @ 了机器人。

        @ 在微信里就是正文的一部分（形如「@昵称」），没有独立的结构字段。
        """
        content = data.get("content", "")
        if not content:
            return False

        for nick in config.BOT_NICKNAMES:
            # 标准 @昵称（ASCII @）
            if f"@{nick}" in content:
                return True
            # 全角 @昵称（部分微信版本可能用全角符号）
            if f"＠{nick}" in content:
                return True

        # 日志：content 以 @ 开头但未匹配到任何昵称（便于排查）
        if (content.startswith("@") or content.startswith("＠")) and len(content) > 1:
            log.debug(
                f"content 以 @ 开头但未匹配昵称: content={content[:40]!r} nicknames={config.BOT_NICKNAMES}"
            )

        return False

    def add_to_buffer(self, data):
        """将消息加入缓冲区，等待合并后统一推送给 OneBot 客户端。"""
        content = data.get("content", "")
        source_name = data.get("sourceName", "") or data.get("talkerName", "") or "未知"

        # 先判断群聊/私聊（图片/表情分支也需要用到）
        session_id_data = data.get("sessionId", "") or source_name
        group_name_raw = data.get("groupName", "")
        is_group = (
            (data.get("sessionType", "") == "group")
            or bool(group_name_raw)
            or "@chatroom" in session_id_data
        )

        # WebUI 监控：记录收到的消息（含图片/表情）
        if monitor is not None:
            monitor.record_inbound(
                contact=(group_name_raw or source_name) if is_group else source_name,
                sender=data.get("senderName", "")
                or data.get("sender", "")
                or data.get("sourceName", ""),
                kind="group" if is_group else "private",
                text=content,
            )

        # 本地库收件会把解出的图片路径接在占位符后面，因此按前缀判断。
        if content == "[图片]" or content.startswith("[图片] "):
            # 图片消息：异步下载并描述后推送
            threading.Thread(target=self.process_image_message, args=(data,), daemon=True).start()
            return

        if content in ("[动画表情]", "[表情]") or content.startswith("[表情] "):
            # 表情包消息：异步解析后推送
            threading.Thread(target=self.process_emoji_message, args=(data,), daemon=True).start()
            return

        now = time.time()
        if content and content in self._sent_recently and now - self._sent_recently[content] < 120:
            log.info(f"自回复去重跳过: {content[:30]}")
            return

        sender_in_group = (
            data.get("senderName", "") or data.get("sender", "") or data.get("sourceName", "")
        )
        # 发送者 wxid 是 ID 层的唯一依据（群消息里它才是「这个人」，
        # 昵称会改、同群可能重名，都不能当身份）
        sender_wxid = data.get("sender", "") or sender_in_group

        if is_group:
            group_raw = group_name_raw or source_name
            base_name = re.sub(r"\s*\(\d+\)\s*$", "", group_raw).strip()
            contact = base_name
            # 自动学习群名映射：若 SSE 推送了真实群名（非 @chatroom 原始 ID），存入映射供发送时用
            if group_name_raw and not group_name_raw.endswith("@chatroom"):
                state._ob_id_to_contact[ob_ids.contact_id(session_id_data)] = group_name_raw.strip()
                log.info(f"[群名学习] {session_id_data} -> {group_name_raw}")
        else:
            contact = source_name

        # 同一会话内按发言人分缓冲：同一人连发合并成一条，不同人互不干扰
        buffer_key = _buffer_key(data, session_id_data, is_group)

        with self.buffer_lock:
            if buffer_key not in self.pending_buffers:
                self.pending_buffers[buffer_key] = {
                    "messages": [],
                    "timer": None,
                    "timer_version": 0,
                    "processing": False,
                    "contact": contact,
                    "is_group": is_group,
                    "source_name": source_name,
                    "group_name": base_name if is_group else "",
                    "sender_in_group": sender_in_group if is_group else "",
                    "sender_wxid": sender_wxid,
                    "local_id": int(data.get("localId") or 0),
                    "session_id_data": session_id_data,
                    "raw_data": data,  # 原始 SSE 数据，供 @ 检测等使用
                }
            entry = self.pending_buffers[buffer_key]
            entry["messages"].append(content)

            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["timer_version"] += 1
                version = entry["timer_version"]

                buffer_delay = config.BUFFER_SECONDS
                log.info(f"收到来自 {contact} 的消息，等待 {buffer_delay}s 后统一推送")
                timer = threading.Timer(
                    buffer_delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v)
                )
                timer.daemon = True
                timer.start()
                entry["timer"] = timer

    def _resolve_names(self, session_id: str) -> dict:
        """解析会话成员 wxid → 显示名（带 10 分钟缓存）。

        名字统一取自本地库的 contact 表（备注名优先）。群里发言的人只要在联系人
        表里就能查到，查不到就退回 wxid。get_group_msg_history 用它还原历史发言人。
        """
        now = time.time()
        cached = self._member_cache.get(session_id)
        if cached and now - cached[0] < 600:
            return cached[1]

        names = {}
        store = getattr(self._source, "store", None) if self._source else None
        if store is not None:
            try:
                names = dict(store.display_names())
            except Exception as e:  # noqa: BLE001
                log.warning(f"读取联系人失败 (session={session_id}): {e}")
        self._member_cache[session_id] = (now, names)
        return names

    @staticmethod
    def _message_id_for(session_handle: str, local_id) -> int:
        """取这条消息的 OneBot message_id。

        有本地库行号就按 (会话, 行号) 幂等分配——同一条消息任何时候取都是同一个号；
        图片/表情这类注入的条目没有行号，现发一个新号。
        """
        try:
            lid = int(local_id or 0)
        except (TypeError, ValueError):
            lid = 0
        if lid > 0:
            return ob_ids.ensure_message_id(session_handle, lid)
        return ob_ids.allocate_message_id(session_handle)

    def process_sender(self, sender_id, version=None):
        """缓冲到期：通过 OneBot 事件推送。"""
        with self.buffer_lock:
            if sender_id not in self.pending_buffers:
                return
            entry = self.pending_buffers[sender_id]
            if version is not None and entry.get("timer_version", 0) != version:
                return
            if not entry["messages"]:
                return
            msgs = entry["messages"].copy()
            # 图片段取出后清空，避免下一轮重复下发同一批图片
            image_segments = entry.pop("image_segments", []) or []
            entry["messages"] = []
            entry["processing"] = True
            if entry["timer"]:
                entry["timer"].cancel()
                entry["timer"] = None

        contact = entry.get("contact", sender_id)
        is_group = entry.get("is_group", False)
        combined = "\n".join(msgs)
        log.info(f"推送 {len(msgs)} 条消息 [{'群' if is_group else '私'}|{contact}]")

        try:
            # OneBot 的 ID 全是整数：会话与发言人先经 ID 层换成稳定整数，
            # 原文字符串只在映射表里留底（发送时按它搜索微信会话）。
            session_handle = entry.get("session_id_data", "") or str(sender_id)
            sender_handle = entry.get("sender_wxid") or session_handle
            user_id = ob_ids.contact_id(sender_handle)
            group_id = ob_ids.contact_id(session_handle) if is_group else 0
            message_id = self._message_id_for(session_handle, entry.get("local_id"))

            if is_group:
                sender_name = entry.get("sender_in_group", "") or entry.get("source_name", "未知")

                # 正文保持原样：发言人、群名由事件的 sender/group 字段承载，
                # 是否 @ 了机器人由 is_mentioned 标记，内容不做改写
                formatted = combined
                for nick in config.BOT_NICKNAMES:
                    formatted = formatted.replace(f"@{nick}\u2005", f"@{nick}")
                    formatted = formatted.replace(f"@{nick}\u200b", f"@{nick}")

                msg_segments = [{"type": "text", "data": {"text": formatted}}]
                msg_segments.extend(image_segments)
                event = make_message_event(
                    "group",
                    user_id,
                    msg_segments,
                    group_id=group_id,
                    group_name=entry.get("group_name", contact),
                    nickname=sender_name,
                    message_id=message_id,
                )
                # 标记该群消息是否真实 @ 了机器人，由客户端决定是否响应
                event["is_mentioned"] = bool(self._is_mentioned(entry.get("raw_data", {})))
                state._ob_id_to_contact[group_id] = contact
                state._ob_id_to_contact[user_id] = sender_name
            else:
                sender_name = entry.get("source_name", contact)
                private_segments = [{"type": "text", "data": {"text": combined}}]
                private_segments.extend(image_segments)
                event = make_message_event(
                    "private",
                    user_id,
                    private_segments,
                    nickname=sender_name,
                    message_id=message_id,
                )
                state._ob_id_to_contact[user_id] = contact

            sent = push_event(event)
            if sent > 0:
                log.info(f"已推送至 {sent} 个 OneBot 客户端 [{contact}]")
                if monitor is not None:
                    monitor.bump("pushed")
            else:
                log.warning(f"无 OneBot 客户端在线 [{contact}]")

        except Exception as e:
            # 推送链路任何异常都不能让缓冲区卡死（processing 必须复位）
            log.error(f"推送消息异常 [{contact}]: {e}")
        finally:
            with self.buffer_lock:
                if sender_id in self.pending_buffers:
                    entry = self.pending_buffers[sender_id]
                    entry["processing"] = False
                    # 处理期间又积压了消息 → 立即安排下一轮推送，
                    # 否则这些消息会一直滞留（消息多时不再推送的根因）
                    if entry["messages"] and entry.get("timer") is None:
                        entry["timer_version"] += 1
                        v = entry["timer_version"]
                        t = threading.Timer(
                            config.BUFFER_SECONDS,
                            lambda v=v, sid=sender_id: self.process_sender(sid, v),
                        )
                        t.daemon = True
                        t.start()
                        entry["timer"] = t
                        log.info(f"{contact} 积压 {len(entry['messages'])} 条，已安排下一轮推送")

    def _ingest(self, data):
        """一条消息进入缓冲区的唯一入口。

        收件源只负责把消息整理成统一形态，去重、过滤与缓冲都在这里，
        这样换收件源不会影响后面的链路。
        """
        raw_id = data.get("rawid", "")
        if raw_id:
            if raw_id in self.processed_ids:
                return False
            self.processed_ids.add(raw_id)
        if self.should_ignore(data):
            return False
        if data.get("sessionType", "") == "group" or "@chatroom" in data.get("sessionId", ""):
            content = data.get("content", "")
            log.info(f"收到群消息 [{data.get('sourceName', '')}]: {content[:60]}")
            log.info(f"   @={self._is_mentioned(data)}")
        else:
            log.info(f"收到: {data.get('sourceName', '')}: {data.get('content', '')[:50]}")
        self.add_to_buffer(data)
        return True

    def listen_local_db(self):
        """轮询微信本地库收件。

        收件源只负责把消息整理成统一形态，去重与缓冲都交给 _ingest。
        """
        try:
            source = wx_source.build_source()
        except Exception as e:  # noqa: BLE001
            log.error(f"本地库收件源启动失败：{e}")
            return

        self._source = source  # 历史回填与昵称解析都从这里取 store
        source.prime()
        try:
            while state.running:
                try:
                    for data in source.poll():
                        if not state.running:
                            break
                        self._ingest(data)
                except wx_crypto.WrongKey:
                    log.error("微信口令与当前登录账号对不上（切换过账号？），本地库收件停止")
                    return
                except Exception as e:  # noqa: BLE001
                    log.warning(f"轮询本地库出错：{e}")
                time.sleep(source.poll_seconds)
        finally:
            log.info("本地库收件已停止")

    def process_image_message(self, data):
        """处理图片消息：把收件侧已解出的图片按 OneBot image 段交给客户端。

        桥接不解读图片内容：图片原样下发，是否描述、如何描述由客户端决定。
        """
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")

        log.info(f"收到图片: {source_name}" + (f" (群:{group_name})" if group_name else ""))

        talker_id = data.get("talkerId", "") or data.get("sessionId", "")
        is_group = bool(group_name) or "@chatroom" in session_id
        sender_display = data.get("senderName", "") or source_name
        # 群内按发言人分键，与文本消息的缓冲键一致：同人的图文能合并，
        # 不同人的图片不会串到同一条事件里
        buffer_key = _buffer_key(data, session_id, is_group)

        img_event = threading.Event()
        self._pending_image[talker_id] = {"caption": None, "event": img_event}

        try:
            image_path = data.get("imagePath") or ""
            segment = _image_segment(image_path)
            placeholder = "[图片]"

            with self.buffer_lock:
                self._pending_image[talker_id] = {"caption": placeholder, "event": img_event}

                if buffer_key in self.pending_buffers:
                    entry = self.pending_buffers[buffer_key]
                    entry["messages"].insert(0, placeholder)
                    if segment:
                        entry.setdefault("image_segments", []).append(segment)
                    entry["image_ready"] = True
                else:
                    # 没有文本排队，创建单条图片消息处理
                    self.pending_buffers[buffer_key] = {
                        "messages": [placeholder],
                        "image_segments": [segment] if segment else [],
                        "timer": None,
                        "timer_version": 0,
                        "processing": False,
                        "contact": group_name if is_group and group_name else source_name,
                        "is_group": is_group,
                        "source_name": source_name,
                        "session_id_data": session_id,
                        "group_name": group_name if is_group else "",
                        "sender_in_group": sender_display if is_group else "",
                        "sender_wxid": data.get("sender", "") or sender_display,
                        "local_id": int(data.get("localId") or 0),
                    }
                    version = 1
                    timer = threading.Timer(
                        2, lambda v=version, sid=buffer_key: self.process_sender(sid, v)
                    )
                    timer.daemon = True
                    timer.start()
                    self.pending_buffers[buffer_key]["timer"] = timer
                    self.pending_buffers[buffer_key]["timer_version"] = version
        finally:
            # 确保 Event 被设置
            img_event.set()

    def process_emoji_message(self, data):
        """处理表情包消息：以 image 段转发图片。

        是否解读表情含义由客户端决定，桥接只负责把图片交出去。
        """
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        content = data.get("content", "[表情]")

        log.info(f"收到表情包: {source_name}" + (f" (群:{group_name})" if group_name else ""))

        is_group = bool(group_name) or "@chatroom" in session_id
        sender_display = data.get("senderName", "") or source_name
        buffer_key = _buffer_key(data, session_id, is_group)

        try:
            image_path = data.get("imagePath") or ""
            segment = _image_segment(image_path)

            # 直接注入缓冲区（不等待，立即推送），图片段随事件一并下发
            self.add_text_to_buffer(
                buffer_key,
                source_name,
                group_name,
                session_id,
                content,
                is_group,
                buffer_key,
                image_segment=segment,
                sender_display=sender_display,
                sender_wxid=data.get("sender", "") or sender_display,
                local_id=int(data.get("localId") or 0),
            )
        except Exception as e:
            log.warning(f"表情包处理异常: {e}")
            # 异常时也保底发送原文
            self.add_text_to_buffer(
                buffer_key,
                source_name,
                group_name,
                session_id,
                content,
                is_group,
                buffer_key,
                sender_display=sender_display,
                sender_wxid=data.get("sender", "") or sender_display,
                local_id=int(data.get("localId") or 0),
            )

    def add_text_to_buffer(
        self,
        session_id_data,
        source_name,
        group_name,
        session_id,
        content,
        is_group,
        sender_key,
        image_segment: dict | None = None,
        sender_display: str | None = None,
        sender_wxid: str | None = None,
        local_id: int = 0,
    ):
        """通用：将一段文本（可选附带图片段）加入缓冲队列。"""
        sender_display = sender_display or source_name
        with self.buffer_lock:
            buffer_key = sender_key
            if buffer_key not in self.pending_buffers:
                self.pending_buffers[buffer_key] = {
                    "messages": [],
                    "image_segments": [],
                    "timer": None,
                    "timer_version": 0,
                    "processing": False,
                    "contact": group_name if is_group and group_name else source_name,
                    "is_group": is_group,
                    "source_name": source_name,
                    "session_id_data": session_id,
                    "group_name": group_name if is_group else "",
                    "sender_in_group": sender_display if is_group else "",
                    "sender_wxid": sender_wxid or sender_display,
                    "local_id": int(local_id or 0),
                }
            entry = self.pending_buffers[buffer_key]
            entry["messages"].append(content)
            if image_segment:
                entry.setdefault("image_segments", []).append(image_segment)

            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["timer_version"] += 1
                version = entry["timer_version"]
                delay = 2  # 表情/图片单独推送，短缓冲
                timer = threading.Timer(
                    delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v)
                )
                timer.daemon = True
                timer.start()
                entry["timer"] = timer
                entry["timer_version"] = version
