"""
桥接核心模块：WeFlowBridge 类。

职责：
1. 连接 WeFlow SSE 推送，接收微信消息
2. 消息缓冲合并（BUFFER_SECONDS）
3. 构造 OneBot 事件，推送给 AstrBot
4. 多层消息去重（rawid、内容、自回复）
"""

import json
import logging
import os
import queue
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

import requests

import state
import config
from ob_protocol import push_event, make_message_event

try:
    from webui import monitor
except Exception:
    monitor = None

log = logging.getLogger("ob11-bridge")


# ============ 桥接核心 ============


class WeFlowBridge:
    """WeFlow ↔ AstrBot 桥接器（OneBot v11 版）。"""

    def __init__(self, sender):
        self.sender = sender
        self.processed_ids = set()
        self.start_timestamp = int(time.time())
        self.pending_buffers = {}
        self.buffer_lock = threading.Lock()
        self.chat_histories = defaultdict(list)
        self.contact_map = {}
        self._sse_session = None
        self._recent_seen = {}
        self._sent_recently = {}
        self._sse_event_keys = {}
        self._pending_image = {}  # talkerId → {"caption": None|str, "event": threading.Event()}
        self._pending_mention_images = {}  # session_id → {"data": data, "time": timestamp} 先图后文暂存
        # 会话滚动历史（含未推送的消息，供上下文附带功能使用）
        self.session_history = defaultdict(lambda: deque(maxlen=80))
        self._history_backfilled = set()  # 已从 WeFlow REST 回填过历史的会话
        self._member_cache = {}  # session_id → (缓存时间, {wxid: 显示名})

    def should_ignore(self, data):
        content = data.get("content", "")
        if data.get("sourceName", "") in config.BOT_NICKNAMES:
            return True
        if config.BOT_WXID and data.get("talkerId", "") == config.BOT_WXID:
            return True
        # 语音消息（content == "[语音]"）由 add_to_buffer 的语音分支处理，
        # 其它类型的空消息才忽略
        if not content or content.strip() == "":
            return True
        return False

    def _is_mentioned(self, data):
        """检测群消息是否 @ 了机器人。

        WeFlow SSE 推送不含 @ 结构字段，只能从 content 文本检测。
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
            log.debug(f"⚠️ content 以@开头但未匹配昵称: content={content[:40]!r} nicknames={config.BOT_NICKNAMES}")

        return False

    def add_to_buffer(self, data):
        """将消息加入缓冲区，等待合并后统一推送给 AstrBot。"""
        content = data.get("content", "")
        source_name = data.get("sourceName", "") or data.get("talkerName", "") or "未知"

        # 先判断群聊/私聊（图片/表情分支也需要用到）
        session_id_data = data.get("sessionId", "") or source_name
        group_name_raw = data.get("groupName", "")
        is_group = (data.get("sessionType", "") == "group") or bool(group_name_raw) or "@chatroom" in session_id_data

        # WebUI 监控：记录收到的消息（含图片/表情，含 mention 模式将跳过的群消息）
        if monitor is not None:
            monitor.record_inbound(
                contact=(group_name_raw or source_name) if is_group else source_name,
                sender=data.get("senderName", "") or data.get("sender", "") or data.get("sourceName", ""),
                kind="group" if is_group else "private",
                text=content,
            )

        # 会话滚动历史：所有通过过滤的消息都记录（mention 模式下未 @ 的消息
        # 不会推给麦麦，但会作为上下文附带的历史来源）
        sender_for_history = data.get("senderName", "") or data.get("sender", "") or source_name
        self.session_history[session_id_data].append({
            "name": sender_for_history,
            "text": content,
            "ts": time.time(),
        })

        # 表情学习：采集消息里的微信自带表情代码（[捂脸] 等）
        try:
            import emoji_learner
            emoji_learner.record_message(content, sender_for_history, session_id_data)
        except Exception:
            pass

        if content == "[图片]":
            # 图片消息（mention 模式下需 @ 才处理）
            if is_group and state.group_reply_mode == "mention" and not self._is_mentioned(data):
                # 先图后文：暂存图片，等后续同人发 @ 文字时合并
                self._pending_mention_images[session_id_data] = {"data": data, "time": time.time()}
                log.info(f"📸 暂存图片，等待关联 @ 文字 (session={session_id_data})")
                return
            threading.Thread(target=self.process_image_message,
                           args=(data,), daemon=True).start()
            return

        if content in ("[动画表情]", "[表情]"):
            # 表情包消息（mention 模式下需 @ 才处理）
            if is_group and state.group_reply_mode == "mention" and not self._is_mentioned(data):
                # 先图后文：暂存表情，等后续同人发 @ 文字时合并
                self._pending_mention_images[session_id_data] = {"data": data, "time": time.time()}
                log.info(f"😀 暂存表情，等待关联 @ 文字 (session={session_id_data})")
                return
            threading.Thread(target=self.process_emoji_message,
                           args=(data,), daemon=True).start()
            return

        if content == "[语音]":
            # 语音消息：先 ASR 转文字，再走 buffer（mention 模式下同样需要 @ 才处理）
            if is_group and state.group_reply_mode == "mention" and not self._is_mentioned(data):
                log.debug(f"🎤 mention 模式跳过未 @ 的语音 (session={session_id_data})")
                return
            threading.Thread(target=self.process_audio_message,
                           args=(data,), daemon=True).start()
            return

        now = time.time()
        if content and content in self._sent_recently and now - self._sent_recently[content] < 120:
            log.info(f"⏭️ 自回复去重跳过: {content[:30]}")
            return

        sender_in_group = data.get("senderName", "") or data.get("sender", "") or data.get("sourceName", "")

        if is_group:
            if state.group_reply_mode == "mention" and not self._is_mentioned(data):
                log.debug(f"⏭️ mention 模式跳过（未检测到 @）: data keys={list(data.keys())} nickname={config.BOT_NICKNAMES} content={content[:40]}")
                return
            group_raw = group_name_raw or source_name
            base_name = re.sub(r'\s*\(\d+\)\s*$', '', group_raw).strip()
            contact = base_name
            # 自动学习群名映射：若 SSE 推送了真实群名（非 @chatroom 原始 ID），存入映射供发送时用
            if group_name_raw and not group_name_raw.endswith("@chatroom"):
                state._ob_id_to_contact[session_id_data] = group_name_raw.strip()
                log.info(f"[群名学习] {session_id_data} -> {group_name_raw}")
        else:
            contact = source_name

        if is_group and state.group_reply_mode == "batch":
            buffer_key = f"__batch__{base_name}"
        elif is_group and sender_in_group:
            buffer_key = f"{session_id_data}_{sender_in_group}"
        else:
            buffer_key = session_id_data

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
                    "session_id_data": session_id_data,
                    "raw_data": data,  # 原始 SSE 数据，供 @ 检测等使用
                }
            entry = self.pending_buffers[buffer_key]
            if is_group and state.group_reply_mode == "batch" and sender_in_group:
                entry["messages"].append(f'成员"{sender_in_group}"在群"{base_name}"中对你说：{content}')
            else:
                entry["messages"].append(content)

            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["timer_version"] += 1
                version = entry["timer_version"]

                # 检查是否有暂存的图片（先图后文场景）
                has_pending_image = False
                if is_group and state.group_reply_mode == "mention":
                    cached = self._pending_mention_images.pop(session_id_data, None)
                    if cached and time.time() - cached["time"] < 15:
                        has_pending_image = True
                        log.info(f"📸 检测到关联图片，延长缓冲等待描述")
                        # 异步下载并描述图片，完成后注入 buffer
                        threading.Thread(
                            target=self._inject_cached_image,
                            args=(cached["data"].get("sessionId", session_id_data),
                                  buffer_key, version),
                            daemon=True,
                        ).start()

                buffer_delay = 15 if has_pending_image else config.BUFFER_SECONDS
                log.info(f"📩 收到来自 {contact} 的消息，等待 {buffer_delay}s 后统一推送")
                timer = threading.Timer(buffer_delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
                timer.daemon = True
                timer.start()
                entry["timer"] = timer

    # ================================================================
    # 上下文附带（mention 模式专用）
    # ================================================================

    def _build_context_block(self, session_id: str, current_msgs: list) -> str:
        """构造上下文文本块：该群最近 N 条消息（不含本次正推送的）。

        数据源：SSE 滚动历史；历史不足时从 WeFlow REST 回填一次
        （覆盖 bridge 重启前 / 麦麦不在线期间的聊天记录）。
        """
        try:
            n = int(getattr(config, "CONTEXT_MESSAGES", 0) or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0 or not session_id:
            return ""

        hist = list(self.session_history.get(session_id, ()))
        if len(hist) < n and session_id not in self._history_backfilled:
            self._backfill_history(session_id, n)
            hist = list(self.session_history.get(session_id, ()))

        current_texts = set(t.strip() for t in current_msgs if t and t.strip())
        entries = [h for h in hist if h["text"].strip() not in current_texts]
        entries = entries[-n:]
        if not entries:
            return ""

        lines = ["[最近群聊记录]"]
        for e in entries:
            lines.append(f'{e["name"]}：{e["text"]}')
        lines.append("——— 以上是该群最近的聊天记录，供你了解上下文 ———")
        log.info(f"📚 附带上文 {len(entries)} 条 (session={session_id})")
        return "\n".join(lines) + "\n"

    def _backfill_history(self, session_id: str, limit: int):
        """从 WeFlow REST 回填会话历史（每会话一次性，失败不阻塞）。"""
        self._history_backfilled.add(session_id)
        try:
            resp = requests.get(
                f"{config.WE_FLOW_BASE_URL}/api/v1/messages",
                params={
                    "access_token": config.ACCESS_TOKEN,
                    "talker": session_id,
                    "limit": max(limit * 2, 10),
                },
                timeout=8,
            )
            if resp.status_code != 200:
                log.warning(f"📚 历史回填 HTTP {resp.status_code} (session={session_id})")
                return
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("messages", data.get("data", []))
            if not isinstance(rows, list) or not rows:
                return

            names = self._resolve_names(session_id)
            type_placeholder = {3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情]", 49: "[链接/文件]"}
            existing = set(h["text"] for h in self.session_history.get(session_id, ()))

            count = 0
            rows = sorted(rows, key=lambda m: m.get("createTime", 0) or 0)
            for m in rows:
                lt = m.get("localType", 1)
                if lt == 1:
                    text = (m.get("content") or "").strip()
                else:
                    text = type_placeholder.get(lt, "")
                if not text or text in existing:
                    continue
                if m.get("isSend"):
                    name = config.SELF_NAME or "我"
                else:
                    wxid = m.get("senderUsername", "") or ""
                    name = names.get(wxid) or wxid or "未知"
                self.session_history[session_id].append({
                    "name": name, "text": text,
                    "ts": m.get("createTime", 0) or time.time(),
                })
                existing.add(text)
                count += 1
            if count:
                log.info(f"📚 已回填 {session_id} 历史 {count} 条")
        except Exception as e:
            log.warning(f"📚 历史回填失败 (session={session_id}): {e}")

    def _resolve_names(self, session_id: str) -> dict:
        """解析会话成员 wxid → 显示名（带 10 分钟缓存）。"""
        now = time.time()
        cached = self._member_cache.get(session_id)
        if cached and now - cached[0] < 600:
            return cached[1]

        names = {}
        try:
            if "@chatroom" in session_id:
                r = requests.get(
                    f"{config.WE_FLOW_BASE_URL}/api/v1/group-members",
                    params={"access_token": config.ACCESS_TOKEN, "talker": session_id},
                    timeout=8,
                )
                if r.status_code == 200:
                    for m in r.json().get("members", []):
                        wxid = m.get("wxid", "")
                        if wxid:
                            names[wxid] = m.get("displayName") or m.get("nickname") or wxid
            else:
                r = requests.get(
                    f"{config.WE_FLOW_BASE_URL}/api/v1/contacts",
                    params={"access_token": config.ACCESS_TOKEN},
                    timeout=8,
                )
                if r.status_code == 200:
                    items = r.json()
                    items = items if isinstance(items, list) else items.get("contacts", items.get("data", []))
                    for c in items:
                        u = c.get("username", "")
                        if u:
                            names[u] = c.get("displayName") or c.get("nickname") or u
        except Exception as e:
            log.warning(f"📚 解析成员昵称失败 (session={session_id}): {e}")
        self._member_cache[session_id] = (now, names)
        return names


    def process_sender(self, sender_id, version=None):
        """缓冲到期：通过 OneBot 事件推送给 AstrBot。"""
        with self.buffer_lock:
            if sender_id not in self.pending_buffers:
                return
            entry = self.pending_buffers[sender_id]
            if version is not None and entry.get("timer_version", 0) != version:
                return
            if not entry["messages"]:
                return
            msgs = entry["messages"].copy()
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
            # 构建 OneBot 事件（user_id 用微信 wxid/会话 ID 原文，跨进程稳定）
            if is_group:
                sender_wxid = entry.get("session_id_data", "") + "_" + (entry.get("sender_in_group", "") or entry.get("source_name", ""))
            else:
                sender_wxid = entry.get("session_id_data", sender_id)
            # 用原文做 user_id（wemai-adapter 与 bridge 同进程内不再 hash，保证回传一致）
            user_id = sender_wxid
            state._ob_id_to_contact[sender_wxid] = contact

            if is_group:
                group_id = entry.get("session_id_data", "")  # 群 sessionId 原文
                sender_name = entry.get("sender_in_group", "") or entry.get("source_name", "未知")

                if state.group_reply_mode == "batch":
                    # 批处理模式：消息已预格式化好，直接使用
                    formatted = combined
                else:
                    # 保留 @昵称 文本（MaiBot planner 需要看到 @ 才知道被点名）
                    # 只去掉零宽空格等不可见字符
                    clean_text = combined
                    for nick in config.BOT_NICKNAMES:
                        at_pattern = f"@{nick}"
                        if at_pattern in clean_text:
                            # 保留 @昵称，仅去掉 @ 后的零宽空格
                            clean_text = clean_text.replace(f"@{nick}\u2005", f"@{nick}")
                            clean_text = clean_text.replace(f"@{nick}\u200b", f"@{nick}")

                    formatted = clean_text
                    if sender_name:
                        formatted = f'{sender_name}在群{entry.get("group_name", contact)}中说：{clean_text}'

                    # 上下文附带：mention 模式下麦麦平时收不到群消息，
                    # @ 触发时把最近的群聊记录一并附上，让麦麦能读到上下文
                    if state.group_reply_mode == "mention":
                        ctx_block = self._build_context_block(entry.get("session_id_data", ""), msgs)
                        if ctx_block:
                            formatted = ctx_block + formatted

                # 消息段：mention 模式带 at 机器人标记，all/batch 不带
                if state.group_reply_mode == "mention":
                    msg_segments = [
                        {"type": "at", "data": {"qq": str(state._self_id_int)}},
                        {"type": "text", "data": {"text": f" {formatted}"}},
                    ]
                else:
                    msg_segments = [
                        {"type": "text", "data": {"text": formatted}},
                    ]
                event = make_message_event("group", user_id, msg_segments,
                                           group_id=group_id,
                                           group_name=entry.get("group_name", contact),
                                           nickname=sender_name)
                # 标记该群消息是否真实 @ 了机器人（wemai-adapter 据此判断 is_mentioned）
                event["is_mentioned"] = bool(self._is_mentioned(entry.get("raw_data", {})))
            else:
                sender_name = entry.get("source_name", contact)
                event = make_message_event("private", user_id,
                                           [{"type": "text", "data": {"text": combined}}],
                                           nickname=sender_name)

            # 记录 user_id → contact 映射，供 API 回复时查找
            if is_group:
                group_id = entry.get("session_id_data", "")  # 群 sessionId 原文，如 47795211464@chatroom
                state._ob_id_to_contact[group_id] = contact
            else:
                state._ob_id_to_contact[user_id] = contact

            sent = push_event(event)
            if sent > 0:
                log.info(f"✅ 已推送至 {sent} 个 AstrBot 客户端 [{contact}]")
                if monitor is not None:
                    monitor.bump("pushed")
            else:
                log.warning(f"⚠️ 无 AstrBot 客户端在线 [{contact}]")

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
                        log.info(f"🔁 {contact} 积压 {len(entry['messages'])} 条，已安排下一轮推送")

    def listen_sse(self):
        """连接 WeFlow SSE 推送。"""
        sse_url = f"{config.WE_FLOW_BASE_URL}/api/v1/push/messages?access_token={config.ACCESS_TOKEN}"
        log.info(f"连接 WeFlow 推送服务: {sse_url}")
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}

        try:
            self._sse_session = requests.get(sse_url, headers=headers, stream=True, timeout=None)
            if self._sse_session.status_code != 200:
                log.error(f"连接失败: HTTP {self._sse_session.status_code}")
                return
            log.info("✅ 已连接到 WeFlow 推送")

            for line in self._sse_session.iter_lines(decode_unicode=True):
                if not state.running:
                    break
                if not line:
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        data = json.loads(data_str)
                        msg_time = data.get("timestamp", 0)
                        if msg_time < self.start_timestamp:
                            continue
                        raw_id = data.get("rawid", "")
                        if raw_id in self.processed_ids:
                            continue
                        self.processed_ids.add(raw_id)
                        if not self.should_ignore(data):
                            if data.get("sessionType", "") == "group" or "@chatroom" in data.get("sessionId", ""):
                                content = data.get("content", "")
                                log.info(f"📩 群消息 [{data.get('sourceName','')}]: {content[:60]}")
                                if state.group_reply_mode == "mention":
                                    mentioned = any(f"@{n}" in content for n in config.BOT_NICKNAMES)
                                    log.info(f"   @={mentioned}")
                            else:
                                log.info(f"📩 收到: {data.get('sourceName','')} → {data.get('content','')[:50]}")
                            self.add_to_buffer(data)
                    except json.JSONDecodeError:
                        pass

        except requests.exceptions.ConnectionError:
            log.error("无法连接 WeFlow")
        except Exception as e:
            log.error(f"SSE 异常: {e}")
        finally:
            self._sse_session = None

    def _fetch_wechat_image(self, talker: str) -> str | None:
        """从 WeFlow REST API 获取最新图片并保存到本地"""
        try:
            url = f"{config.WE_FLOW_BASE_URL}/api/v1/messages"
            params = {
                "access_token": config.ACCESS_TOKEN,
                "talker": talker,
                "media": "true",
                "limit": 3,
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                log.error(f"WeFlow 消息API: HTTP {resp.status_code}")
                return None

            data = resp.json()
            messages = data if isinstance(data, list) else data.get("messages", data.get("data", []))
            if not isinstance(messages, list):
                messages = []

            for msg in messages:
                if msg.get("mediaType") in ("image", "sticker", "emoji") and msg.get("mediaUrl"):
                    media_url = msg["mediaUrl"]
                    sep = "&" if "?" in media_url else "?"
                    dl_url = f"{media_url}{sep}access_token={config.ACCESS_TOKEN}"

                    img_resp = requests.get(dl_url, timeout=30)
                    if img_resp.status_code != 200:
                        continue

                    # 根据 Content-Type 确定扩展名
                    ct = img_resp.headers.get("Content-Type", "")
                    ext = ".jpg"
                    if "png" in ct: ext = ".png"
                    elif "gif" in ct: ext = ".gif"
                    elif "webp" in ct: ext = ".webp"

                    filename = f"wechat_{int(time.time())}{ext}"
                    save_dir = os.path.join(config.ASTRBOT_ATTACHMENTS, "wechat_images")
                    os.makedirs(save_dir, exist_ok=True)
                    save_path = os.path.join(save_dir, filename)

                    with open(save_path, "wb") as f:
                        f.write(img_resp.content)

                    log.info(f"✅ 微信图片已保存: {save_path}")
                    return save_path

            log.warning(f"消息列表无图片 mediaUrl (talker={talker})")
            return None
        except Exception as e:
            log.error(f"获取微信图片异常: {e}")
            return None

    def process_image_message(self, data):
        """处理图片消息：从 WeFlow 取图 → ollama 描述 → 注入缓冲区"""
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        rawid = data.get("rawid", "")

        log.info(f"🖼️ 收到图片: {source_name}" +
                 (f" (群:{group_name})" if group_name else ""))

        talker_id = data.get("talkerId", "") or data.get("sessionId", "")
        is_group = bool(group_name) or "@chatroom" in session_id

        # 注册待处理的图片（ollama 完成前标记为 pending）
        img_event = threading.Event()
        self._pending_image[talker_id] = {"caption": None, "event": img_event}

        try:
            # 取图 + ollama 描述
            image_path = self._fetch_wechat_image(session_id)
            caption = None
            if image_path:
                caption = caption_image_via_ollama(image_path)

            caption_text = caption if caption else None
            if caption_text:
                log.info(f"📝 图片描述: {caption_text[:60]}...")
            else:
                log.info("⚠️ 图片描述失败")
                caption_text = "（图片内容无法描述）"

            # 注入图片描述到缓冲区
            with self.buffer_lock:
                self._pending_image[talker_id] = {"caption": caption_text, "event": img_event}

                # 批处理模式用群共享 key
                if is_group and state.group_reply_mode == "batch" and group_name:
                    g_base = re.sub(r'\s*\(\d+\)\s*$', '', group_name).strip()
                    batch_key = f"__batch__{g_base}"
                    if batch_key in self.pending_buffers:
                        entry = self.pending_buffers[batch_key]
                        entry["messages"].insert(0, f'成员"{source_name}"在群"{group_name}"中对你说：[图片: {caption_text}]')
                        entry["image_ready"] = True
                        log.info(f"📝 图片已注入批处理队列")
                        return
                    # 没有文字排队，用 batch key 创建独立条目
                    self.pending_buffers[batch_key] = {
                        "messages": [f'成员"{source_name}"在群"{group_name}"中对你说：[图片: {caption_text}]'],
                        "timer": None,
                        "timer_version": 0,
                        "processing": False,
                        "contact": group_name,
                        "is_group": True,
                        "source_name": source_name,
                        "session_id_data": session_id,
                        "group_name": group_name,
                        "sender_in_group": source_name,
                    }
                    log.info(f"📩 图片无文本跟随，创建批处理图片条目")
                    version = 1
                    timer = threading.Timer(5, lambda v=version, sid=batch_key: self.process_sender(sid, v))
                    timer.daemon = True
                    timer.start()
                    self.pending_buffers[batch_key]["timer"] = timer
                    self.pending_buffers[batch_key]["timer_version"] = version
                elif talker_id in self.pending_buffers:
                    # 已有文本在排队，注入图片上下文
                    entry = self.pending_buffers[talker_id]
                    entry["messages"].insert(0, f"[图片: {caption_text}]")
                    entry["image_ready"] = True
                    log.info(f"📝 图片已注入待处理文本队列")
                else:
                    # 没有文本排队，创建单条图片消息处理
                    log.info(f"📩 图片无文本跟随，直接处理")
                    self.pending_buffers[talker_id] = {
                        "messages": [f"[图片: {caption_text}]"],
                        "timer": None,
                        "timer_version": 0,
                        "processing": False,
                        "contact": group_name if is_group and group_name else source_name,
                        "is_group": is_group,
                        "source_name": source_name,
                        "session_id_data": session_id,
                        "group_name": group_name if is_group else "",
                        "sender_in_group": "",
                    }
                    version = 1
                    timer = threading.Timer(2, lambda v=version, sid=talker_id: self.process_sender(sid, v))
                    timer.daemon = True
                    timer.start()
                    self.pending_buffers[talker_id]["timer"] = timer
                    self.pending_buffers[talker_id]["timer_version"] = version
        finally:
            # 确保 Event 被设置
            img_event.set()

    # ---------------------------------------------------------------- #
    # 语音消息：下载 wav → ASR → 走 buffer 推送
    # ---------------------------------------------------------------- #

    def _fetch_wechat_audio(self, talker: str) -> str | None:
        """从 WeFlow REST API 拉取最新语音并保存到本地。

        WeFlow 在 ``media=1`` 时会把微信语音自动转码成 wav 放到 ``/api/v1/media/.../voices/voice_xxx.wav``，
        不需要我们做 silk 解码。
        """
        try:
            url = f"{config.WE_FLOW_BASE_URL}/api/v1/messages"
            params = {
                "access_token": config.ACCESS_TOKEN,
                "talker": talker,
                "media": "true",
                "voice": "1",
                "limit": 5,
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                log.warning(f"⚠️ WeFlow 消息API HTTP {resp.status_code}")
                return None

            data = resp.json()
            messages = data if isinstance(data, list) else data.get("messages", data.get("data", []))
            if not isinstance(messages, list):
                messages = []

            for msg in messages:
                # mediaType in (voice, audio)，localType=34 是微信语音消息
                mt = (msg.get("mediaType") or "").lower()
                lt = msg.get("localType") or msg.get("msgType") or 0
                is_voice = mt in ("voice", "audio") or int(lt) == 34
                if not is_voice:
                    continue
                media_url = msg.get("mediaUrl")
                if not media_url:
                    continue
                sep = "&" if "?" in media_url else "?"
                dl_url = f"{media_url}{sep}access_token={config.ACCESS_TOKEN}"

                audio_resp = requests.get(dl_url, timeout=30)
                if audio_resp.status_code != 200:
                    log.warning(f"⚠️ 语音下载 HTTP {audio_resp.status_code}")
                    continue

                ct = audio_resp.headers.get("Content-Type", "")
                ext = ".wav"
                if "mpeg" in ct or "mp3" in ct:
                    ext = ".mp3"
                elif "ogg" in ct:
                    ext = ".ogg"

                filename = f"wechat_audio_{int(time.time())}{ext}"
                save_dir = os.path.join(config.ASTRBOT_ATTACHMENTS, "wechat_audios")
                os.makedirs(save_dir, exist_ok=True)
                save_path = os.path.join(save_dir, filename)
                with open(save_path, "wb") as f:
                    f.write(audio_resp.content)
                log.info(f"🎤 微信语音已保存: {save_path} ({len(audio_resp.content)} bytes)")
                return save_path

            log.warning(f"消息列表无语音 mediaUrl (talker={talker})")
            return None
        except Exception as e:
            log.error(f"获取微信语音异常: {e}")
            return None

    def process_audio_message(self, data):
        """处理语音消息：拉取 wav → FunASR 转写 → 注入 buffer 走文字推送。"""
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or data.get("talkerName", "") or "未知"
        group_name = data.get("groupName", "")
        is_group = bool(group_name) or "@chatroom" in session_id
        sender_in_group = data.get("senderName", "") or data.get("sourceName", "")

        log.info(
            f"🎤 收到语音: {source_name}" +
            (f" (群:{group_name})" if group_name else "")
        )

        audio_path = self._fetch_wechat_audio(session_id)
        if not audio_path:
            log.warning("🎤 无可用语音文件，跳过推送")
            return

        text = transcribe_audio_via_fun_asr(audio_path)
        if not text:
            log.warning("🎤 语音转写失败，跳过推送")
            return

        log.info(f"🎤 语音转写: {text[:80]}...")

        # 推给麦麦（沿用 add_to_buffer 流程，但 content 用转写结果）
        # 用 _dispatch_message_to_buffer 复用 buffer 创建/调度逻辑
        new_data = dict(data)
        new_data["content"] = f"🎤 语音消息：{text}"
        new_data["senderName"] = sender_in_group
        self._dispatch_message_to_buffer(new_data)

    def _dispatch_message_to_buffer(self, data):
        """通用 buffer 注入：转写后的语音消息复走文字流程。"""
        # 直接调用 add_to_buffer 即可；它现在会跳过语音分支（content 不再是 "[语音]"）
        self.add_to_buffer(data)

    def process_emoji_message(self, data):
        """处理表情包消息：下载图片、收纳到 MaiBot 表情库、描述后转发"""
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        content = data.get("content", "[表情]")

        log.info(f"😀 收到表情包: {source_name}" +
                 (f" (群:{group_name})" if group_name else ""))

        talker_id = data.get("talkerId", "") or data.get("sessionId", "")
        is_group = bool(group_name) or "@chatroom" in session_id

        # 尝试下载图片描述
        try:
            image_path = self._fetch_wechat_image(session_id)
            if image_path:
                # 收纳到 MaiBot 表情库（复制到 data/emoji/，定时维护会自动注册）
                self._save_emoji_to_maibot(image_path)
                caption = caption_image_via_ollama(image_path)
                if caption:
                    content = f"[表情: {caption}]"
                    log.info(f"😀 表情包已描述: {caption[:60]}...")
                else:
                    log.info("😀 表情包图片描述失败，保留原文")
            else:
                log.info("😀 表情包无可用图片，保留原文")

            # 直接注入缓冲区（不等待，立即推送）
            self.add_text_to_buffer(talker_id, source_name, group_name,
                                    session_id, content, is_group, talker_id)
        except Exception as e:
            log.warning(f"😀 表情包处理异常: {e}")
            # 异常时也保底发送原文
            self.add_text_to_buffer(talker_id, source_name, group_name,
                                    session_id, content, is_group, talker_id)

    def _save_emoji_to_maibot(self, image_path: str) -> bool:
        """复制微信表情图到 MaiBot 的 data/emoji/ 目录，供其定时维护自动注册收纳。

        文件名用 sha256 哈希（MaiBot 的 register_emoji_by_filename 会据此去重）。
        """
        try:
            import hashlib
            import shutil
            if not image_path or not os.path.isfile(image_path):
                return False
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            if not img_bytes:
                return False
            hash_hex = hashlib.sha256(img_bytes).hexdigest()
            ext = os.path.splitext(image_path)[1].lower() or ".jpg"
            emoji_dir = os.path.join(config.MAIBOT_EMOJI_DIR, "")
            os.makedirs(emoji_dir, exist_ok=True)
            target = os.path.join(emoji_dir, f"{hash_hex}{ext}")
            if os.path.exists(target):
                log.debug(f"😀 表情已存在，跳过: {os.path.basename(target)}")
                return True
            with open(target, "wb") as f:
                f.write(img_bytes)
            log.info(f"😀 已收纳表情到 MaiBot 表情库: {os.path.basename(target)}")
            return True
        except Exception as e:
            log.warning(f"😀 收纳表情到 MaiBot 失败: {e}")
            return False

    def _inject_cached_image(self, session_id, buffer_key, version):
        """下载缓存图片 → 描述 → 注入到 buffer 条目（在缓冲计时器到期前完成）"""
        try:
            img_path = self._fetch_wechat_image(session_id)
            caption = None
            if img_path:
                caption = caption_image_via_ollama(img_path)
            text = f"[图片: {caption or '无法描述'}]"

            with self.buffer_lock:
                if buffer_key in self.pending_buffers:
                    entry = self.pending_buffers[buffer_key]
                    # 版本匹配才注入（版本变了说明被新消息重置过）
                    if entry.get("timer_version") == version:
                        entry["messages"].insert(0, text)
                        log.info(f"📸 缓存图片已注入: {text[:60]}")
                    else:
                        log.info(f"📸 缓存图片跳过（buffer 版本已变更）")
        except Exception as e:
            log.warning(f"📸 缓存图片处理异常: {e}")

    def add_text_to_buffer(self, session_id_data, source_name, group_name,
                           session_id, content, is_group, sender_key):
        """通用：将一段文本直接加入缓冲队列（供表情/图片等异步处理完后调用）"""
        with self.buffer_lock:
            buffer_key = sender_key
            if buffer_key not in self.pending_buffers:
                self.pending_buffers[buffer_key] = {
                    "messages": [],
                    "timer": None,
                    "timer_version": 0,
                    "processing": False,
                    "contact": group_name if is_group and group_name else source_name,
                    "is_group": is_group,
                    "source_name": source_name,
                    "session_id_data": session_id,
                    "group_name": group_name if is_group else "",
                    "sender_in_group": source_name if is_group else "",
                }
            entry = self.pending_buffers[buffer_key]
            entry["messages"].append(content)

            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["timer_version"] += 1
                version = entry["timer_version"]
                delay = 2  # 表情/图片单独推送，短缓冲
                timer = threading.Timer(delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
                timer.daemon = True
                timer.start()
                entry["timer"] = timer
                entry["timer_version"] = version
def caption_image_via_ollama(image_path: str) -> str | None:
    """对图片进行文字描述，支持 ollama 和 OpenAI 兼容 API 两种后端。"""
    try:
        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        if state.image_caption_provider == "openai":
            if not state.image_caption_api_base:
                log.warning("⚠️ provider=openai 但未配置 api_base，跳过图片描述")
                return None
            # OpenAI 兼容 API
            resp = requests.post(
                f"{state.image_caption_api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {state.image_caption_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": state.image_caption_model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": state.image_caption_prompt},
                            {"type": "image_url", "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}"
                            }},
                        ],
                    }],
                    # 推理模型（如 deepseek-v4-flash-vision-exp）的推理过程
                    # 会占用输出 token 预算，给太小会导致正文为空、静默失败
                    "max_tokens": 3000,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                choice = resp.json()["choices"][0]
                caption = (choice.get("message", {}).get("content") or "").strip()
                if caption:
                    log.info(f"🖼️ 图片描述: {caption[:80]}...")
                    return caption
                # 200 但正文为空：通常是推理预算被思考烧光（finish_reason=length）
                log.warning(
                    f"⚠️ 图片描述返回空内容 (finish_reason={choice.get('finish_reason')}, "
                    f"usage={resp.json().get('usage', {})})"
                )
            else:
                log.warning(f"OpenAI API 返回 HTTP {resp.status_code}: {resp.text[:200]}")
        else:
            # ollama 原生 API
            resp = requests.post(
                f"{state.ollama_base_url}/api/generate",
                json={
                    "model": state.image_caption_model,
                    "prompt": state.image_caption_prompt,
                    "images": [img_b64],
                    "stream": False,
                },
                timeout=state.ollama_timeout,
            )
            if resp.status_code == 200:
                caption = resp.json().get("response", "").strip()
                if caption:
                    log.info(f"🖼️ 图片描述: {caption[:80]}...")
                    return caption
            else:
                log.warning(f"ollama 返回 HTTP {resp.status_code}: {resp.text[:100]}")

    except requests.Timeout:
        log.warning(f"图片描述超时 (30s)")
    except Exception as e:
        log.warning(f"图片描述失败: {e}")
    return None


def transcribe_audio_via_fun_asr(audio_path: str) -> str | None:
    """通过阿里云 DashScope Fun-ASR 把 wav/mp3 转成中文文本。

    要求 audio 在 WeFlow 导出时已经是 16k 16bit 单声道 wav 或 mp3（WeFlow 默认输出 wav）。
    DashScope 同步接口要求 base64 ≤ 10MB，60 秒以内。
    """
    try:
        import base64
        # 用后缀猜 mime，默认 wav
        ext = os.path.splitext(audio_path)[1].lower()
        if ext in (".mp3", ".mpeg"):
            mime = "audio/mpeg"
            fmt = "mp3"
        elif ext in (".ogg", ".opus"):
            mime = "audio/ogg"
            fmt = "ogg"
        else:
            mime = "audio/wav"
            fmt = "wav"

        with open(audio_path, "rb") as f:
            data = f.read()
        size_mb = len(data) / 1024 / 1024
        if size_mb > 10:
            log.warning(f"🎤 音频过大 ({size_mb:.1f}MB)，超过 DashScope 同步接口限制")
            return None
        data_uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"

        workspace_id = state.fun_asr_workspace_id
        api_key = state.fun_asr_api_key
        region = (state.fun_asr_region or "cn").lower()
        if region in ("intl", "sg", "ap-southeast"):
            host = f"https://{workspace_id}.ap-southeast-1.maas.aliyuncs.com"
        else:
            host = f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com"

        if not workspace_id or not api_key:
            log.warning("🎤 FunASR 未配置（fun_asr_workspace_id / fun_asr_api_key），跳过转写")
            return None

        url = f"{host}/api/v1/services/aigc/multimodal-generation/generation"
        payload = {
            "model": state.fun_asr_model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_audio", "input_audio": {"data": data_uri}}
                        ],
                    }
                ]
            },
            "parameters": {"format": fmt, "sample_rate": 16000},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "disable",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            log.warning(f"🎤 FunASR HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        body = resp.json()
        # DashScope Fun-ASR 同步接口的真实响应结构：
        # 顶层有 sentence / text / request_id / output / usage，
        # text 也在 body["sentence"]["text"] 或 body["output"]["sentence"]["text"]。
        text = ""
        sentence = body.get("sentence") or body.get("output", {}).get("sentence") or {}
        if isinstance(sentence, dict):
            text = sentence.get("text", "")
        if not text:
            text = body.get("text", "") or body.get("output", {}).get("text", "")
        text = (text or "").strip()
        if not text:
            log.warning(f"🎤 FunASR 返回空文本: {json.dumps(body)[:300]}")
            return None
        return text
    except requests.Timeout:
        log.warning("🎤 FunASR 转写超时 (60s)")
    except Exception as e:
        log.warning(f"🎤 FunASR 转写失败: {e}")
    return None
