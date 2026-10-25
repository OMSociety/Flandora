"""
桥接核心模块：Flandora 类。

职责：
1. 从微信本地库收件（WxLocalSource 轮询，见 wx_source）
2. 构造 OneBot 事件，推送给已连接的客户端
3. 多层消息去重（rawid、内容、自回复）

一条收件消息对应一个事件：合并、排队与回复策略都属于客户端，桥接不替它决定。
"""

import base64
import logging
import os
import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from . import ob_ids, ob_inbound, state, wx_crypto, wx_source
from .ob_protocol import (
    make_message_event,
    make_poke_event,
    make_recall_event,
    push_event,
)

try:
    from .webui import monitor

except Exception:
    monitor = None

log = logging.getLogger("flandora")

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


# ============ 桥接核心 ============


class Flandora:
    """微信 ↔ OneBot v11 桥接器。"""

    def __init__(self, sender):
        self.sender = sender
        self.processed_ids = set()
        self.start_timestamp = int(time.time())
        self.chat_histories = defaultdict(list)
        self.contact_map = {}
        self._source = None  # 本地库收件源（listen_local_db 启动时挂上）
        self._recent_seen = {}
        self._sent_recently = {}
        self._member_cache = {}  # session_id → (缓存时间, {wxid: 显示名})
        self._nick_store = None  # 自己昵称的缓存宿主（store 换掉就重读）
        self._nick = ""

    def self_nickname(self):
        """机器人自己的微信昵称（本地通讯录里自己那行），读不到返回空串。

        @ 识别与防自回环都以这个昵称为准，它随登录账号变化，所以不落配置。
        按当前收件源的 store 缓存；读不到时不记缓存，下次调用再试。
        """
        store = getattr(self._source, "store", None) if self._source else None
        if store is None:
            return ""
        if store is not self._nick_store:
            try:
                nick = store.self_nickname() or ""
            except Exception:  # noqa: BLE001
                nick = ""
            if not nick:
                return ""
            self._nick = nick
            self._nick_store = store
        return self._nick

    def _nicknames(self):
        """@ 识别用的称呼列表；昵称读不到时为空列表（判定为「没人 @ 我」）。"""
        nick = self.self_nickname()
        return [nick] if nick else []

    def should_ignore(self, data):
        content = data.get("content", "")
        nick = self.self_nickname()
        if nick and data.get("sourceName", "") == nick:
            return True
        # 防自回环第二道：按运行时服务的登录账号精确匹配（wxid 不随改名漂移）
        my_wxid = getattr(getattr(self._source, "store", None), "my_wxid", "")
        if my_wxid and data.get("talkerId", "") == my_wxid:
            return True
        # 语音消息的 content 是 "[语音]" 占位符，按普通文本上报，不走单独分支
        if not content or content.strip() == "":
            return True
        return False

    def _is_mentioned(self, data):
        """检测群消息是否 @ 了机器人。

        @ 在微信里就是正文的一部分（形如「@昵称」），没有独立的结构字段；
        比对的昵称取自本地通讯录里自己那行，改名或换号后不需要改配置。
        """
        content = data.get("content", "")
        if not content:
            return False

        nick = self.self_nickname()
        if nick:
            # 标准 @昵称（ASCII @）
            if f"@{nick}" in content:
                return True
            # 全角 @昵称（部分微信版本可能用全角符号）
            if f"＠{nick}" in content:
                return True

        # 日志：content 以 @ 开头但没匹配到自己昵称（读不到昵称时会一直走这里，便于排查）
        if (content.startswith("@") or content.startswith("＠")) and len(content) > 1:
            log.debug(
                f"content 以 @ 开头但未匹配自己昵称: content={content[:40]!r} self_nickname={nick!r}"
            )

        return False

    def emit_message(self, data):
        """处理一条收件消息，立即组装成 OneBot 事件推送。

        合并多条、按时间窗排队、决定要不要回，都是客户端的活；桥接收到一条就
        报一条，不做延迟与拼接。
        """
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
        # 撤回与拍一拍不是聊天正文：转成 notice 事件，别混进 message 流
        notice = self._notice_for(data, is_group, session_id_data)
        if notice is not None:
            push_event(notice)
            log.info(
                f"[OB11] 通知事件已推送：{notice.get('notice_type')}"
                f"{'/' + notice['sub_type'] if notice.get('sub_type') else ''}"
            )
            return

        if content and content in self._sent_recently and now - self._sent_recently[content] < 120:
            log.info(f"自回复去重跳过: {content[:30]}")
            return

        if is_group:
            # 自动学习群名映射：若推送了真实群名（非 @chatroom 原始 ID），存入映射供发送时用
            if group_name_raw and not group_name_raw.endswith("@chatroom"):
                state._ob_id_to_contact[ob_ids.contact_id(session_id_data)] = group_name_raw.strip()
                log.info(f"[群名学习] {session_id_data} -> {group_name_raw}")

        self._push_entry(self._entry_for(data, content))

    def _notice_for(self, data, is_group: bool, session_handle: str):
        """把撤回与拍一拍从消息流里摘出来，转成 OneBot 通知事件。

        这两类在本地库里是独立行（`local_type=10000` 的 sysmsg、appmsg 子类型 62），
        照常当正文推上去会让客户端收到「[撤回了一条消息]」「[拍一拍] …」这种假消息，
        也没法据此更新会话状态。
        """
        revoke = data.get("revoke") or {}
        pat = data.get("pat") or {}
        if not revoke and not pat:
            return None
        group_id = ob_ids.contact_id(session_handle) if is_group else 0

        if revoke:
            nickname = str(revoke.get("nickname") or data.get("senderName") or "").strip()
            wxid = self._wxid_for_name(session_handle, nickname)
            user_id = ob_ids.contact_id(wxid) if wxid else 0
            return make_recall_event(
                group_id=group_id,
                user_id=user_id,
                operator_id=user_id,
                message_id=0,
                nickname=nickname,
            )

        from_wxid = str(pat.get("from") or "").strip()
        to_wxid = str(pat.get("to") or "").strip()
        return make_poke_event(
            group_id=group_id,
            user_id=ob_ids.contact_id(from_wxid) if from_wxid else 0,
            target_id=ob_ids.contact_id(to_wxid) if to_wxid else 0,
            suffix=str(pat.get("suffix") or "").strip(),
        )

    def _wxid_for_name(self, session_handle: str, name: str) -> str:
        """按显示名反查 wxid；重名或查不到都返回空串（宁可给 0 也不猜一个人）。"""
        if not name:
            return ""
        hits = [
            wxid for wxid, shown in self._resolve_names(session_handle).items() if shown == name
        ]
        return hits[0] if len(hits) == 1 else ""

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

    def _inbound_segments(self, entry, text: str, session_handle: str) -> list[dict]:
        """把一条收到的消息还原成 OneBot 段。

        引用在前（客户端据此拿到被引消息的 message_id），正文里的真 @ 拆成 at 段，
        其余保持文本。还原不出来的一律留在原文里，不改写内容。
        """
        segments: list[dict] = []
        reply = ob_inbound.reply_segment(
            entry.get("quote") or {}, ob_ids.message_ids().message_by_server
        )
        if reply:
            segments.append(reply)
        names = self._resolve_names(session_handle)
        segments.extend(
            ob_inbound.split_mentions(text, lambda name: ob_inbound.resolve_from_names(names, name))
        )
        return segments

    @staticmethod
    def _message_id_for(session_handle: str, local_id, server_id: str = "") -> int:
        """取这条消息的 OneBot message_id。

        有本地库行号就按 (会话, 行号) 幂等分配——同一条消息任何时候取都是同一个号；
        图片/表情这类注入的条目没有行号，现发一个新号。
        """
        try:
            lid = int(local_id or 0)
        except (TypeError, ValueError):
            lid = 0
        if lid > 0:
            return ob_ids.ensure_message_id(session_handle, lid, server_id=server_id or "")
        return ob_ids.allocate_message_id(session_handle)

    def _entry_for(self, data: dict, content: str, image_segments=None) -> dict:
        """把一条收件消息整理成推送用的中间结构。

        每条消息各一份，不做跨消息合并；群名去掉微信附加的 "(3)" 人数后缀。
        """
        source_name = data.get("sourceName", "") or data.get("talkerName", "") or "未知"
        session_id_data = data.get("sessionId", "") or source_name
        group_name_raw = data.get("groupName", "")
        is_group = (
            (data.get("sessionType", "") == "group")
            or bool(group_name_raw)
            or "@chatroom" in session_id_data
        )
        sender_in_group = (
            data.get("senderName", "") or data.get("sender", "") or data.get("sourceName", "")
        )
        if is_group:
            contact = re.sub(r"\s*\(\d+\)\s*$", "", group_name_raw or source_name).strip()
        else:
            contact = source_name
        return {
            "content": content,
            "image_segments": list(image_segments or []),
            "contact": contact,
            "is_group": is_group,
            "source_name": source_name,
            "group_name": contact if is_group else "",
            "sender_in_group": sender_in_group if is_group else "",
            # 发送者 wxid 是 ID 层的唯一依据（群消息里它才是「这个人」，
            # 昵称会改、同群可能重名，都不能当身份）
            "sender_wxid": data.get("sender", "") or sender_in_group,
            "local_id": int(data.get("localId") or 0),
            "server_id": str(data.get("serverId") or ""),
            "quote": data.get("quote") or {},
            "timestamp": int(data.get("timestamp") or 0),
            "session_id_data": session_id_data,
            "raw_data": data,  # 原始数据，供 @ 检测等使用
        }

    def _push_entry(self, entry: dict):
        """把一条消息组装成 OneBot 事件推给已连接的客户端。"""
        contact = entry.get("contact", "")
        is_group = entry.get("is_group", False)
        combined = entry.get("content", "")
        image_segments = list(entry.get("image_segments") or [])
        log.info(f"推送消息 [{'群' if is_group else '私'}|{contact}]")

        try:
            # OneBot 的 ID 全是整数：会话与发言人先经 ID 层换成稳定整数，
            # 原文字符串只在映射表里留底（发送时按它搜索微信会话）。
            session_handle = entry.get("session_id_data", "") or str(contact)
            sender_handle = entry.get("sender_wxid") or session_handle
            user_id = ob_ids.contact_id(sender_handle)
            group_id = ob_ids.contact_id(session_handle) if is_group else 0
            message_id = self._message_id_for(
                session_handle, entry.get("local_id"), entry.get("server_id", "")
            )

            if is_group:
                sender_name = entry.get("sender_in_group", "") or entry.get("source_name", "未知")

                # 正文保持原样：发言人、群名由事件的 sender/group 字段承载，
                # 是否 @ 了机器人由 is_mentioned 标记，内容不做改写
                formatted = combined
                for nick in self._nicknames():
                    formatted = formatted.replace(f"@{nick}\u2005", f"@{nick}")
                    formatted = formatted.replace(f"@{nick}\u200b", f"@{nick}")

                msg_segments = self._inbound_segments(entry, formatted, session_handle)
                msg_segments.extend(image_segments)
                event = make_message_event(
                    "group",
                    user_id,
                    msg_segments,
                    group_id=group_id,
                    group_name=entry.get("group_name", contact),
                    nickname=sender_name,
                    message_id=message_id,
                    timestamp=entry.get("timestamp", 0),
                )
                # 标记该群消息是否真实 @ 了机器人，由客户端决定是否响应
                event["is_mentioned"] = bool(self._is_mentioned(entry.get("raw_data", {})))
                state._ob_id_to_contact[group_id] = contact
                state._ob_id_to_contact[user_id] = sender_name
            else:
                sender_name = entry.get("source_name", contact)
                private_segments = self._inbound_segments(entry, combined, session_handle)
                private_segments.extend(image_segments)
                event = make_message_event(
                    "private",
                    user_id,
                    private_segments,
                    nickname=sender_name,
                    message_id=message_id,
                    timestamp=entry.get("timestamp", 0),
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
            log.error(f"推送消息异常 [{contact}]: {e}")

    def _ingest(self, data):
        """一条消息进入桥接链路的唯一入口。

        收件源只负责把消息整理成统一形态，去重、过滤与推送都在这里，
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
        self.emit_message(data)
        return True

    def listen_local_db(self):
        """轮询微信本地库收件。

        收件源只负责把消息整理成统一形态，去重与推送都交给 _ingest。
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
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        log.info(f"收到图片: {source_name}" + (f" (群:{group_name})" if group_name else ""))

        segment = _image_segment(data.get("imagePath") or "")
        self._push_entry(self._entry_for(data, "[图片]", [segment] if segment else []))

    def process_emoji_message(self, data):
        """处理表情包消息：以 image 段转发图片。

        是否解读表情含义由客户端决定，桥接只负责把图片交出去。
        """
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        content = data.get("content", "[表情]")
        log.info(f"收到表情包: {source_name}" + (f" (群:{group_name})" if group_name else ""))

        try:
            segment = _image_segment(data.get("imagePath") or "")
        except Exception as e:  # noqa: BLE001
            # 图片解不出来也把原文发出去，客户端至少知道有人发过表情
            log.warning(f"表情包处理异常: {e}")
            segment = None

        self._push_entry(self._entry_for(data, content, [segment] if segment else []))
