"""把微信本地库接成 bridge 的收件源。

WeFlow 的推送是「插件把消息转发出来」，这里换成直接读微信自己的库：
不依赖任何第三方插件，微信也不必交给别的程序控制。

消息的新旧靠每个会话的 local_id 游标判断。首次启动时把游标推到当前值，
不重放历史（需要重放时把 wx_backfill_seconds 设成正数）。
"""
import logging
import os
import time

import config
import wechat_account
import wx_crypto
import wx_store
from wx_store import WxStoreError
from wxkey.store import KeyStore, KeyStoreError

log = logging.getLogger("ob11-bridge")

MAX_BATCH = 200          # 单次轮询每个会话最多取多少条
PRIME_LIMIT = 500        # 计算回填起点时最多往前看多少条


class WxSourceError(Exception):
    """收件源起不来（口令缺失/账号不对/依赖缺失）。"""


def default_key_file() -> str:
    """口令文件默认位置：配置文件同目录下的 ``wx_key.dat``。

    扩展名是 .dat 而非 .hex：内容是 DPAPI 密文（见 wxkey.store），不是明文。
    """
    base = os.path.dirname(config.CONFIG_FILE) or "."
    return os.path.join(base, "wx_key.dat")


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
    """轮询本地库，产出与 WeFlow 推送同形的消息字典。"""

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
        """取回新消息。返回与 WeFlow 推送同形的字典列表（不含自己发出的）。"""
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
        return {
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

    def contacts(self):
        """用于日志/界面：当前会话的展示名。"""
        return dict(self.names)


def build_source(key_file: str = None) -> WxLocalSource:
    """按配置开一个收件源。"""
    root = config.WECHAT_FILES_ROOT or wechat_account.default_root()
    account = wechat_account.current_account(root)
    if account is None:
        raise WxSourceError(f"在 {root} 下找不到已登录的微信账号")
    if not account.data_dir:
        raise WxSourceError(f"账号 {account.wxid} 的数据目录不存在（可能还没登录完成）")

    passphrase = load_passphrase(key_file)
    work_root = config.WX_WORK_DIR or default_work_dir()
    work_dir = os.path.join(work_root, account.wxid)

    store = wx_store.WxStore(account.data_dir, passphrase, work_dir, my_wxid=account.wxid)
    try:
        store.refresh()
    except wx_crypto.WrongKey:
        raise WxSourceError(
            f"口令与账号 {account.wxid} 对不上。切换过微信账号后需要重新取一次钥") from None

    src = WxLocalSource(store, backfill_seconds=config.WX_BACKFILL_SECONDS,
                        poll_seconds=config.WX_POLL_SECONDS)
    step = ", ".join(sorted(store._decrypted))
    log.info(f"本地库收件：账号 {account.wxid}，已解密 {step}")
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
