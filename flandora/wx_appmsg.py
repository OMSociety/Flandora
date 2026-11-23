"""微信富消息（appmsg 与几种单列类型）的解析与文本化。

公众号文章、B 站分享、网易云、小程序、文件、聊天记录、视频号、转账红包……都落在
同一种 XML 外壳里：``<msg><appmsg>`` 下共用 ``title`` / ``des`` / ``url`` /
``sourcedisplayname`` / ``appinfo``，差异只在 ``<type>``（即本地库 ``local_type`` 的高
32 位）和少数子节点（小程序的 ``weappinfo``、聊天记录的 ``recorditem``）。所以只写一个
解析函数，按类型选标签与取值。

产出两样东西：给人（和 LLM）读的多行文本，以及随事件上报的结构化字典。文本里不放
追踪参数：公众号链接只留定位文章的四个参数，B 站短链去掉整段 query。

XML 来自任意发送者：含 ``<!DOCTYPE`` / ``<!ENTITY`` 的一律不交给解析器。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 一段文本、一条聊天记录行的长度上限：超出的截断，免得一张长图文卡片撑爆上下文
MAX_DESC_CHARS = 300
MAX_RECORD_LINES = 30
MAX_RECORD_LINE_CHARS = 300

#: appmsg 子类型 → 卡片标签。没登记的归「卡片」。
APP_LABELS = {
    3: "音乐",
    4: "分享",
    5: "链接",
    6: "文件",
    19: "聊天记录",
    24: "笔记",
    33: "小程序",
    36: "小程序",
    51: "视频号",
    53: "接龙",
    87: "群公告",
    2000: "转账",
    2001: "红包",
}

#: 公众号文章链接里能唯一定位文章的参数；其余（mpshare、srcid、sharer_shareinfo……）全是追踪
_MP_KEEP = ("__biz", "mid", "idx", "sn")


@dataclass(frozen=True)
class Card:
    """一条富消息的结构化内容。"""

    kind: str  # app / location / namecard
    label: str  # 方括号里的标签：链接、小程序……
    title: str = ""
    desc: str = ""
    url: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)  # 类型专有的补充字段，随事件上报
    lines: tuple[str, ...] = ()  # 聊天记录、群公告的逐行内容

    def to_dict(self) -> dict:
        """随事件上报的字典；空字段不带。"""
        out = {"type": self.label, "title": self.title, "desc": self.desc}
        if self.url:
            out["url"] = self.url
        if self.source:
            out["source"] = self.source
        if self.lines:
            out["lines"] = list(self.lines)
        out.update({k: v for k, v in self.extra.items() if v not in ("", None)})
        return {k: v for k, v in out.items() if v not in ("", None)}

    def text(self) -> str:
        """给人读的多行文本。"""
        head = f"[{self.label}]"
        if self.title:
            head += f" {self.title}"
        rows = [head]
        if self.desc:
            rows.append(self.desc)
        rows.extend(self.lines)
        if self.source:
            rows.append(f"来源：{self.source}")
        if self.url:
            rows.append(self.url)
        return "\n".join(rows)


def _as_text(body) -> str:
    if body is None:
        return ""
    return body.decode("utf-8", "ignore") if isinstance(body, (bytes, bytearray)) else str(body)


def _safe_root(text: str):
    """解析 XML；不合规或含实体声明时返回 None。"""
    stripped = text.strip()
    if not stripped or re.search(r"<!(DOCTYPE|ENTITY)", stripped, re.I):
        return None
    # 群消息正文前可能还带着 `wxid:\n`，从第一个 `<` 起算
    start = stripped.find("<")
    if start < 0:
        return None
    try:
        return ET.fromstring(stripped[start:])
    except ET.ParseError:
        return None


def _t(node, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def clean_url(url: str) -> str:
    """去掉分享链接里的追踪参数。认不出的站点原样返回。"""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if host == "mp.weixin.qq.com" and parts.path == "/s":
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        kept = [(k, query[k]) for k in _MP_KEEP if k in query]
        if kept:
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))
    if host in ("b23.tv", "www.bilibili.com", "m.bilibili.com") and parts.query:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return url


def _is_placeholder_url(url: str) -> bool:
    """「请升级微信」之类的占位页：对读者毫无用处，不展示。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return host == "support.weixin.qq.com" or parts.path.startswith("/mp/waerrpage")


def _record_items(raw: str) -> list[tuple[str, str]]:
    """聊天记录 / 群公告的 ``recorditem``（CDATA 里还有一层 XML）展开成 (发言人, 内容)。"""
    root = _safe_root(raw)
    if root is None:
        return []
    rows: list[tuple[str, str]] = []
    for item in root.findall(".//dataitem"):
        if item.get("htmlid") == "WeNoteHtmlFile":
            continue  # 微信给富文本另存的一份 .htm 渲染，不是内容
        kind = item.get("datatype") or ""
        who = _t(item, "sourcename")
        if kind == "1":
            body = _t(item, "datadesc")
        elif kind == "2":
            body = "[图片]"
        elif kind == "3":
            body = "[语音]"
        elif kind == "4":
            body = "[视频]"
        elif kind == "8":
            body = "[文件] " + (_t(item, "datatitle") or "").strip()
        elif kind == "5":
            body = "[链接] " + (_t(item, "datatitle") or _t(item, "weburlitem/title")).strip()
        elif kind == "6":
            body = "[位置] " + (_t(item, "locitem/poiname") or _t(item, "locitem/label")).strip()
        elif kind == "17":
            body = "[聊天记录] " + _t(item, "datatitle")
        else:
            body = (_t(item, "datadesc") or _t(item, "datatitle") or "[消息]").strip()
        body = _clip(" ".join(body.split()), MAX_RECORD_LINE_CHARS)
        if body:
            rows.append((who, body))
    return rows


def _record_lines(raw: str) -> tuple[str, ...]:
    """聊天记录逐行文本（带发言人），超出上限的折成一行「另有 N 条」。"""
    rows = [f"{who}: {body}" if who else body for who, body in _record_items(raw)]
    if len(rows) > MAX_RECORD_LINES:
        rest = len(rows) - MAX_RECORD_LINES
        rows = rows[:MAX_RECORD_LINES] + [f"……另有 {rest} 条"]
    return tuple(rows)


def parse_appmsg(sub_type: int, body) -> Card | None:
    """解析一条 appmsg；XML 解不开时返回 None（调用方退回标题正则）。"""
    root = _safe_root(_as_text(body))
    app = root.find("appmsg") if root is not None else None
    if app is None:
        return None
    kind = int(sub_type or 0) or int(_t(app, "type") or 0)
    label = APP_LABELS.get(kind, "卡片")
    title = _t(app, "title")
    desc = _t(app, "des")
    url = clean_url(_t(app, "url"))
    source = _t(root, "appmsg/sourcedisplayname")
    extra: dict = {"app_type": kind}
    lines: tuple[str, ...] = ()

    appid = app.get("appid") or ""
    if appid:
        extra["appid"] = appid

    if kind == 5:
        if _t(app, "sourceusername").startswith("gh_"):
            label = "公众号文章"
            extra["account"] = _t(app, "sourceusername")
        source = source or _t(root, "appinfo/appname")
    elif kind in (3, 4):
        source = source or _t(root, "appinfo/appname")
    elif kind in (33, 36):
        wx = app.find("weappinfo")
        if wx is not None:
            extra["pagepath"] = _t(wx, "pagepath")
            extra["username"] = _t(wx, "username")
            extra["appid"] = _t(wx, "appid") or extra.get("appid", "")
            extra["app_version"] = _t(wx, "version")
        extra["miniapp"] = True
        if not extra.get("appid"):
            m = re.search(r"[?&]appid=(wx[0-9a-f]{16})", _t(app, "url"))
            if m:
                extra["appid"] = m.group(1)
    elif kind == 6:
        extra["size"] = _t(app, "appattach/totallen")
        extra["ext"] = _t(app, "appattach/fileext")
        extra["md5"] = _t(app, "md5")
        desc = human_size(extra["size"])
        source = ""
    elif kind in (19, 24):
        info = _t(app, "recorditem")
        lines = _record_lines(info)
        if kind == 24 and not title:
            title = _clip(desc, 60)
            desc = ""
        desc = _clip(desc, MAX_DESC_CHARS) if not lines else ""
    elif kind == 87:
        lines = tuple(body for _, body in _record_items(_t(app, "announcement")))
        title = ""
        desc = ""
        url = ""
    elif kind == 51:
        feed = app.find("finderFeed")
        if feed is not None:
            title = _t(feed, "nickname")
            desc = _t(feed, "desc")
            extra["feed_id"] = _t(feed, "objectId")
        else:
            title = ""
        url = ""
        source = "视频号"
    elif kind == 53:
        title = title.removeprefix("#接龍").removeprefix("#接龙").strip() or title
    elif kind in (2000, 2001):
        pay = app.find("wcpayinfo")
        fee = _t(pay, "feedesc")
        memo = _t(pay, "pay_memo") or _t(pay, "receivertitle")
        title = fee or title
        desc = f"备注：{memo}" if memo else ""
        extra["paysubtype"] = _t(pay, "paysubtype")
        url = ""
        source = ""

    if url and _is_placeholder_url(url):
        url = ""
    if kind not in (19, 24, 87, 2000, 2001):
        desc = _clip(desc, MAX_DESC_CHARS)
    return Card(
        kind="app",
        label=label,
        title=_clip(" ".join(title.split()), 200),
        desc=desc,
        url=url,
        source=source,
        extra=extra,
        lines=lines,
    )


def parse_location(body) -> Card | None:
    """位置消息（local_type=48）。"""
    root = _safe_root(_as_text(body))
    loc = root.find("location") if root is not None else None
    if loc is None:
        return None
    name = (loc.get("poiname") or "").strip()
    address = (loc.get("label") or "").strip()
    lat, lng = (loc.get("x") or "").strip(), (loc.get("y") or "").strip()
    desc = address if name else ""
    return Card(
        kind="location",
        label="位置",
        title=name or address,
        desc=desc,
        extra={"latitude": lat, "longitude": lng},
    )


def parse_namecard(body) -> Card | None:
    """名片消息（local_type=42）。``username`` 可能是陌生人的加密标识，不展示。"""
    root = _safe_root(_as_text(body))
    if root is None or root.tag != "msg":
        return None
    nick = (root.get("nickname") or "").strip()
    alias = (root.get("alias") or "").strip()
    sign = (root.get("certinfo") or root.get("sign") or "").strip()
    username = (root.get("username") or "").strip()
    return Card(
        kind="namecard",
        label="名片",
        title=nick,
        desc=_clip(sign, 120),
        extra={"alias": alias, "wxid": username if "@" not in username else ""},
    )


def video_info(body) -> dict:
    """视频消息的时长（秒）与字节数。"""
    root = _safe_root(_as_text(body))
    vm = root.find("videomsg") if root is not None else None
    if vm is None:
        return {}
    out = {}
    for key, attr in (("seconds", "playlength"), ("size", "length")):
        try:
            out[key] = int(vm.get(attr) or 0)
        except ValueError:
            out[key] = 0
    return out


def voice_info(body) -> dict:
    """语音消息的时长（毫秒）与字节数。"""
    root = _safe_root(_as_text(body))
    vm = root.find("voicemsg") if root is not None else None
    if vm is None:
        return {}
    out = {}
    for key, attr in (("millis", "voicelength"), ("size", "length")):
        try:
            out[key] = int(vm.get(attr) or 0)
        except ValueError:
            out[key] = 0
    return out


def emoticon_md5(body) -> str:
    """「表情」型 appmsg（type=8）里的表情 md5，没有返回空串。"""
    root = _safe_root(_as_text(body))
    return _t(root, "appmsg/appattach/emoticonmd5").lower() if root is not None else ""


def human_size(size) -> str:
    """字节数 → 「195.9 KB」。"""
    try:
        n = float(size)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""
