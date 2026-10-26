"""微信本地库的只读访问层。

口令由取钥链路提供（见 wx_key），本模块负责：定位当前登录账号的数据目录、
把要用的库解到自己的工作目录、按会话增量取新消息。

对微信原始目录只读；所有写操作都落在 bridge 自己的工作目录里。

实测结构（微信 4.1.15.56；账号目录形如 `<wxid>_<4 位后缀>`）：

    message/message_0.db
        消息分片表 `Msg_<md5(会话用户名)>`，一张表一个会话。实测三组对得上：
            filehelper              -> Msg_9e20f478899dc29eb19741386f9343c8
            58482702152@chatroom    -> Msg_21e2b0091b003e8914cd3c99cd986b06
            50071592949@chatroom    -> Msg_640ac2f5c4f4eccd50607cdd409a4b48
        `real_sender_id` 指向同库 Name2Id 表，取出的是发送者 wxid；
        自己发的消息其 real_sender_id 就是本账号 wxid。
        `message_content` 按 WCDB_CT_message_content 标注的方式压缩（4 = zstd，
        魔数 28 b5 2f fd），解压后形如 `<发送者 wxid>:<正文>`，正文形态由
        local_type 决定。

    session/session.db 的 SessionTable：未读数、摘要、最后一条的类型与发送者。
    contact/contact.db：备注名、昵称。
"""

import glob
import hashlib
import logging
import os
import re
import sqlite3
import time

from . import wechat_account, wx_crypto, wx_image
from .domain.content import EMOJI_PLACEHOLDER, IMAGE_PLACEHOLDER

log = logging.getLogger("flandora")

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# 微信通讯录里的伪联系人：不是真人，也不是群，作为好友上报只会让客户端
# 把消息推给「服务通知」「微信团队」这类系统号。
SYSTEM_USERNAMES = frozenset(
    {
        "filehelper",
        "notifymessage",
        "weixin",
        "fmessage",
        "medianote",
        "mphelper",
        "qmessage",
        "qqmail",
        "newsapp",
        "brandsessionholder",
        "officialaccounts",
        "floatbottle",
        "shakeapp",
        "lbsapp",
        "voipnotify",
        "qqsync",
    }
)

# 解密哪些库。顺序无关，只是把「要用的库」集中在一处便于核对。
DBS = (
    ("session", "session.db"),
    ("message", "message_0.db"),
    ("contact", "contact.db"),
    ("message", "message_resource.db"),
    ("hardlink", "hardlink.db"),
)

# local_type 的低 32 位是消息大类，高 32 位是 49（appmsg）的子类型
BASE_IMAGE = 3
BASE_VOICE = 34
BASE_CARD = 42
BASE_VIDEO = 43
BASE_EMOJI = 47
BASE_LOCATION = 48
BASE_APPMSG = 49
BASE_VOIP = 50
BASE_SYSTEM = 10000
BASE_REVOKE = 10002

# appmsg 子类型
APP_FILE = 6
APP_LINK = 5
APP_MERGED = 19
APP_QUOTE = 57
APP_PAT = 62

_KIND_TEXT = "text"
_TALKER_RE = re.compile(r"^[A-Za-z0-9_@.\-]{3,64}:\s?", re.UNICODE)


class WxStoreError(Exception):
    """本地库不可用（账号未登录、口令不对、库结构不认识）。"""


def _decompress(raw):
    if raw[:4] != ZSTD_MAGIC:
        return raw
    try:
        import zstandard
    except ImportError as e:
        raise WxStoreError(
            "消息内容是 zstd 压缩的，需要 zstandard 依赖（pip install zstandard）"
        ) from e
    try:
        return zstandard.ZstdDecompressor().decompress(raw)
    except Exception as e:  # noqa: BLE001
        log.debug("zstd 解压失败（%d 字节）: %s", len(raw), e)
        return None


def _text_of(raw):
    """把 message_content 还原成文本：解压 → UTF-8 → 去掉开头的发送者前缀。"""
    if raw is None:
        return None
    data = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8", "replace")
    data = _decompress(bytes(data))
    if data is None:
        return None
    text = data.decode("utf-8", "replace")
    return _TALKER_RE.sub("", text, count=1)


def _xml_value(text, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1) if m else ""


_REFMSG_RE = re.compile(r"<refermsg>(.*?)</refermsg>", re.S)


def quote_ref(body):
    """解出引用消息里被引的那一条。

    返回 `{"server_id", "sender", "name"}`；不是引用消息返回 None。
    `server_id` 就是被引消息的服务端号，能拿它回查 message_id 还原 `reply` 段。

    `sender` 是被引消息的发送者：它可能在 `<fromusr>`，也可能只在 `<chatusr>`（群里
    `<chatusr>` 还可能是群标识），两个都认、先看 `<fromusr>`。调用方用它判断「被引的
    是不是机器人自己发的消息」，认不出只是少补一个 at 段，不影响正文。
    """
    if not body:
        return None
    text = body.decode("utf-8", "ignore") if isinstance(body, (bytes, bytearray)) else str(body)
    m = _REFMSG_RE.search(text)
    if not m:
        return None
    inner = m.group(1)
    server_id = _xml_value(inner, "svrid").strip()
    if not server_id:
        return None
    sender = _xml_value(inner, "fromusr").strip() or _xml_value(inner, "chatusr").strip()
    return {
        "server_id": server_id,
        "sender": sender,
        "name": _xml_value(inner, "displayname").strip(),
    }


_SYSMSG_TYPE_RE = re.compile(r'<sysmsg[^>]*\btype="([^"]+)"')
_REVOKE_TEXT_RE = re.compile(r'^"?(?P<nick>.+?)"?\s*撤回了一条消息\s*$')
_PATINFO_RE = re.compile(r"<patinfo>(.*?)</patinfo>", re.S)


def sysmsg_type(body) -> str:
    """系统消息（local_type=10000）的 sysmsg 类型，取不到返回空串。"""
    if not body:
        return ""
    text = body.decode("utf-8", "ignore") if isinstance(body, (bytes, bytearray)) else str(body)
    m = _SYSMSG_TYPE_RE.search(text)
    return (m.group(1) or "").strip() if m else ""


def revoke_info(body) -> dict | None:
    """撤回提示（sysmsg type="revokemsg"）。

    PC 端的撤回提示只有一句 `"昵称" 撤回了一条消息`，**不带被撤回消息的服务端号**
    （实测本机 6 条样本里没有 newmsgid / msgid 字段），因此调用方拿不到
    message_id，只能如实给 0，不能猜一条。
    """
    if sysmsg_type(body) != "revokemsg":
        return None
    raw = body.decode("utf-8", "ignore") if isinstance(body, (bytes, bytearray)) else str(body)
    # 客户端版本不同，这句提示可能落在 <content> 或 <replacemsg> 里
    content = (_xml_value(raw, "content") or _xml_value(raw, "replacemsg")).strip()
    # CDATA 包裹要剥掉，否则昵称会带上 <![CDATA[ 与 ]]>
    if content.startswith("<![CDATA[") and content.endswith("]]>"):
        content = content[len("<![CDATA[") : -3].strip()
    m = _REVOKE_TEXT_RE.match(content)
    nick = (m.group("nick") or "").strip() if m else ""
    return {"nickname": nick}


def pat_info(body) -> dict | None:
    """拍一拍（appmsg 子类型 62）的结构化字段。

    `<patinfo>` 里 fromusername / pattedusername 是 wxid，chatusername 是群 ID，
    可以直接用；带引号的 `<title>` 只是给人看的那句话。
    """
    if not body:
        return None
    text = body.decode("utf-8", "ignore") if isinstance(body, (bytes, bytearray)) else str(body)
    m = _PATINFO_RE.search(text)
    if not m:
        return None
    inner = m.group(1)
    return {
        "from": _xml_value(inner, "fromusername").strip(),
        "group": _xml_value(inner, "chatusername").strip(),
        "to": _xml_value(inner, "pattedusername").strip(),
        # 老版本用 <suffix>，新版本用 <patsuffix>，两个都认
        "suffix": (_xml_value(inner, "patsuffix") or _xml_value(inner, "suffix")).strip(),
    }


def classify(local_type, body):
    """按 local_type 给出 (类别, 送给上游的文本)。

    类别用于日志与统计，文本是最终交给 OneBot 的内容；
    非文本消息给出方括号占位符，避免上游收到 XML 原文。

    图片与表情的文本里带上 XML 原文里的 md5：解码要按 md5 找对应的 `.dat`，
    只留一个占位符就没法定位了。
    """
    base = local_type & 0xFFFFFFFF
    sub = local_type >> 32
    if body is None:
        return "unknown", "[无法解析的消息]"
    if base == 1:
        return _KIND_TEXT, body
    if base == BASE_IMAGE:
        # body 可能是 str，也可能是未解码的 bytes；两种都要能取到 md5。
        text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else str(body)
        m = re.search(r'md5="([0-9a-fA-F]{32})"', text)
        # 占位符与 domain.content 的判定共用常量，改字面量时两边一起改
        return (
            "image",
            f'{IMAGE_PLACEHOLDER} md5="{m.group(1).lower()}"' if m else IMAGE_PLACEHOLDER,
        )
    if base == BASE_EMOJI:
        text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else str(body)
        m = re.search(r'md5="([0-9a-fA-F]{32})"', text)
        return "emoji", (
            f'{EMOJI_PLACEHOLDER} md5="{m.group(1).lower()}"' if m else EMOJI_PLACEHOLDER
        )
    if base == BASE_VOICE:
        return "voice", "[语音]"
    if base == BASE_VIDEO:
        return "video", "[视频]"
    if base == BASE_CARD:
        return "card", "[名片]"
    if base == BASE_LOCATION:
        return "location", "[位置]"
    if base == BASE_VOIP:
        return "voip", "[音视频通话]"
    if base == BASE_SYSTEM:
        content = _xml_value(body, "content")
        return "system", content or "[系统消息]"
    if base == BASE_REVOKE:
        return "revoke", "[撤回了一条消息]"
    if base == BASE_APPMSG:
        title = _xml_value(body, "title")
        if sub == APP_FILE:
            return "file", f"[文件] {title}".rstrip()
        if sub == APP_LINK:
            return "link", f"[链接] {title}".rstrip()
        if sub == APP_MERGED:
            return "merged", f"[合并转发] {title}".rstrip()
        if sub == APP_QUOTE:
            return "quote", title or "[引用消息]"
        if sub == APP_PAT:
            return "pat", title or "[拍一拍]"
        return "app", f"[卡片消息] {title}".rstrip()
    return "unknown", f"[未支持的消息类型 {local_type}]"


def payload_of(
    *,
    local_id: int,
    server_id,
    local_type: int,
    sender: str,
    create_time,
    body: str,
    my_wxid: str = "",
) -> dict:
    """把一行本地库消息拼成上游用的字典。

    撤回行（`local_type=10002`）不能按正文推：它得带上 `revoke` 字段，桥接才会把它摘成
    notice（`bridge_core._notice_for`）。这一行的 body 里通常只剩占位文本，撤回了谁要看
    同一会话的 sysmsg；解不到也照旧上报，昵称回退到这一行的发送者。
    """
    kind, text = classify(local_type, body)
    quote = quote_ref(body) if kind == "quote" else None
    if kind == "revoke":
        revoke = revoke_info(body) or {"nickname": ""}
    elif kind == "system":
        revoke = revoke_info(body)
    else:
        revoke = None
    pat = pat_info(body) if kind == "pat" else None
    return {
        "local_id": local_id,
        "server_id": str(server_id or ""),
        "local_type": local_type,
        "create_time": create_time,
        "sender": sender,
        "kind": kind,
        "text": text,
        "quote": quote or {},
        "revoke": revoke or {},
        "pat": pat or {},
        "outgoing": bool(my_wxid) and sender == my_wxid,
    }


class WxStore:
    """一个账号的本地库快照。

    refresh() 只在库文件真的变了时才重新解密；解密结果放在 work_dir，
    用 sqlite3 只读打开。
    """

    def __init__(
        self,
        data_dir: str,
        passphrase: bytes,
        work_dir: str,
        my_wxid: str = "",
        image_key: bytes = b"",
        image_xor_key: int = -1,
    ):
        self.data_dir = data_dir
        self.passphrase = passphrase
        self.work_dir = work_dir
        self.my_wxid = my_wxid
        self.image_key = image_key  # 图片 AES 密钥（账号级，16 字节 ASCII）
        self.image_xor_key = image_xor_key  # 图片尾段 XOR 密钥（账号级，-1 = 未取得）
        self._xor_sniffed = False
        self._seen = {}  # 相对路径 -> (size, mtime_ns)
        self._decrypted = set()  # 已成功解出的库名
        self._image_dir = os.path.join(work_dir, "images")

    # ---------- 路径与解密 ----------

    def _pairs(self):
        for sub, name in DBS:
            src = os.path.join(self.data_dir, "db_storage", sub, name)
            if os.path.isfile(src):
                yield name, src, os.path.join(self.work_dir, name)

    def refresh(self, force: bool = False) -> bool:
        """把改动过的库重新解出来。返回是否有库被更新。"""
        changed = False
        os.makedirs(self.work_dir, exist_ok=True)
        for name, src, dst in self._pairs():
            try:
                st = os.stat(src)
            except OSError:
                continue
            key = (st.st_size, st.st_mtime_ns)
            if not force and self._seen.get(name) == key:
                continue
            try:
                size = wx_crypto.decrypt_to_file(self.passphrase, src, dst)
            except wx_crypto.WrongKey:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("解密 %s 失败：%s", name, e)
                continue
            self._seen[name] = key
            self._decrypted.add(name)
            changed = True
            log.debug("已解密 %s（%d 字节）", name, size)
        return changed

    def _con(self, name):
        path = os.path.join(self.work_dir, name)
        if name not in self._decrypted or not os.path.isfile(path):
            raise WxStoreError(f"{name} 尚未解密，先调用 refresh()")
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    # ---------- 会话 ----------

    @staticmethod
    def table_for(username: str) -> str:
        """会话用户名对应的消息分片表名。"""
        return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()

    def sessions(self):
        """当前账号的所有会话，按最后活跃时间倒序。"""
        con = self._con("session.db")
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(SessionTable)")]
            if not cols:
                raise WxStoreError("session.db 里没有 SessionTable")
            rows = con.execute("SELECT * FROM SessionTable").fetchall()
            out = []
            for row in rows:
                d = dict(zip(cols, row))
                username = d.get("username") or ""
                if not username:
                    continue
                out.append(
                    {
                        "username": username,
                        "is_group": username.endswith("@chatroom"),
                        "unread": d.get("unread_count") or 0,
                        "summary": d.get("summary") or "",
                        "last_timestamp": d.get("last_timestamp") or 0,
                        "last_local_id": d.get("last_msg_locald_id") or 0,
                        "last_type": d.get("last_msg_type") or 0,
                        "last_sender": d.get("last_msg_sender") or "",
                        "is_hidden": bool(d.get("is_hidden")),
                    }
                )
            out.sort(key=lambda x: x["last_timestamp"], reverse=True)
            return out
        finally:
            con.close()

    def display_names(self):
        """wxid -> 展示名（优先备注名）。"""
        con = self._con("contact.db")
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(contact)")]
            if not cols:
                return {}
            pick = [c for c in ("username", "remark", "nick_name", "alias") if c in cols]
            names = {}
            for row in con.execute(f"SELECT {', '.join(pick)} FROM contact"):
                d = dict(zip(pick, row))
                username = d.get("username") or ""
                if username:
                    names[username] = d.get("remark") or d.get("nick_name") or username
            return names
        except sqlite3.Error as e:
            log.debug("读取联系人失败：%s", e)
            return {}
        finally:
            con.close()

    def self_nickname(self) -> str:
        """当前登录账号自己的微信昵称（contact 表里 username == my_wxid 那一行）。

        读不到（表结构变化、行不存在等）返回空串，由调用方决定兜底。
        """
        if not self.my_wxid:
            return ""
        con = self._con("contact.db")
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(contact)")]
            pick = [c for c in ("nick_name", "username") if c in cols]
            if not pick:
                return ""
            row = con.execute(
                f"SELECT {', '.join(pick)} FROM contact WHERE username = ?", (self.my_wxid,)
            ).fetchone()
            if not row:
                return ""
            return ((dict(zip(pick, row))).get("nick_name") or "").strip()
        except sqlite3.Error as e:
            log.debug("读取自己的昵称失败：%s", e)
            return ""
        finally:
            con.close()

    # ---------- 名册 ----------

    @staticmethod
    def _name_of(remark, nick_name, username):
        """展示名：备注名优先，其次昵称，最后退回 wxid。"""
        return (remark or "").strip() or (nick_name or "").strip() or (username or "")

    def contacts(self):
        """通讯录里的个人联系人。

        不含群会话（`@chatroom`）、公众号（`gh_` 开头）与微信内部的伪联系人
        （`SYSTEM_USERNAMES`：文件传输助手、服务通知、微信团队等）：它们在微信里
        各自是独立入口，混进好友列表会让客户端给它们推消息。
        """
        con = self._con("contact.db")
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(contact)")]
            if not cols:
                raise WxStoreError("contact.db 里没有 contact 表")
            pick = [
                c
                for c in ("username", "remark", "nick_name", "alias", "big_head_url", "flag")
                if c in cols
            ]
            where = "WHERE COALESCE(delete_flag, 0) = 0" if "delete_flag" in cols else ""
            out = []
            for row in con.execute(f"SELECT {', '.join(pick)} FROM contact {where}"):
                d = dict(zip(pick, row))
                username = (d.get("username") or "").strip()
                if not username or "@" in username or username.startswith("gh_"):
                    continue
                if username in SYSTEM_USERNAMES:
                    continue
                if self.my_wxid and username == self.my_wxid:
                    # 通讯录里可能含自己，好友列表不该把自己算成一个好友
                    continue
                out.append(
                    {
                        "username": username,
                        "name": self._name_of(d.get("remark"), d.get("nick_name"), username),
                        "remark": (d.get("remark") or "").strip(),
                        "nick_name": (d.get("nick_name") or "").strip(),
                        "alias": (d.get("alias") or "").strip(),
                        "avatar": (d.get("big_head_url") or "").strip(),
                        "starred": bool(int(d.get("flag") or 0) & 2),
                    }
                )
            return out
        except sqlite3.Error as e:
            log.debug("读取联系人失败：%s", e)
            return []
        finally:
            con.close()

    def groups(self):
        """群列表：群名、群主与成员数。

        群名与成员数都可缺省（刚建的群 contact 里可能还没有行），缺了不丢群。
        """
        con = self._con("contact.db")
        try:
            has_room = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_room'"
            ).fetchone()
            if not has_room:
                return []
            rows = con.execute(
                "SELECT r.username, r.owner, c.remark, c.nick_name, "
                "       (SELECT COUNT(*) FROM chatroom_member m WHERE m.room_id = r.id), "
                "       d.announcement_ "
                "FROM chat_room r "
                "LEFT JOIN contact c ON c.username = r.username "
                "LEFT JOIN chat_room_info_detail d ON d.room_id_ = r.id "
                "ORDER BY r.id"
            ).fetchall()
            out = []
            for username, owner, remark, nick_name, member_count, announcement in rows:
                username = (username or "").strip()
                if not username:
                    continue
                out.append(
                    {
                        "username": username,
                        "name": self._name_of(remark, nick_name, username),
                        "owner": (owner or "").strip(),
                        "member_count": int(member_count or 0),
                        "announcement": (announcement or "").strip(),
                    }
                )
            return out
        except sqlite3.Error as e:
            log.debug("读取群列表失败：%s", e)
            return []
        finally:
            con.close()

    def group_members(self, group_username: str):
        """某个群的成员名单（含群主标记）。

        `chatroom_member` 的 room_id / member_id 实测与 `contact.id`（即
        `name2id` 的 rowid）一一对应；用它连 `contact` 取备注与昵称。
        """
        con = self._con("contact.db")
        try:
            row = con.execute(
                "SELECT r.id, r.owner FROM chat_room r WHERE r.username = ?",
                (group_username,),
            ).fetchone()
            if not row:
                return []
            room_id, owner = row
            rows = con.execute(
                "SELECT c.username, c.remark, c.nick_name "
                "FROM chatroom_member m JOIN contact c ON c.id = m.member_id "
                "WHERE m.room_id = ? ORDER BY c.username",
                (room_id,),
            ).fetchall()
            out = []
            for username, remark, nick_name in rows:
                username = (username or "").strip()
                if not username:
                    continue
                out.append(
                    {
                        "username": username,
                        "name": self._name_of(remark, nick_name, username),
                        "remark": (remark or "").strip(),
                        "nick_name": (nick_name or "").strip(),
                        "role": "owner"
                        if username and username == (owner or "").strip()
                        else "member",
                    }
                )
            return out
        except sqlite3.Error as e:
            log.debug("读取群成员失败：%s", e)
            return []
        finally:
            con.close()

    # ---------- 消息 ----------

    def messages(self, username: str, after_local_id: int = 0, limit: int = 50):
        """取某会话里 local_id 大于游标的消息，按 local_id 升序。"""
        table = self.table_for(username)
        con = self._con("message_0.db")
        try:
            has = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not has:
                return []
            names = {r[0]: r[1] for r in con.execute("SELECT rowid, user_name FROM Name2Id")}
            rows = con.execute(
                f"SELECT local_id, server_id, local_type, real_sender_id, create_time, message_content "
                f'FROM "{table}" WHERE local_id > ? ORDER BY local_id ASC LIMIT ?',
                (after_local_id, limit),
            ).fetchall()
            out = []
            for local_id, server_id, local_type, sender_id, create_time, content in rows:
                sender = names.get(sender_id, "")
                out.append(
                    payload_of(
                        local_id=local_id,
                        server_id=server_id,
                        local_type=local_type,
                        sender=sender,
                        create_time=create_time,
                        body=_text_of(content),
                        my_wxid=self.my_wxid,
                    )
                )
            return out
        finally:
            con.close()

    def recent_messages(self, username: str, limit: int = 20):
        """取某会话最近的 limit 条消息，按 local_id 升序返回。

        与 :meth:`messages` 的区别是取「最新的一段」而不是「最早的一段」，
        供 bridge 重启后回填上下文使用。
        """
        table = self.table_for(username)
        con = self._con("message_0.db")
        try:
            has = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not has:
                return []
            names = {r[0]: r[1] for r in con.execute("SELECT rowid, user_name FROM Name2Id")}
            rows = con.execute(
                f"SELECT local_id, server_id, local_type, real_sender_id, create_time, message_content "
                f'FROM "{table}" ORDER BY local_id DESC LIMIT ?',
                (limit,),
            ).fetchall()
            out = []
            for local_id, server_id, local_type, sender_id, create_time, content in reversed(rows):
                sender = names.get(sender_id, "")
                out.append(
                    payload_of(
                        local_id=local_id,
                        server_id=server_id,
                        local_type=local_type,
                        sender=sender,
                        create_time=create_time,
                        body=_text_of(content),
                        my_wxid=self.my_wxid,
                    )
                )
            return out
        finally:
            con.close()

    def image_path(self, msg_md5: str):
        """图片消息的 md5 对应的本地文件名（仅用于定位 .dat，文件本体是加密的）。"""
        con = self._con("hardlink.db")
        try:
            row = con.execute(
                "SELECT file_name FROM image_hardlink_info_v4 WHERE md5=? OR md5_hash=?",
                (msg_md5, msg_md5),
            ).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None
        finally:
            con.close()

    # ---------- 图片 ----------

    #: 能直接交给客户端看图的格式。``wxgf`` 是微信私有的 HEVC 容器，不在其列。
    _PLAIN_IMAGE_EXTS = frozenset({"jpg", "png", "gif", "webp"})

    def image_ready(self) -> bool:
        """图片能否解码。

        XOR 密钥允许留空（-1）：它可以从本账号的图片里反推，不必预先配置。
        真正必需的是 AES 密钥——那个只能从进程内存取，取不到就解不了。
        """
        return bool(self.image_key)

    def _plain_image(self, main: str):
        """主文件取不到像素时，去旁边找一份能直接看的明文副本。

        微信把同一张图存了好几份：``<md5>_h.dat`` 是高清原图（明文 JPEG/PNG），
        ``<md5>_t.dat`` 是缩略图，预览还可能落在 cache 树的
        ``Message/<会话目录>/Bubble/<md5>_b.dat``。这些副本用同一把密钥就能解，
        主文件（``wxgf`` 这类容器）反而需要专门的解码器。

        返回 ``(路径, 解码结果)``；一份都解不出来时返回 None。
        """
        for cand in self._image_variants(main):
            try:
                img = wx_image.decode_file(cand, self.image_key, self._ensure_xor_key())
            except (wx_image.ImageDecodeError, WxStoreError) as e:
                log.debug("副本 %s 解不开：%s", os.path.basename(cand), e)
                continue
            if img.ext in self._PLAIN_IMAGE_EXTS:
                return cand, img
        return None

    def _image_variants(self, main: str) -> list[str]:
        """主文件旁边可能存在的明文副本，按画质从高到低排。"""
        stem = main[: -len(".dat")] if main.lower().endswith(".dat") else main
        out: list[str] = []
        high = f"{stem}_h.dat"
        if os.path.isfile(high):
            out.append(high)
        out.extend(self._cache_previews(stem))
        thumb = f"{stem}_t.dat"
        if os.path.isfile(thumb):
            out.append(thumb)
        return out

    def _cache_previews(self, stem: str) -> list[str]:
        """cache 树里的预览图。

        主文件在 ``msg/attach/<会话目录>/<月>/Img/`` 下，预览在
        ``cache/<月>/Message/<会话目录>/Bubble/`` 下：会话目录同名，月份两边都用
        通配，免得跟着微信改目录写法。
        """
        img_dir = os.path.dirname(stem)
        talker = os.path.basename(os.path.dirname(os.path.dirname(img_dir)))
        name = os.path.basename(stem) + "_b.dat"
        pattern = os.path.join(self.data_dir, "cache", "*", "Message", talker, "Bubble", name)
        return sorted(p for p in glob.glob(pattern) if os.path.isfile(p))

    def _resolve_image(self, msg_md5: str, local_id: int = 0, *, talker: str = "") -> str:
        """按 md5 找到对应的 .dat 文件路径。

        磁盘文件名用的是**资源库里的 md5**，不一定等于消息 XML 里的 md5，所以按
        三级兜底找：

        1. `hardlink.db` 的 md5 → file_name（最准，但并非所有版本都填）；
        2. 消息 XML 的 md5 直接匹配文件名；
        3. 用 `message_resource.db` 把 (会话, local_id) 映射到资源 md5 再匹配
           ——XML 的 md5 与资源 md5 在实测里并不相同，这是补上的关键一环。

        第三级必须带会话：`message_local_id` 只是会话内的序号，不同会话会撞号
        （实测 local_id=15 同时命中 filehelper 与当前会话），漏掉会话就会把别的
        会话的同号图片推出去。
        """
        attach = os.path.join(self.data_dir, "msg", "attach")
        if not os.path.isdir(attach):
            raise WxStoreError(f"图片目录不存在：{attach}")

        def find(name: str) -> str:
            if not name:
                return ""
            for hit in glob.glob(os.path.join(attach, "*", "*", "Img", name)):
                if os.path.isfile(hit):
                    return hit
            hits = glob.glob(os.path.join(attach, "*", "**", "Img", f"{name}*"), recursive=True)
            return hits[0] if hits else ""

        name = self.image_path(msg_md5)
        if name:
            hit = find(name)
            if hit:
                return hit

        hit = find(msg_md5)
        if hit:
            return hit

        for cand in self._resource_md5s(local_id, talker=talker):
            hit = find(cand)
            if hit:
                return hit
        raise WxStoreError(f"找不到图片文件（md5={msg_md5}, local_id={local_id}）")

    def _resource_md5s(self, local_id: int, *, talker: str = "") -> list[str]:
        """资源库里与某条消息相关的 md5 候选（只认这条消息所在的会话）。"""
        if not local_id or not talker:
            # 没有会话就无从判断这份资源属于谁：宁可空手而归（图片退回占位符），
            # 也不拿别的会话的同号图片顶上。
            return []
        try:
            con = self._con("message_resource.db")
        except WxStoreError:
            return []
        try:
            row = con.execute(
                "SELECT rowid FROM ChatName2Id WHERE user_name=?", (talker,)
            ).fetchone()
            if row is None:
                return []
            rows = con.execute(
                "SELECT packed_info FROM MessageResourceInfo"
                " WHERE message_local_id=? AND chat_id=?",
                (local_id, int(row[0])),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            con.close()
        out = []
        for (packed,) in rows:
            if not packed:
                continue
            text = packed.decode("utf-8", "ignore") if isinstance(packed, bytes) else str(packed)
            tail = text.strip().split("\n")[-1].strip()
            if len(tail) == 32 and all(c in "0123456789abcdefABCDEF" for c in tail):
                out.append(tail.lower())
        return out

    def _ensure_xor_key(self) -> int:
        """XOR 密钥没配时，从本账号的图片里反推一次。"""
        if self.image_xor_key >= 0:
            return self.image_xor_key
        if self._xor_sniffed:
            raise WxStoreError("无法从本地图片反推 XOR 密钥")
        self._xor_sniffed = True
        attach = os.path.join(self.data_dir, "msg", "attach")
        sample = glob.glob(os.path.join(attach, "*", "**", "Img", "*.dat"), recursive=True)
        self.image_xor_key = wx_image.sniff_xor_key(sample, self.image_key)
        return self.image_xor_key

    def extract_image(self, msg_md5: str, local_id: int = 0, *, talker: str = "") -> str | None:
        """把一条图片消息还原成可读文件，返回落地路径；失败返回 None。

        解出来的图片放在工作目录的 `images/` 下，按 md5 命名并缓存：同一张图
        只解一次。密钥缺失时返回 None 而不是抛错——图片解不出来不该让整条消息
        处理失败，上层会退回占位符。

        主文件是 ``wxgf``（微信私有的 HEVC 容器）时像素取不到，改用旁边的明文
        副本，见 :meth:`_plain_image`。
        """
        if not self.image_ready():
            return None
        try:
            src = self._resolve_image(msg_md5, local_id, talker=talker)
        except WxStoreError as e:
            log.debug("定位图片失败：%s", e)
            return None
        try:
            xor_key = self._ensure_xor_key()
            img = wx_image.decode_file(src, self.image_key, xor_key)
        except (wx_image.ImageDecodeError, WxStoreError) as e:
            log.warning("图片解码失败（md5=%s）：%s", msg_md5, e)
            return None
        if img.ext not in self._PLAIN_IMAGE_EXTS:
            container = img.ext
            found = self._plain_image(src)
            if found is None:
                log.warning("图片主文件是 %s、旁边没有可读副本（md5=%s）", container, msg_md5)
                return None
            src, img = found
            log.info("图片主文件是 %s，改用旁边的明文副本 %s", container, os.path.basename(src))
        os.makedirs(self._image_dir, exist_ok=True)
        out = os.path.join(self._image_dir, f"{os.path.basename(src)}.{img.ext}")
        if not os.path.isfile(out):
            with open(out, "wb") as fh:
                fh.write(img.data)
        return out


def open_store(
    passphrase: bytes,
    work_root: str,
    data_root: str = None,
    image_key: bytes = b"",
    image_xor_key: int = -1,
):
    """按当前登录账号开一个 WxStore。"""
    root = data_root or wechat_account.default_root()
    account = wechat_account.current_account(root)
    if account is None:
        raise WxStoreError(f"在 {root} 下找不到已登录的微信账号")
    if not account.data_dir:
        raise WxStoreError(f"账号 {account.wxid} 的数据目录不存在")
    work_dir = os.path.join(work_root, account.wxid)
    store = WxStore(
        account.data_dir,
        passphrase,
        work_dir,
        my_wxid=account.wxid,
        image_key=image_key,
        image_xor_key=image_xor_key,
    )
    store.refresh()
    return store


def _selftest(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    key_hex = None
    for cand in (os.path.join(os.getcwd(), "key.hex"), os.path.join(here, "key.hex")):
        if os.path.isfile(cand):
            key_hex = open(cand, encoding="utf-8").read().strip()
            break
    if argv[1:2]:
        key_hex = argv[1]
        if os.path.isfile(key_hex):
            key_hex = open(key_hex, encoding="utf-8").read().strip()
    if not key_hex:
        print("需要口令：python wx_store.py <key.hex 里的十六进制或该文件路径>")
        return 2

    t0 = time.time()
    store = open_store(
        bytes.fromhex(key_hex), os.path.join(os.environ.get("TEMP", "."), "wx_store_test")
    )
    print(f"账号 {store.my_wxid}  数据目录 {store.data_dir}")
    print(f"解密耗时 {time.time() - t0:.2f} s，已解密 {sorted(store._decrypted)}")

    names = store.display_names()
    sess = store.sessions()
    print(f"\n会话 {len(sess)} 个：")
    for s in sess:
        who = names.get(s["username"], s["username"])
        print(
            f"  {who[:24]:24s} {s['username']:34s} 未读={s['unread']} "
            f"摘要={s['summary'][:24]!r} 最后 local_id={s['last_local_id']}"
        )

    print("\n各会话最近 5 条：")
    for s in sess[:4]:
        msgs = store.messages(s["username"], 0, limit=1000)
        if not msgs:
            continue
        print(f"  --- {s['username']}  共 {len(msgs)} 条 ---")
        for m in msgs[-5:]:
            who = names.get(m["sender"], m["sender"])
            flag = "我" if m["outgoing"] else "收"
            print(f"    [{flag}][{m['kind']:8s}] {who[:18]:18s} {m['text'][:44]}")
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")
    sys.exit(_selftest(sys.argv))
