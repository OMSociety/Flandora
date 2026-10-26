"""
桥接核心模块：Flandora 类。

职责：
1. 从微信本地库收件（WxLocalSource 轮询，见 wx_source）
2. 把收件字典整理成领域消息，交给 ob_protocol 渲染成 OneBot 事件
3. 多层消息去重（rawid、内容、自回复）

一条收件消息对应一个事件：合并、排队与回复策略都属于客户端，桥接不替它决定。
"""

import logging
import re
import threading
import time
from collections import defaultdict

from . import config, ob_ids, ob_inbound, wx_crypto, wx_source
from .domain.content import IMAGE_PLACEHOLDER, ContentKind, classify
from .domain.message import (
    ChatKind,
    ChatRef,
    Image,
    InboundMessage,
    Mention,
    Message,
    QuoteRef,
    Segment,
    SenderRef,
)
from .ob_protocol import (
    make_poke_event,
    make_recall_event,
    push_event,
    render_message_event,
)
from .runtime import LIFECYCLE, RUNTIME, events

log = logging.getLogger("flandora")

# 微信给群名挂的 "(3)" 人数后缀：搜索会话时用不上，推事件前剥掉
_GROUP_COUNT_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")


def _quote_ref(quote) -> QuoteRef | None:
    """引用信息 → 领域对象；没有服务端号就没有可还原的引用。"""
    if not isinstance(quote, dict):
        return None
    server_id = str(quote.get("server_id") or "").strip()
    if not server_id:
        return None
    return QuoteRef(
        server_id=server_id,
        sender=str(quote.get("sender") or ""),
        name=str(quote.get("name") or ""),
    )


# ============ 桥接核心 ============


class Flandora:
    """微信 ↔ OneBot v11 桥接器。"""

    def __init__(self, sender):
        self.sender = sender
        self.processed_ids = set()
        self.start_timestamp = int(time.time())
        self.chat_histories = defaultdict(list)
        self.contact_map = {}
        self._recent_seen = {}
        self._member_cache = {}  # session_id → (缓存时间, {wxid: 显示名})
        self._nick_store = None  # 自己昵称的缓存宿主（store 换掉就重读）
        self._nick = ""

    def self_nickname(self) -> str:
        """机器人自己的微信昵称（本地通讯录里自己那行），读不到返回空串。

        @ 识别与防自回环都以这个昵称为准，它随登录账号变化，所以不落配置。
        按当前收件源的 store 缓存；读不到时不记缓存，下次调用再试。
        """
        store = RUNTIME.store
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

    def should_ignore(self, data):
        content = data.get("content", "")
        nick = self.self_nickname()
        if nick and data.get("sourceName", "") == nick:
            return True
        # 防自回环第二道：按运行时服务的登录账号精确匹配（wxid 不随改名漂移）
        store = RUNTIME.store
        my_wxid = store.my_wxid if store is not None else ""
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

    def emit_message(self, data: dict):
        """处理一条收件消息，立即组装成 OneBot 事件推送。

        合并多条、按时间窗排队、决定要不要回，都是客户端的活；桥接收到一条就
        报一条，不做延迟与拼接。
        """
        content = data.get("content", "")
        source_name = data.get("sourceName", "") or data.get("talkerName", "") or "未知"
        group_name_raw = data.get("groupName", "")

        message = self._entry_for(data)

        # 面板监控：记录收到的消息（含图片/表情），事件总线在核心侧（runtime.events）
        events.record_inbound(
            contact=(group_name_raw or source_name) if message.chat.is_group else source_name,
            sender=data.get("senderName", "")
            or data.get("sender", "")
            or data.get("sourceName", ""),
            kind="group" if message.chat.is_group else "private",
            text=content,
        )

        # 图片与表情包都要先把本地文件解出来，交给独立线程做，避免挡住轮询
        if message.kind is ContentKind.IMAGE:
            threading.Thread(
                target=self.process_image_message, args=(message,), daemon=True
            ).start()
            return
        if message.kind is ContentKind.EMOJI:
            threading.Thread(
                target=self.process_emoji_message, args=(message,), daemon=True
            ).start()
            return

        # 撤回与拍一拍不是聊天正文：转成 notice 事件，别混进 message 流
        notice = self._notice_for(message)
        if notice is not None:
            push_event(notice)
            log.info(
                f"[OB11] 通知事件已推送：{notice.get('notice_type')}"
                f"{'/' + notice['sub_type'] if notice.get('sub_type') else ''}"
            )
            return

        if message.chat.is_group:
            # 自动学习群名映射：若推送了真实群名（非 @chatroom 原始 ID），存入映射供发送时用。
            # 名字没变（同一个群继续来消息）就不重复记、不重复打日志，否则会把日志刷满；
            # 群里改名时名字变了，仍会留下 [群名学习] 这一行。
            if group_name_raw and not group_name_raw.endswith("@chatroom"):
                group_ob_id = ob_ids.contact_id(message.chat.handle)
                if ob_ids.lookup_name(group_ob_id) != group_name_raw.strip():
                    ob_ids.remember_name(group_ob_id, group_name_raw.strip())
                    log.info(f"[群名学习] {message.chat.handle} -> {group_name_raw}")

        self._push_entry(message)

    def _notice_for(self, message: InboundMessage):
        """把撤回与拍一拍从消息流里摘出来，转成 OneBot 通知事件。

        这两类在本地库里是独立行（`local_type=10000` 的 sysmsg、appmsg 子类型 62），
        照常当正文推上去会让客户端收到「[撤回了一条消息]」「[拍一拍] …」这种假消息，
        也没法据此更新会话状态。
        """
        revoke = message.raw.get("revoke") or {}
        pat = message.raw.get("pat") or {}
        if not revoke and not pat:
            return None
        group_id = ob_ids.contact_id(message.chat.handle) if message.chat.is_group else 0

        if revoke:
            nickname = str(revoke.get("nickname") or message.raw.get("senderName") or "").strip()
            wxid = self._wxid_for_name(message.chat.handle, nickname)
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
        hits = [wxid for wxid, shown in self.resolve_names(session_handle).items() if shown == name]
        return hits[0] if len(hits) == 1 else ""

    def resolve_names(self, session_id: str) -> dict[str, str]:
        """解析会话成员 wxid → 显示名（带 10 分钟缓存）。

        名字统一取自本地库的 contact 表（备注名优先）。群里发言的人只要在联系人
        表里就能查到，查不到就退回 wxid。get_group_msg_history 用它还原历史发言人。
        """
        now = time.time()
        cached = self._member_cache.get(session_id)
        if cached and now - cached[0] < 600:
            return cached[1]

        names: dict[str, str] = {}
        store = RUNTIME.store
        if store is not None:
            try:
                names = dict(store.display_names())
            except Exception as e:  # noqa: BLE001
                log.warning(f"读取联系人失败 (session={session_id}): {e}")
        self._member_cache[session_id] = (now, names)
        return names

    def _inbound_segments(self, message: InboundMessage, text: str) -> list[Segment]:
        """把一条收到的消息还原成领域段。

        引用在前（客户端据此拿到被引消息的 message_id），正文里的真 @ 拆成 at 段，
        其余保持文本。还原不出来的一律留在原文里，不改写内容。

        被引的是机器人自己发的消息时，最前面再补一个 at 自己的段：客户端把「被 @」
        当作唤醒条件（AstrBot 的唤醒检查就在其列）。机器人自己发出的消息现在也进事件
        存档，按被引消息的号取得到原文（见 :meth:`_bind_outgoing_server`），这个 at 段
        是第二道保险：客户端版本旧或存档被清过时，仍能按「被 @」认出这条消息冲机器人来。
        正文里已经 @ 机器人时不补。
        """
        segments: list[Segment] = []
        reply = ob_inbound.reply_segment(message.quote, ob_ids.message_ids().message_by_server)
        if reply is not None:
            segments.append(reply)
        names = self.resolve_names(message.chat.handle)
        mentions = ob_inbound.split_mentions(
            text, lambda name: ob_inbound.resolve_from_names(names, name)
        )
        quote = message.quote
        quoted_self = (
            quote is not None and bool(quote.sender) and quote.sender == ob_ids.SELF.wxid()
        )
        mentioned_self = any(
            isinstance(seg, Mention) and seg.user_id == ob_ids.SELF.user_id() for seg in mentions
        )
        if quoted_self and not mentioned_self:
            segments.insert(0, Mention(user_id=ob_ids.SELF.user_id(), name=ob_ids.SELF.nickname()))
        segments.extend(mentions)
        return segments

    @staticmethod
    def _bind_outgoing_server(talker: str, server_id: str) -> None:
        """把自己发出的一条消息绑到服务端号上。

        客户端引用机器人自己的消息时，`<refermsg><svrid>` 指的就是这个号；绑上之后
        `get_msg` 回得出原文，客户端「引用了机器人」的唤醒也就自然成立。
        """
        bound = ob_ids.bind_pending_outgoing(str(talker), str(server_id))
        if bound is not None:
            log.debug(f"[OB11] 出站消息 {bound} 绑上服务端号 {server_id}")

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

    def _entry_for(self, data: dict) -> InboundMessage:
        """把一条收件消息整理成领域消息。

        每条消息各一份，不做跨消息合并；群名去掉微信附加的 "(3)" 人数后缀。
        正文里的占位符分不出图片与表情时，以收件源给出的 ``imagePath`` 为准。
        """
        source_name = str(data.get("sourceName", "") or data.get("talkerName", "") or "未知")
        session_handle = str(data.get("sessionId", "") or source_name)
        group_name_raw = str(data.get("groupName", ""))
        is_group = (
            (data.get("sessionType", "") == "group")
            or bool(group_name_raw)
            or "@chatroom" in session_handle
        )
        sender_in_group = str(
            data.get("senderName", "") or data.get("sender", "") or data.get("sourceName", "")
        )
        if is_group:
            contact = _GROUP_COUNT_SUFFIX.sub("", group_name_raw or source_name).strip()
            chat = ChatRef(handle=session_handle, name=contact, kind=ChatKind.GROUP)
            # 发送者 wxid 是 ID 层的唯一依据：群消息里它才是「这个人」，
            # 昵称会改、同群可能重名，都不能当身份
            sender = SenderRef(
                handle=str(data.get("sender", "") or sender_in_group),
                nickname=sender_in_group or source_name,
            )
        else:
            chat = ChatRef(handle=session_handle, name=source_name, kind=ChatKind.PRIVATE)
            sender = SenderRef(handle=str(data.get("sender", "") or ""), nickname=source_name)

        content = str(data.get("content", ""))
        kind, media = classify(content)
        if kind in (ContentKind.IMAGE, ContentKind.EMOJI):
            # 收件源解出的文件路径优先；正文尾巴里的路径只作兜底
            path = str(data.get("imagePath") or "") or (media[0] if media else "")
            media = (path,) if path else ()
        if data.get("revoke"):
            kind = ContentKind.RECALL
        elif data.get("pat"):
            kind = ContentKind.POKE
        return InboundMessage(
            chat=chat,
            sender=sender,
            text=IMAGE_PLACEHOLDER if kind is ContentKind.IMAGE else content,
            kind=kind,
            media=tuple(media),
            quote=_quote_ref(data.get("quote")),
            local_id=int(data.get("localId") or 0),
            server_id=str(data.get("serverId") or ""),
            timestamp=int(data.get("timestamp") or 0),
            raw=data,  # 原始数据，供 @ 检测等必须看原文的地方使用
        )

    def _push_entry(self, message: InboundMessage):
        """把一条领域消息渲染成 OneBot 事件推给已连接的客户端。"""
        contact = message.chat.name
        is_group = message.chat.is_group
        log.info(f"推送消息 [{'群' if is_group else '私'}|{contact}]")

        try:
            # OneBot 的 ID 全是整数：会话与发言人先经 ID 层换成稳定整数，
            # 原文字符串只在映射表里留底（发送时按它搜索微信会话）。
            session_handle = message.chat.handle or str(contact)
            sender_handle = message.sender.handle or session_handle
            user_id = ob_ids.contact_id(sender_handle)
            message_id = self._message_id_for(session_handle, message.local_id, message.server_id)

            # 正文保持原样：发言人、群名由事件的 sender/group 字段承载，是否 @ 了
            # 机器人由 is_mentioned 标记。提及的标记字符由 ob_inbound 在认出提及之后
            # 清掉——只有它知道哪一处 @ 是真提及，先清就再也认不出来了。
            segments = self._inbound_segments(message, message.text)
            segments.extend(Image(path=path) for path in message.media)

            event = render_message_event(
                Message(
                    chat=message.chat,
                    sender=message.sender,
                    segments=tuple(segments),
                    message_id=message_id,
                    timestamp=message.timestamp,
                    mentioned=is_group and self._is_mentioned(message.raw),
                ),
                config.OB_MESSAGE_FORMAT,
            )
            if is_group:
                ob_ids.remember_name(ob_ids.contact_id(session_handle), contact)
                ob_ids.remember_name(user_id, message.sender.nickname)
            else:
                ob_ids.remember_name(user_id, contact)

            sent = push_event(event)
            if sent > 0:
                log.info(f"已推送至 {sent} 个 OneBot 客户端 [{contact}]")
                events.bump("pushed")
            else:
                # 没有客户端在线等于这条消息没有出口，如实记进丢弃计数（不重试）
                log.warning(f"无 OneBot 客户端在线 [{contact}]，本条未推送")
                events.bump("dropped")

        except Exception as e:
            log.error(f"推送消息异常 [{contact}]，本条已丢弃（不会重试）: {e}")
            events.bump("dropped")

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
        收件源就绪时把本地库视图挂到运行时、停止时清空：昵称、名册与历史
        查询都只认这一份，不会读到上一次运行留下的 store。
        """
        try:
            source = wx_source.build_source(on_outgoing=self._bind_outgoing_server)
        except Exception as e:  # noqa: BLE001
            log.error(f"本地库收件源启动失败：{e}")
            return

        RUNTIME.store = source.store
        source.prime()
        try:
            while LIFECYCLE.should_continue():
                try:
                    for data in source.poll():
                        if not LIFECYCLE.should_continue():
                            break
                        self._ingest(data)
                except wx_crypto.WrongKey:
                    log.error("微信口令与当前登录账号对不上（切换过账号？），本地库收件停止")
                    return
                except Exception as e:  # noqa: BLE001
                    log.warning(f"轮询本地库出错：{e}")
                time.sleep(source.poll_seconds)
        finally:
            RUNTIME.store = None
            log.info("本地库收件已停止")

    def process_image_message(self, message: InboundMessage):
        """处理图片消息：把收件侧已解出的图片按 OneBot image 段交给客户端。

        桥接不解读图片内容：图片原样下发，是否描述、如何描述由客户端决定。
        """
        source_name = str(message.raw.get("sourceName") or "未知")
        group_name = str(message.raw.get("groupName") or "")
        log.info(f"收到图片: {source_name}" + (f" (群:{group_name})" if group_name else ""))

        self._push_entry(message)

    def process_emoji_message(self, message: InboundMessage):
        """处理表情包消息：以 image 段转发图片。

        是否解读表情含义由客户端决定，桥接只负责把图片交出去。
        """
        source_name = str(message.raw.get("sourceName") or "未知")
        group_name = str(message.raw.get("groupName") or "")
        log.info(f"收到表情包: {source_name}" + (f" (群:{group_name})" if group_name else ""))

        self._push_entry(message)
