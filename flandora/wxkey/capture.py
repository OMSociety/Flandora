"""在运行中的微信进程里截获数据库口令。

做法：定位"口令设置函数"，在它入口处取调用方传进来的密钥缓冲区（连同它前面几个
指针槽），把指针解引用成原始字节，再交给 :mod:`wxkey.recover` 用数据库页 1 的
HMAC 判定。全程只读内存，不写入、不改变微信行为，也不改动任何数据库文件。

口令只在密钥派生的那一瞬间存在于内存，数据库密钥本身从不以明文常驻，所以这一步
必须在微信调用它时现场截获——事后扫描内存是找不到的。微信已登录且连接池常开时
不会再调用，因此正常流程是"退出微信 → 开始取钥 → 重新登录"。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .. import wechat_account
from . import locator, recover

log = logging.getLogger("wxkey.capture")

#: 已经登录的微信身上直接尝试的时长。连接池常开时通常等不到，够短就好。
QUICK_TIMEOUT = 20.0
#: "等微信重新登录"流程里，微信出现的等待上限。
LOGIN_WAIT = 300.0
#: 登录流程里出现之后的截获时长。
LOGIN_CAPTURE = 180.0
#: 密钥缓冲区前面这几个指针槽一起看，口令所在的缓冲区常常由其中之一指过去。
KEY_SLOTS = 8
BEFORE_BYTES = 0x40
AT_BYTES = 0x200
DEREF_BYTES = 0x200
#: 每个候选库的判定时间上限。命中通常在头几个候选，慢是常态不是异常。
PER_TARGET_BUDGET = 600.0
#: 拿到第一轮片段后再等一小会儿，让同一轮里其余槽位也落袋。
SETTLE = 0.8
POLL = 0.5


class KeyCaptureError(Exception):
    """取钥失败。消息直接面向终端用户，避免术语。"""


class WeChatNotRunning(KeyCaptureError):
    """微信没在运行。"""


class HookPointUnavailable(KeyCaptureError):
    """没能在微信进程里找到挂钩位置。"""


class NoKeyEvent(KeyCaptureError):
    """等不到口令设置调用。"""


_JS_MODULE_BASE = """
const mod = Process.findModuleByName('Weixin.dll');
send({ base: mod ? mod.base.toString() : null });
"""

_JS_TEMPLATE = """
const target = ptr('{base}').add({rva});
const BEFORE = {before};
const AT = {at};
const DEREF = {deref};
const SLOTS = {slots};
let calls = 0;

function grab(tag, address, size) {{
  try {{
    if (address.isNull()) return;
    const bytes = address.readByteArray(size);
    if (!bytes) return;
    const view = new Uint8Array(bytes);
    let hex = '';
    for (let i = 0; i < view.length; i++) {{
      hex += (view[i] < 16 ? '0' : '') + view[i].toString(16);
    }}
    send({{ type: 'blob', tag: tag, hex: hex }});
  }} catch (e) {{}}
}}

Interceptor.attach(target, {{
  onEnter: function (args) {{
    calls += 1;
    send({{ type: 'call', index: calls }});
    const keyData = args[1];
    if (keyData.isNull()) return;
    grab('setkey.before', keyData.sub(BEFORE), BEFORE);
    grab('setkey.at', keyData, AT);
    for (let off = 0; off < SLOTS * 8; off += 8) {{
      try {{
        const p = keyData.add(off).readPointer();
        if (!p.isNull()) grab('setkey.deref' + off, p, DEREF);
      }} catch (e) {{}}
    }}
  }}
}});

send({{ type: 'ready', target: target.toString() }});
"""


@dataclass(frozen=True)
class CaptureInfo:
    """一次取钥的完整取证信息。"""

    passphrase: bytes
    wxid: str
    db_path: str
    blobs: int
    calls: int
    pids: tuple[int, ...]
    dll_version: str
    dll_sha256: str
    seconds: float

    def describe(self) -> str:
        return (
            f"账号 {self.wxid} 的口令已取到（{len(self.passphrase)} 字节），"
            f"用 {os.path.basename(self.db_path)} 校验通过；"
            f"微信 {self.dll_version}，截获 {self.blobs} 段片段、{self.calls} 次调用，"
            f"耗时 {self.seconds:.1f} 秒"
        )


def _frida():
    try:
        import frida
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise HookPointUnavailable(
            "缺少 frida 组件，无法取密钥。请用带 frida 的完整安装包运行。"
        ) from exc
    return frida


def _weixin_pids() -> list[int]:
    frida = _frida()
    # frida 17 起进程枚举挪到了设备对象上，模块级的 enumerate_processes 已移除。
    device = getattr(frida, "get_local_device", lambda: None)()
    source = device if device is not None else frida
    return [p.pid for p in source.enumerate_processes() if p.name.lower() == "weixin.exe"]


def _module_base(pid: int) -> Optional[int]:
    """该进程里 Weixin.dll 的加载基址，没加载就返回 None。"""
    frida = _frida()
    session = None
    try:
        session = frida.attach(pid)
        holder: dict = {}
        done = threading.Event()

        def on_message(message, _data):
            if message.get("type") == "send":
                holder.update(message.get("payload") or {})
            done.set()

        script = session.create_script(_JS_MODULE_BASE)
        script.on("message", on_message)
        script.load()
        done.wait(8.0)
        raw = holder.get("base")
        return int(raw, 16) if raw else None
    except Exception as exc:  # 进程可能刚好退出，或不允许注入
        log.debug("探测 pid=%d 失败：%s", pid, exc)
        return None
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


class _Collector:
    """把钩子挂到一批进程上，收齐片段。"""

    def __init__(self, hook: locator.HookPoint) -> None:
        self.hook = hook
        self.blobs: list[tuple[str, bytes]] = []
        self.attached: dict[int, int] = {}
        self.calls = 0
        self.first_at = 0.0
        self._seen: set[tuple[str, str]] = set()
        self._sessions: list[object] = []
        self._lock = threading.Lock()

    def attach(self, pid: int, base: int) -> bool:
        frida = _frida()
        try:
            session = frida.attach(pid)
            script = session.create_script(
                _JS_TEMPLATE.format(
                    base=hex(base),
                    rva=self.hook.rva,
                    before=BEFORE_BYTES,
                    at=AT_BYTES,
                    deref=DEREF_BYTES,
                    slots=KEY_SLOTS,
                )
            )
            script.on("message", self._on_message)
            script.load()
        except Exception as exc:
            log.debug("挂钩 pid=%d 失败：%s", pid, exc)
            return False
        with self._lock:
            self._sessions.append(session)
            self.attached[pid] = base
        log.info("已挂上钩子：pid=%d 基址 %#x", pid, base)
        return True

    def _on_message(self, message, _data) -> None:
        if message.get("type") != "send":
            return
        payload = message.get("payload") or {}
        kind = payload.get("type")
        if kind == "call":
            with self._lock:
                self.calls = max(self.calls, int(payload.get("index", 0)))
        elif kind == "blob":
            tag = payload.get("tag", "?")
            hexed = payload.get("hex", "")
            if not hexed:
                return
            with self._lock:
                if (tag, hexed) in self._seen:
                    return
                self._seen.add((tag, hexed))
                self.blobs.append((tag, bytes.fromhex(hexed)))
                if not self.first_at:
                    self.first_at = time.time()

    def settled(self) -> bool:
        if not self.blobs:
            return False
        return time.time() - self.first_at >= SETTLE

    def close(self) -> None:
        for session in self._sessions:
            try:
                session.detach()
            except Exception:
                pass
        self._sessions.clear()


def _pump(
    collector: _Collector,
    timeout_s: float,
    *,
    require_start: bool = False,
    on_status: Optional[Callable[[str], None]] = None,
) -> None:
    """把新出现的微信进程陆续挂上，直到收到片段或超时。"""
    deadline = time.time() + timeout_s
    tried: set[int] = set()
    started = bool(_weixin_pids())
    announced = False
    while time.time() < deadline:
        if collector.settled():
            return
        pids = _weixin_pids()
        if require_start and not started:
            if pids:
                started = True
                log.info("微信已启动，开始挂钩")
        if started:
            for pid in pids:
                if pid in tried:
                    continue
                tried.add(pid)
                base = _module_base(pid)
                if base is not None:
                    collector.attach(pid, base)
            attached = len(collector.attached)
            if attached and on_status and attached != announced:
                announced = attached
                on_status(f"正在等待微信打开数据库（{attached} 个进程已挂钩）")
        time.sleep(POLL)
    if not collector.blobs:
        raise NoKeyEvent("等不到微信打开数据库。" if started else "等不到微信启动。")


def capture_live(
    hook: locator.HookPoint,
    *,
    timeout_s: float = QUICK_TIMEOUT,
    out_path: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[list[tuple[str, bytes]], int, list[int]]:
    """在已经登录的微信身上直接试一次。"""
    if not _weixin_pids():
        raise WeChatNotRunning("微信没有在运行。请先登录微信，再取密钥。")
    collector = _Collector(hook)
    try:
        _pump(collector, timeout_s, on_status=on_status)
    finally:
        collector.close()
    log.info("截获 %d 段片段，覆盖 %d 次调用", len(collector.blobs), collector.calls)
    if out_path:
        dump_jsonl(out_path, collector.blobs, hook)
    return collector.blobs, collector.calls, list(collector.attached)


def capture_login(
    hook: locator.HookPoint,
    *,
    login_wait_s: float = LOGIN_WAIT,
    timeout_s: float = LOGIN_CAPTURE,
    out_path: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[list[tuple[str, bytes]], int, list[int]]:
    """等微信重新登录并在登录过程中截获。"""
    collector = _Collector(hook)
    try:
        _pump(collector, login_wait_s + timeout_s, require_start=True, on_status=on_status)
    finally:
        collector.close()
    log.info("截获 %d 段片段，覆盖 %d 次调用", len(collector.blobs), collector.calls)
    if out_path:
        dump_jsonl(out_path, collector.blobs, hook)
    return collector.blobs, collector.calls, list(collector.attached)


def dump_jsonl(out_path: str, blobs: Sequence[tuple[str, bytes]], hook: locator.HookPoint) -> None:
    """把片段落成 JSONL，便于事后复算或提交问题报告。"""
    import json

    with open(out_path, "w", encoding="utf-8") as fh:
        meta = {
            "type": "meta",
            "rva": hex(hook.rva),
            "version": hook.file_version,
            "dll_sha256": hook.dll_sha256,
        }
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for tag, data in blobs:
            fh.write(
                json.dumps({"type": "blob", "tag": tag, "hex": data.hex()}, ensure_ascii=False)
                + "\n"
            )


def _account_db_paths(root: Optional[str]) -> list[tuple[str, str]]:
    """候选校验库，最近登录的账号排在前面。

    取钥期间用户会退出并重新登录，登录的账号不一定是开始时那个；开始前就定死
    一个库，就会拿错账号的库去判 HMAC，所有候选都被误判为未命中。所以校验目标
    在截获结束之后才取，并把其余账号作为后备。
    """
    targets: list[tuple[str, str]] = []
    for account in wechat_account.list_accounts(root):
        if not account.data_dir:
            continue
        db_path = os.path.join(account.data_dir, "db_storage", "message", "message_0.db")
        if os.path.isfile(db_path):
            targets.append((account.wxid, db_path))
    if not targets:
        raise KeyCaptureError("没找到已登录的微信账号的数据目录。请先登录微信，再试一次。")
    return targets


def _finish(
    blobs: Sequence[tuple[str, bytes]],
    calls: int,
    pids: Sequence[int],
    hook: locator.HookPoint,
    *,
    targets: Sequence[tuple[str, str]],
    started: float,
    budget_s: float = PER_TARGET_BUDGET,
) -> CaptureInfo:
    """逐个账号判 HMAC。最近登录的那个先试，命中即止。"""
    for index, (wxid, db_path) in enumerate(targets):
        left = budget_s * (len(targets) - index)
        hit = recover.find_passphrase(blobs, db_path, deadline_s=left)
        if hit is None:
            log.info("片段解不开账号 %s 的库，继续试下一个", wxid)
            continue
        info = CaptureInfo(
            passphrase=hit.passphrase,
            wxid=wxid,
            db_path=db_path,
            blobs=len(blobs),
            calls=calls,
            pids=tuple(pids),
            dll_version=hook.file_version,
            dll_sha256=hook.dll_sha256,
            seconds=time.time() - started,
        )
        log.info(info.describe())
        return info
    raise KeyCaptureError(
        "截获到了内存片段，但没有任何一段能解开已知账号的数据库。"
        "请把微信完全退出后重新登录，登录完成立刻再试一次。"
    )


def capture_and_recover(
    *,
    quick_s: float = QUICK_TIMEOUT,
    login_wait_s: float = LOGIN_WAIT,
    login_capture_s: float = LOGIN_CAPTURE,
    out_path: Optional[str] = None,
    root: Optional[str] = None,
    manual_rva: int = 0,
    on_status: Optional[Callable[[str], None]] = None,
) -> CaptureInfo:
    """取钥主流程：认账号 → 挂钩截获 → 还原 → 用真实数据库校验。

    微信已在运行时先短试一次；等不到就转入"等重新登录"的流程。

    校验库在截获结束之后才取：这个过程要求用户退出并重新登录，登进来的可能是
    另一个账号（实测就发生过），提前定死的库会把正确口令判成未命中。

    manual_rva 非 0 时按它挂钩，不跑签名扫描（自动定位失手时的逃生口）。
    """
    hook = locator.resolve(manual_rva=manual_rva)
    started = time.time()

    pids: list[int] = []
    try:
        blobs, calls, pids = capture_live(
            hook, timeout_s=quick_s, out_path=out_path, on_status=on_status
        )
    except (WeChatNotRunning, NoKeyEvent):
        blobs, calls = [], 0
    if blobs:
        return _finish(blobs, calls, pids, hook, targets=_account_db_paths(root), started=started)

    if on_status:
        on_status("请在微信里退出登录，然后重新登录；登录完成会自动继续。")
    blobs, calls, pids = capture_login(
        hook,
        login_wait_s=login_wait_s,
        timeout_s=login_capture_s,
        out_path=out_path,
        on_status=on_status,
    )
    return _finish(blobs, calls, pids, hook, targets=_account_db_paths(root), started=started)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="从微信里取数据库口令")
    parser.add_argument(
        "--live", action="store_true", help="只在当前运行的微信上试一次，不等重新登录"
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="等待上限（--live 时为截获时长，否则为等登录的时长）",
    )
    parser.add_argument("--capture-out", default=None, help="把截获片段另存为 JSONL，便于复算")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.live:
            hook = locator.resolve()
            started = time.time()
            blobs, calls, pids = capture_live(
                hook,
                timeout_s=args.seconds or QUICK_TIMEOUT,
                out_path=args.capture_out,
                on_status=print,
            )
            info = _finish(
                blobs, calls, pids, hook, targets=_account_db_paths(None), started=started
            )
        else:
            info = capture_and_recover(
                quick_s=args.seconds or QUICK_TIMEOUT, out_path=args.capture_out, on_status=print
            )
    except KeyCaptureError as exc:
        print(f"取密钥失败：{exc}")
        return 1
    print(info.describe())
    return 0


__all__ = [
    "CaptureInfo",
    "HookPointUnavailable",
    "KeyCaptureError",
    "NoKeyEvent",
    "WeChatNotRunning",
    "capture_and_recover",
    "capture_live",
    "capture_login",
    "dump_jsonl",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
