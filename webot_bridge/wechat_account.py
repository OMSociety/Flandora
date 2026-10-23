"""微信登录账号识别，以及「用户切换了账号」的兜底。

微信把当前登录账号落在 `xwechat_files\\all_users\\login\\<wxid>\\key_info.db`，
每次登录都会重写它；账号自己的数据放在 `xwechat_files\\<wxid>_<后缀>\\`。
所以「现在登录的是谁」不必读内存、不必挂钩子，看最新写入的那个 key_info.db
就够——重启微信后也照样成立。

为什么单独有这个模块：本地库解密、联系人映射、机器人身份都只对**当前登录的
那个账号**成立。用户切换账号后这些缓存全部作废；没有兜底的话，bridge 会拿着
上一个账号的联系人映射继续发消息——发得出去，但来自错的身份、投给了错的会话。
本模块负责在切换发生时立刻发现，并把「哪些状态必须重建」讲清楚。

`key_info.db` 只是登录标记，本身不是数据库口令的存放处；新账号的库要能读，
仍须对新账号走一次密钥提取（见文档 `wechat41-ghidra-key-extraction.md`）。
"""

import ctypes
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

log = logging.getLogger("ob11-bridge")

# ============ 路径约定 ============

DATA_DIR_NAME = "xwechat_files"
LOGIN_SUBDIR = os.path.join("all_users", "login")
KEY_INFO = "key_info.db"

# 账号目录名：wxid_xxx，或带随机后缀的 wxid_xxx_3720
_ACCOUNT_DIR_RE = re.compile(r"^(wxid_[0-9A-Za-z]+)(?:_([0-9A-Za-z]+))?$")


def _documents_dir() -> str:
    """当前用户的「文档」目录（解析被重定向到别的盘的情况）。

    直接拼 `~/Documents` 在文档库被移到 D 盘之类的机器上会落空，所以走
    `SHGetFolderPathW` 取系统认定值，失败再退回拼路径。
    """
    try:
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_PERSONAL = 5
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
            if buf.value:
                return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def default_root() -> str:
    """微信数据根目录的默认位置。"""
    return os.path.join(_documents_dir(), DATA_DIR_NAME)


# ============ 账号识别 ============


@dataclass(frozen=True)
class Account:
    """一个登录过的微信账号。"""

    wxid: str
    login_dir: str          # all_users\login\<wxid>
    data_dir: str           # xwechat_files\<wxid>_<后缀>，找不到时为空串
    key_info_mtime: float   # 登录标记的最新写入时间


def _newest_mtime(path: str) -> float:
    """base / -wal / -shm 三个文件里最新的写入时间（0 = 都不存在）。"""
    newest = 0.0
    for suffix in ("", "-wal", "-shm"):
        try:
            newest = max(newest, os.path.getmtime(path + suffix))
        except OSError:
            continue
    return newest


def _data_dir_for(root: str, wxid: str) -> str:
    """账号数据目录：目录名可能带随机后缀，所以按前缀匹配。"""
    exact = os.path.join(root, wxid)
    if os.path.isdir(exact):
        return exact
    try:
        for name in os.listdir(root):
            if name.startswith(wxid + "_"):
                candidate = os.path.join(root, name)
                if os.path.isdir(candidate):
                    return candidate
    except OSError:
        pass
    return ""


def list_accounts(root: Optional[str] = None) -> List[Account]:
    """所有登录过的账号，最近登录的在前。

    判据是登录标记的写入时间，不是「目录是否存在」——旧账号的目录会一直留在
    磁盘上，只看存在与否会认错人。
    """
    root = root or default_root()
    login_root = os.path.join(root, LOGIN_SUBDIR)
    accounts: List[Account] = []
    try:
        names = os.listdir(login_root)
    except OSError:
        return accounts
    for name in names:
        login_dir = os.path.join(login_root, name)
        if not os.path.isdir(login_dir):
            continue
        if not os.path.isfile(os.path.join(login_dir, KEY_INFO)):
            continue
        accounts.append(Account(
            wxid=name,
            login_dir=login_dir,
            data_dir=_data_dir_for(root, name),
            key_info_mtime=_newest_mtime(os.path.join(login_dir, KEY_INFO)),
        ))
    accounts.sort(key=lambda a: a.key_info_mtime, reverse=True)
    return accounts


def current_account(root: Optional[str] = None) -> Optional[Account]:
    """当前登录的账号；识别不出来时返回 None。"""
    accounts = list_accounts(root)
    return accounts[0] if accounts else None


def describe(root: Optional[str] = None) -> str:
    """一行式账号概览，用于启动日志与报错信息。"""
    accounts = list_accounts(root)
    if not accounts:
        return f"未在 {root or default_root()} 下找到任何登录记录"
    lines = []
    for i, acc in enumerate(accounts):
        mark = "当前" if i == 0 else "历史"
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(acc.key_info_mtime))
        lines.append(f"{mark} {acc.wxid}（最后登录 {when}，数据目录 {acc.data_dir or '缺失'}）")
    return "；".join(lines)


# ============ 切换监视 ============

# 新账号识别出来之后，先观察这么久再认定切换完成：微信切换账号会重启界面并
# 重写整套库，登录瞬间的目录状态是半截的，认得太早会误判。
SETTLE_SECONDS = 6.0


class AccountWatcher(threading.Thread):
    """轮询登录账号，变化时回调一次。

    回调参数 `(old, new)`：`old` 可能是 None（启动时就没识别出来）。
    回调里应当重建所有「只对某个账号有效」的状态。异常只记日志，不会让监视
    线程退出——切换兜底本身不能成为新的故障点。
    """

    def __init__(self,
                 root: Optional[str] = None,
                 on_change: Optional[Callable[[Optional[Account], Account], None]] = None,
                 interval: float = 5.0,
                 settle: float = SETTLE_SECONDS):
        super().__init__(daemon=True, name="wx-account-watch")
        self.root = root or default_root()
        self.on_change = on_change
        self.interval = interval
        self.settle = settle
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._account = current_account(self.root)
        self._pending = None      # (wxid, 首次见到的时间)

    @property
    def account(self) -> Optional[Account]:
        with self._lock:
            return self._account

    def stop(self) -> None:
        self._stop.set()

    def _confirmed_change(self, seen: Account) -> bool:
        """新账号是否已稳定到可以认定。"""
        now = time.time()
        if self._pending is None or self._pending[0] != seen.wxid:
            self._pending = (seen.wxid, now)
            return False
        return now - self._pending[1] >= self.settle

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            seen = current_account(self.root)
            if seen is None:
                continue
            with self._lock:
                known = self._account
            if known is not None and seen.wxid == known.wxid:
                self._pending = None
                continue
            if not self._confirmed_change(seen):
                continue
            self._pending = None
            with self._lock:
                self._account = seen
            log.warning(f"[账号] 登录账号变化："
                        f"{known.wxid if known else '（未识别）'} → {seen.wxid}")
            if self.on_change is not None:
                try:
                    self.on_change(known, seen)
                except Exception as e:
                    log.error(f"[账号] 切换回调出错: {e}")
