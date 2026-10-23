"""把微信本地库接成 bridge 的收件源。

这里直接读微信自己的库：
不依赖任何第三方插件，微信也不必交给别的程序控制。

消息的新旧靠每个会话的 local_id 游标判断。首次启动时把游标推到当前值，
不重放历史（需要重放时把 wx_backfill_seconds 设成正数）。
"""
import json
import logging
import os
import re
import time

from . import config
from . import wechat_account
from . import wx_crypto
from . import wx_store
from .wx_store import WxStoreError
from .wxkey.store import KeyStore, KeyStoreError

log = logging.getLogger("ob11-bridge")

MAX_BATCH = 200          # 单次轮询每个会话最多取多少条
PRIME_LIMIT = 500        # 计算回填起点时最多往前看多少条


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
        raise WxSourceError(
            f"找不到微信口令文件 {path}。请先在配置界面里执行一次「取密钥」。")

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
            f"请为新账号重新执行一次「取密钥」。")
    return stored.passphrase


class WxLocalSource:
    """轮询本地库，产出统一形态的消息字典。"""

    def __init__(self, store, backfill_seconds: int = 0, poll_seconds: float = 1.0):
        self.store = store
        self.backfill_seconds = max(0, int(backfill_seconds or 0))
        self.poll_seconds = max(0.2, float(poll_seconds or 1.0))
        self.cursors = {}
        self.names = {}
        self._primed = False

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
                # 新出现的会话（例如刚建群）：从当前位置开始，不补历史
                self.cursors[username] = int(s["last_local_id"] or 0)
                continue
            try:
                msgs = self.store.messages(username, cursor, limit=MAX_BATCH)
            except WxStoreError as e:
                log.debug("读取 %s 的消息失败：%s", username, e)
                continue
            for m in msgs:
                self.cursors[username] = max(self.cursors[username], m["local_id"])
                if m["outgoing"]:
                    continue
                out.append(self._to_data(s, m))
        return out

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
        }

        # 图片与表情：能解出文件就把路径带上，下游据此读图；解不出就保留占位符文本。
        # 图片的正文换成路径（分派与展示都用得到），表情的正文保留占位符+md5，
        # 由 process_emoji_message 决定要不要用描述替换。
        if msg["kind"] in ("image", "emoji"):
            path = self._image_path_for(msg)
            if path:
                data["imagePath"] = path
                if msg["kind"] == "image":
                    data["content"] = f"[图片] {path}"
        return data

    def _image_path_for(self, msg) -> str:
        """按消息内容里的 md5 还原图片。失败返回空串。"""
        if not self.store.image_ready():
            return ""
        body = msg.get("raw") or msg.get("text") or ""
        m = re.search(r'md5="([0-9a-fA-F]{32})"', body)
        if not m:
            return ""
        try:
            return self.store.extract_image(m.group(1).lower(),
                                            int(msg.get("local_id") or 0)) or ""
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

    AES 密钥在落盘时可能有两种写法：32 位十六进制（16 字节二进制），或直接
    16 个 ASCII 字符。实测微信用的是后者，但两种都接受，避免手工填错就静默失效。

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
            "要显示图片，请对新账号执行一次「取图片密钥」。", owner, current_wxid)
        return b"", -1

    aes = _parse_aes_key(payload.get("aes_key") or "")
    if not aes:
        return b"", -1
    try:
        xor_int = int(payload.get("xor_key", -1))
    except (TypeError, ValueError):
        xor_int = -1
    log.info("已加载图片密钥（账号 %s），XOR 密钥 %s",
             owner or "未知",
             f"0x{xor_int:02x}" if xor_int >= 0 else "未配置（将自动反推）")
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
    log.warning("图片 AES 密钥格式不对：应为 32 位十六进制或 16 个 ASCII 字符，实为 %r",
                text[:40])
    return b""


def build_source(key_file: str = None) -> WxLocalSource:
    """按配置开一个收件源。"""
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

    store = wx_store.WxStore(account.data_dir, passphrase, work_dir, my_wxid=account.wxid,
                             image_key=image_key, image_xor_key=image_xor)
    try:
        store.refresh()
    except wx_crypto.WrongKey:
        raise WxSourceError(
            f"口令与账号 {account.wxid} 对不上。切换过微信账号后需要重新取一次钥") from None

    src = WxLocalSource(store, backfill_seconds=config.WX_BACKFILL_SECONDS,
                        poll_seconds=config.WX_POLL_SECONDS)
    step = ", ".join(sorted(store._decrypted))
    log.info(f"本地库收件：账号 {account.wxid}，已解密 {step}，"
             f"图片{'可用' if store.image_ready() else '不可用（缺密钥）'}")
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
