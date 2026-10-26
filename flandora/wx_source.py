"""把微信本地库接成 bridge 的收件源。

这里直接读微信自己的库：
不依赖任何第三方插件，微信也不必交给别的程序控制。

消息的新旧靠每个会话的 local_id 游标判断。首次启动时把游标推到当前值，
不重放历史（需要重放时把 wx_backfill_seconds 设成正数）；启动之后才出现的会话
（陌生人第一次来信、新加群、删掉又重建的会话）按同一个分界处理：分界之前算历史，
分界之后——也就是程序正跑着的时候到的——照常推送。
"""

import json
import logging
import os
import re
import time

from . import config, wechat_account, wx_crypto, wx_store
from .domain.content import IMAGE_PLACEHOLDER
from .wx_image_key import open_aes_key
from .wx_store import WxStoreError
from .wxkey.store import KeyStore, KeyStoreError

log = logging.getLogger("flandora")

MAX_BATCH = 200  # 单次轮询每个会话最多取多少条
PRIME_LIMIT = 500  # 计算回填起点时最多往前看多少条


class WxSourceError(Exception):
    """收件源起不来（口令缺失/账号不对/依赖缺失）。"""


def default_key_file() -> str:
    """口令文件默认位置：可写数据目录下的 ``wx_key.dat``。

    扩展名是 .dat 而非 .hex：内容是 DPAPI 密文（见 wxkey.store），不是明文。
    """
    return os.path.join(config.data_dir(), "wx_key.dat")


def default_image_key_file() -> str:
    """图片密钥默认位置：可写数据目录下的 ``wx_image_key.json``。

    图片 AES 密钥是账号级的，且只在微信解码图片那几秒驻留内存。它与数据库口令
    分开存：缺它只影响图片，不影响文本通道。
    """
    return os.path.join(config.data_dir(), "wx_image_key.json")


def default_work_dir() -> str:
    base = os.path.dirname(config.CONFIG_FILE) or "."
    return os.path.join(base, "wx_cache")


def load_passphrase(path: str = None) -> bytes:
    """读口令，并确认它属于当前登录的账号。

    口令文件是 DPAPI 加密的，只有生成它的那个 Windows 账户解得开；换账户或
    换机器会解不开，这时给出「重新取钥」的指引，而不是把底层错误直接抛给用户。
    """
    path = path or config.WX_KEY_FILE or default_key_file()
    if not os.path.isfile(path):
        raise WxSourceError(f"找不到微信口令文件 {path}。请在 WebUI 的「取钥向导」里走一遍取钥。")

    store = KeyStore(path)
    try:
        stored = store.load()
    except KeyStoreError as e:
        raise WxSourceError(str(e)) from e
    if stored is None:
        raise WxSourceError(f"口令文件为空：{path}")

    # 账号对不上时直接报错，不拿旧口令去解新账号的库——那样只会得到
    # 「页面校验失败」，看不出真正原因是换了账号。
    current = wechat_account.current_account(config.WECHAT_FILES_ROOT or None)
    if current is not None and stored.wxid and current.wxid != stored.wxid:
        raise WxSourceError(
            f"口令属于账号 {stored.wxid}，当前登录的是 {current.wxid}。"
            f"请在 WebUI 的「取钥向导」里为新账号重新取一次钥。"
        )
    return stored.passphrase


class WxLocalSource:
    """轮询本地库，产出统一形态的消息字典。"""

    def __init__(
        self,
        store,
        backfill_seconds: int = 0,
        poll_seconds: float = 1.0,
        on_outgoing=None,
    ):
        self.store = store
        self.backfill_seconds = max(0, int(backfill_seconds or 0))
        self.poll_seconds = max(0.2, float(poll_seconds or 1.0))
        self.cursors = {}
        self.names = {}
        # 本进程开始收件的时刻：启动之后新出现的会话靠它划分「历史」与「刚到的」
        self.started_at = int(time.time())
        self._primed = False
        # 自己发出的消息不推送（防自回环），但要把它的服务端号交给上层绑号：
        # 客户端引用机器人自己的消息时，靠那个号回查得到原文。
        self.on_outgoing = on_outgoing

    # ---------- 启动 ----------

    def prime(self):
        """记录各会话当前进度。回填模式下调到 backfill 窗口的起点。"""
        self.names = self.store.display_names()
        now = int(time.time())
        cutoff = now - self.backfill_seconds
        replayed = 0
        for s in self.store.sessions():
            username = s["username"]
            latest = int(s["last_local_id"] or 0)
            cursor = latest
            if self.backfill_seconds > 0 and latest:
                msgs = self.store.messages(username, 0, limit=PRIME_LIMIT)
                fresh = [m for m in msgs if (m["create_time"] or 0) >= cutoff]
                if fresh:
                    cursor = fresh[0]["local_id"] - 1
                    replayed += len(fresh)
            self.cursors[username] = cursor
        self._primed = True
        if replayed:
            log.info(f"本地库收件：回填窗口 {self.backfill_seconds} 秒，涉及 {replayed} 条历史消息")
        else:
            log.info(f"本地库收件：已就位，{len(self.cursors)} 个会话从当前位置开始")

    # ---------- 轮询 ----------

    def poll(self):
        """取回新消息。返回统一形态的字典列表（不含自己发出的）。"""
        if not self._primed:
            self.prime()
        self.store.refresh()
        out = []
        for s in self.store.sessions():
            username = s["username"]
            cursor = self.cursors.get(username)
            if cursor is None:
                # 会话是本进程跑起来之后才出现的。启动时那份名单里没有它，显示名要现取
                # 一次，否则群里只会显示 123456@chatroom 这类原始 ID，桥接也学不到群名。
                try:
                    self.names.update(self.store.display_names())
                except Exception as e:  # noqa: BLE001 - 名字只影响展示，读不到不该挡住收件
                    log.debug("新会话 %s 的显示名读取失败：%s", username, e)
                cursor = self._boundary_cursor(username, int(s["last_local_id"] or 0))
                self.cursors[username] = cursor
                log.info(
                    f"本地库收件：新会话 {self.names.get(username) or username}，"
                    f"从 local_id {cursor} 之后开始读"
                )
            try:
                msgs = self.store.messages(username, cursor, limit=MAX_BATCH)
            except WxStoreError as e:
                log.debug("读取 %s 的消息失败：%s", username, e)
                continue
            for m in msgs:
                self.cursors[username] = max(self.cursors[username], m["local_id"])
                if m["outgoing"]:
                    self._report_outgoing(username, m)
                    continue
                out.append(self._to_data(s, m))
        return out

    def _report_outgoing(self, username: str, msg) -> None:
        """把自己发出的一条消息的服务端号报给上层。绑号失败不影响收件。"""
        if self.on_outgoing is None:
            return
        server_id = str(msg.get("server_id") or "")
        if not server_id:
            return
        try:
            self.on_outgoing(username, server_id)
        except Exception as e:  # noqa: BLE001 - 绑号只影响引用回查，不该挡住收件
            log.debug("出站服务端号上报失败（%s）：%s", username, e)

    def _boundary_cursor(self, username: str, latest: int) -> int:
        """新会话的起读游标：分界之前最后一条的 local_id（没有就是 0）。

        分界与 :meth:`prime` 同一口径——不开回填是「本进程启动的时刻」，开了回填是
        「最近 backfill_seconds 秒」。分界之前的算历史，不重放；分界之后的照常推，
        所以「让会话第一次现身的那条消息」不会丢。

        只回看尾部 PRIME_LIMIT 条：新会话的存量通常很短，一次查询就够找到分界；
        存量比这还长（旧会话以新面孔出现）时索性跳过更早的部分，不为了找分界
        把整段历史读一遍。
        """
        if latest <= 0:
            return 0
        if self.backfill_seconds > 0:
            cutoff = int(time.time()) - self.backfill_seconds
        else:
            cutoff = self.started_at
        start = max(0, latest - PRIME_LIMIT)
        try:
            msgs = self.store.messages(username, start, limit=PRIME_LIMIT + 1)
        except WxStoreError as e:
            log.debug("读取新会话 %s 的历史失败：%s", username, e)
            return latest
        history = [m["local_id"] for m in msgs if (m["create_time"] or 0) < cutoff]
        return max(history) if history else start

    def _to_data(self, session, msg):
        username = session["username"]
        is_group = bool(session["is_group"])
        chat_name = self.names.get(username) or username
        who = self.names.get(msg["sender"]) or msg["sender"] or chat_name

        data = {
            "rawid": f"wxlocal:{username}:{msg['local_id']}",
            "timestamp": msg["create_time"] or int(time.time()),
            "content": msg["text"],
            "sourceName": chat_name,
            "talkerName": who if is_group else chat_name,
            "talkerId": username,
            "sessionId": username,
            "sessionType": "group" if is_group else "private",
            "groupName": chat_name if is_group else "",
            "sender": msg["sender"],
            "senderName": who,
            "msgType": msg["kind"],
            "localId": msg["local_id"],
            # 服务端号是引用消息唯一的回查键（<refermsg><svrid> 就是它）
            "serverId": msg.get("server_id", ""),
            "quote": msg.get("quote") or {},
            # 撤回与拍一拍带结构化字段，桥接据此发 notice 而不是当聊天正文
            "revoke": msg.get("revoke") or {},
            "pat": msg.get("pat") or {},
        }

        # 图片与表情：能解出文件就把路径带上，下游据此读图；解不出就保留占位符文本。
        # 图片的正文换成路径（分派与展示都用得到），表情的正文保留占位符+md5，
        # 由 process_emoji_message 决定要不要用描述替换。
        if msg["kind"] in ("image", "emoji"):
            path = self._image_path_for(msg, username)
            if path:
                data["imagePath"] = path
                if msg["kind"] == "image":
                    # 占位符与 domain.content 的判定共用常量，改字面量时两边一起改
                    data["content"] = f"{IMAGE_PLACEHOLDER} {path}"
        return data

    def _image_path_for(self, msg, talker: str = "") -> str:
        """按消息内容里的 md5 还原图片。失败返回空串。

        `talker` 是这条消息所在的会话：资源库里的消息序号是会话内的，缺了它就会
        把别的会话的同号图片当成这条消息的图。
        """
        if not self.store.image_ready():
            return ""
        body = msg.get("raw") or msg.get("text") or ""
        m = re.search(r'md5="([0-9a-fA-F]{32})"', body)
        if not m:
            return ""
        try:
            md5 = m.group(1).lower()
            return self.store.extract_image(md5, int(msg.get("local_id") or 0), talker=talker) or ""
        except Exception as exc:  # noqa: BLE001 - 单张图失败不该影响整条消息
            log.debug("图片还原失败：%s", exc)
            return ""

    def contacts(self):
        """用于日志/界面：当前会话的展示名。"""
        return dict(self.names)


def load_image_key(current_wxid: str = "") -> tuple[bytes, int]:
    """读图片解码用的两把密钥。拿不到时返回 (b"", -1)，图片退回占位符。

    与数据库口令分开存：口令丢了整条通道都不能用，而图片密钥缺失只影响图片。
    所以这里不抛错，让文本消息照常走。

    AES 密钥落盘时可能是三种写法：DPAPI 密文（``aes_key_dpapi``，取钥时由本机写入，
    只有同一个 Windows 账户解得开），或手写的明文——32 位十六进制（16 字节二进制），
    或直接 16 个 ASCII 字符。实测微信用的是最后一种，三种都接受，避免手工填错就静默
    失效。

    **密钥是账号级的**：文件里记了取钥时的账号，与当前登录账号不一致时按「没有
    密钥」处理。否则切号后会拿旧密钥去解新账号的图，解出一堆随机字节再报解码
    失败——那既看不出真正原因，也白费每张图的解密开销。
    """
    path = config.WX_IMAGE_KEY_FILE or default_image_key_file()
    if not os.path.isfile(path):
        log.info("未配置图片密钥（%s），图片消息将以占位符形式转发", path)
        return b"", -1
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("图片密钥文件读不出来（%s）：%s", path, exc)
        return b"", -1

    owner = (payload.get("wxid") or "").strip()
    if owner and current_wxid and owner != current_wxid:
        log.warning(
            "图片密钥属于账号 %s，当前登录的是 %s：先按无密钥处理。"
            "要显示图片，请对新账号执行一次「取图片密钥」。",
            owner,
            current_wxid,
        )
        return b"", -1

    aes = open_aes_key(str(payload.get("aes_key_dpapi") or ""))
    if not aes:
        aes = _parse_aes_key(payload.get("aes_key") or "")
    if not aes:
        return b"", -1
    try:
        xor_int = int(payload.get("xor_key", -1))
    except (TypeError, ValueError):
        xor_int = -1
    log.info(
        "已加载图片密钥（账号 %s），XOR 密钥%s",
        owner or "未知",
        "已配置" if xor_int >= 0 else "未配置（将自动反推）",
    )
    return aes, xor_int


def _parse_aes_key(raw: str) -> bytes:
    """把密钥文本解析成 16 字节。兼容 ASCII 与十六进制两种写法。"""
    text = raw.strip()
    if len(text) == 32:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    if len(text) == 16 and text.isascii():
        return text.encode("ascii")
    log.warning("图片 AES 密钥格式不对：应为 32 位十六进制或 16 个 ASCII 字符，实为 %r", text[:40])
    return b""


def build_source(key_file: str = None, on_outgoing=None) -> WxLocalSource:
    """按配置开一个收件源。

    ``on_outgoing(talker, server_id)`` 是可选回调：轮询到自己发出的消息时，把它的
    服务端号交给上层绑到 message_id 上（客户端引用机器人自己的消息时要用）。
    """
    root = config.WECHAT_FILES_ROOT or wechat_account.default_root()
    account = wechat_account.current_account(root)
    if account is None:
        raise WxSourceError(f"在 {root} 下找不到已登录的微信账号")
    if not account.data_dir:
        raise WxSourceError(f"账号 {account.wxid} 的数据目录不存在（可能还没登录完成）")

    passphrase = load_passphrase(key_file)
    image_key, image_xor = load_image_key(account.wxid)
    work_root = config.WX_WORK_DIR or default_work_dir()
    work_dir = os.path.join(work_root, account.wxid)

    store = wx_store.WxStore(
        account.data_dir,
        passphrase,
        work_dir,
        my_wxid=account.wxid,
        image_key=image_key,
        image_xor_key=image_xor,
    )
    try:
        store.refresh()
    except wx_crypto.WrongKey:
        raise WxSourceError(
            f"口令与账号 {account.wxid} 对不上。切换过微信账号后需要重新取一次钥"
        ) from None

    src = WxLocalSource(
        store,
        backfill_seconds=config.WX_BACKFILL_SECONDS,
        poll_seconds=config.WX_POLL_SECONDS,
        on_outgoing=on_outgoing,
    )
    step = ", ".join(sorted(store._decrypted))
    log.info(
        f"本地库收件：账号 {account.wxid}，已解密 {step}，"
        f"图片{'可用' if store.image_ready() else '不可用（缺密钥）'}"
    )
    return src


def _selftest(argv):
    """python wx_source.py [口令文件] —— 打印一轮轮询会产出什么。"""
    src = build_source(argv[1] if argv[1:2] else None)
    src.prime()
    print(f"会话：{len(src.cursors)} 个")
    for name, cursor in src.cursors.items():
        print(f"  {src.names.get(name, name)[:24]:24s} {name:34s} 游标={cursor}")
    print("\n当前能取到的新消息（应为空）：")
    for d in src.poll():
        print(f"  [{d['msgType']}] {d['senderName']}: {d['content'][:40]}")
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(_selftest(sys.argv))
