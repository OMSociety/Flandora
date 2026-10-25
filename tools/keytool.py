"""取钥器：独立小程序的入口。

职责只有一件——在微信登录的那一刻截获数据库口令，用 DPAPI 加密存到指定路径，
然后退出。它不常驻、不读写微信数据、不碰 bridge 的任何状态，所以向导可以在
用户退出登录之前把它拉起来，取完就结束。

为什么必须是独立进程：口令只在登录瞬间存在，取钥要长时间挂着 Frida 钩子（等
用户退出并重新登录，可能好几分钟）。放进 bridge 主进程会拖住整个收件的启动。

用法（向导调起时用的就是这条）：
    Flandora-KeyTool.exe --out <口令文件路径> --capture-out <取证jsonl> --json

--out 省略时写到程序目录下的 data/wx_key.dat（打包 = exe 同级 data/，源码 = 仓库根
data/），与 bridge 读写口令的位置一致。

--rva 是自动定位失手时的逃生口：微信改版后签名扫描可能找不到口令设置函数，这时
手动给出地址即可继续（不必正好是函数开头，落在函数里就行）。

退出码：
    0  取到并已保存
    2  没取到（用户没重新登录、或当前微信版本不支持定位）
    3  取到了但存不下（DPAPI 或磁盘问题）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Callable, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from flandora.wxkey import capture, locator  # noqa: E402
from flandora.wxkey.store import KeyStore, KeyStoreError  # noqa: E402

log = logging.getLogger("wxkey.tool")

EXIT_OK = 0
EXIT_NOT_CAPTURED = 2
EXIT_CANNOT_SAVE = 3


def _default_out_path(filename: str) -> str:
    """默认落盘位置：数据目录下的 ``filename``，规则与配置模块一致。"""
    from flandora.config.paths import default_data_dir

    return os.path.join(default_data_dir(), filename)


def _parse_rva(text: str) -> int:
    """接受 0x6148a0 与 6375584 两种写法。"""
    try:
        return int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"地址要写成 0x6148a0 这样：{text}") from exc


class Reporter:
    """把进度同时打到 stderr 与 JSON 行，供向导实时读取。

    stderr 给人看；stdout 的 ``--json`` 模式给程序读。两者分开，避免把日志混进
    机器可解析的流里。
    """

    def __init__(self, as_json: bool) -> None:
        self.as_json = as_json

    def status(self, text: str) -> None:
        if self.as_json:
            self._emit({"event": "status", "text": text})
        print(text, file=sys.stderr, flush=True)

    def done(self, payload: dict) -> None:
        if self.as_json:
            self._emit(payload)
        else:
            print(json.dumps(payload, ensure_ascii=False))

    def _emit(self, payload: dict) -> None:
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def run(
    out_path: str,
    *,
    capture_out: Optional[str] = None,
    login_wait_s: float = capture.LOGIN_WAIT,
    quick_s: float = 0.0,
    manual_rva: int = 0,
    report: Optional[Callable[[str], None]] = None,
) -> tuple[int, dict]:
    """取钥主流程。返回 (退出码, 结果字典)。"""
    say = report or (lambda _t: None)
    started = time.time()

    if manual_rva:
        say(f"按手动指定的地址 {manual_rva:#x} 定位……")
    else:
        say("正在定位微信数据库口令的设置点……")
    try:
        hook = locator.resolve(manual_rva=manual_rva)
    except locator.LocatorError as exc:
        return EXIT_NOT_CAPTURED, {"ok": False, "reason": "locate-failed", "detail": str(exc)}
    say(f"已定位（微信 {hook.file_version or '未知版本'}，函数 RVA {hook.rva:#x}）。")
    say("现在请在微信里退出登录，然后重新登录。登录完成会自动继续。")

    try:
        # quick_s=0 表示不先试「已登录的微信」——那只会在连接池常开时干等。
        info = capture.capture_and_recover(
            quick_s=quick_s or 0.001,
            login_wait_s=login_wait_s,
            out_path=capture_out,
            manual_rva=manual_rva,
            on_status=say,
        )
    except capture.WeChatNotRunning as exc:
        return EXIT_NOT_CAPTURED, {"ok": False, "reason": "wechat-not-running", "detail": str(exc)}
    except capture.NoKeyEvent as exc:
        return EXIT_NOT_CAPTURED, {"ok": False, "reason": "no-key-event", "detail": str(exc)}
    except capture.KeyCaptureError as exc:
        return EXIT_NOT_CAPTURED, {"ok": False, "reason": "capture-failed", "detail": str(exc)}

    say("已取到口令，正在校验并加密保存……")
    try:
        store = KeyStore(out_path)
        store.save(info.passphrase, wxid=info.wxid, dll_version=info.dll_version)
    except KeyStoreError as exc:
        return EXIT_CANNOT_SAVE, {
            "ok": False,
            "reason": "save-failed",
            "detail": str(exc),
            "wxid": info.wxid,
        }

    return EXIT_OK, {
        "ok": True,
        "wxid": info.wxid,
        "dll_version": info.dll_version,
        "rva": hex(hook.rva),
        "blobs": info.blobs,
        "calls": info.calls,
        "seconds": round(time.time() - started, 1),
        "out": out_path,
        "message": info.describe(),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从微信里取数据库口令并加密保存")
    parser.add_argument(
        "--out",
        default=_default_out_path("wx_key.dat"),
        help="口令文件路径（DPAPI 加密，仅当前用户可解）；默认 data/wx_key.dat",
    )
    parser.add_argument(
        "--capture-out", default=None, help="把截获的内存片段另存为 JSONL，便于排查问题"
    )
    parser.add_argument(
        "--login-wait", type=float, default=capture.LOGIN_WAIT, help="等待重新登录的秒数上限"
    )
    parser.add_argument(
        "--rva",
        type=_parse_rva,
        default=0,
        help="手动指定口令设置函数的地址（如 0x6148a0）；省略则自动定位",
    )
    parser.add_argument("--json", action="store_true", help="用 JSON 行输出进度与结果，供界面读取")
    parser.add_argument("--verbose", action="store_true", help="打印调试日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    reporter = Reporter(args.json)
    code, payload = run(
        args.out,
        capture_out=args.capture_out,
        login_wait_s=args.login_wait,
        manual_rva=args.rva,
        report=reporter.status,
    )
    reporter.done(payload)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
