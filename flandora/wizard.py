"""取钥向导的状态机。

四步，顺序固定：

1. ``disclaimer`` 免责声明——必须显式勾选才放行，勾过之后记在 ``data/wizard.json``
2. ``dbkey``      数据库口令——调起独立的取钥器，在微信重新登录那一刻截获
3. ``imagekey``   图片密钥——在微信实况画面上点开一张没看过的图，后台同步扫内存
4. ``verify``     校验——拿取到的口令逐个库过 page-1 HMAC

为什么非做成向导不可：数据库口令只在微信登录那一瞬间存在，图片密钥只在微信解码
图片的那几秒存在。这两样都无法事后补取，只能把用户手上的动作和后台的动作掐在同
一段时间里。向导就是那个掐表的角色。

状态存在模块级单例上（与 ``wx_desktop.manager()`` 同一个风格）：WebUI 的接口是
无状态的 HTTP 请求，谁都可以随时来问进度，因此状态不能挂在请求上。步骤的完成态
只在内存里；进程重启时按盘上的口令/密钥文件把已完成的两步标回来（见
``Wizard._restore_key_steps``），这样钥匙还在就不会退回「重新取钥」。
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from . import config, wechat_account, wx_crypto, wx_image, wx_image_key, wx_source
from .wxkey.store import KeyStore, KeyStoreError

log = logging.getLogger("flandora")

STEP_DISCLAIMER = "disclaimer"
STEP_DBKEY = "dbkey"
STEP_IMAGEKEY = "imagekey"
STEP_VERIFY = "verify"
STEPS = (STEP_DISCLAIMER, STEP_DBKEY, STEP_IMAGEKEY, STEP_VERIFY)

DISCLAIMER_FILE = "wizard.json"
LOG_MAX = 300

# 等用户重新登录的上限。比取钥器自己的默认值宽：向导里用户还要先看清提示、
# 必要时还得先用实况画面把微信退出登录。
DBKEY_LOGIN_WAIT = 600.0
# 图片密钥的持续扫描上限
IMAGEKEY_SECONDS = 600.0
# 单个库的页 1 校验预算（PBKDF2 256000 轮，约 0.3 秒一个）
VERIFY_TIMEOUT = 20.0


class WizardError(Exception):
    """向导操作不被接受（缺前置条件、已有任务在跑等）。"""


@dataclass
class StepState:
    state: str = "idle"  # idle | running | ok | failed
    message: str = ""
    detail: str = ""
    log: list = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "message": self.message,
            "detail": self.detail,
            "log": list(self.log[-LOG_MAX:]),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_passphrase(text: str) -> list[bytes]:
    """把手填的口令文本变成候选字节串。

    口令是 32 字节的随机串，实际取出来时是十六进制；但用户在别处看到的可能已经是
    字节串本身。长度能有两种解释时两种都给出来，由调用方拿真库逐个试，谁能解开用谁。
    """
    text = (text or "").strip()
    if not text:
        raise WizardError("口令不能为空")
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return [bytes.fromhex(text)]
    if re.fullmatch(r"[0-9a-fA-F]{32}", text):
        return [bytes.fromhex(text), text.encode("ascii")]
    return [text.encode("utf-8", errors="replace")]


def verify_summary(passed: int, total: int, failed: list[str]) -> str:
    """校验结论：只说核对过的事实，不拿失败个数去猜库的种类。

    失败的名字与类型照实列出来，让结论能对上校验日志；按个数推断会出现
    「三个正文库一起解不开也被说成无害」这种与事实相反的结论。
    """
    if total and passed == total:
        return f"{passed}/{total} 个库全部通过"
    if not passed:
        return f"0/{total} 个库通过，口令可能不属于当前账号"
    shown = "、".join(failed) if len(failed) <= 5 else f"{len(failed)} 个（见校验日志）"
    return (
        f"{passed}/{total} 个库通过；未通过：{shown}。"
        "正文库解不开说明口令不对或库不完整，若收件异常请重新取一次口令"
    )


class Wizard:
    """四步向导。所有公开方法都是线程安全的。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._steps = {name: StepState() for name in STEPS}
        self._disclaimer = self._load_disclaimer()
        if self._disclaimer:
            # 进程重启后要接着上次的进度走，所以确认过就得把这一步标成已完成，
            # 否则 current 会一直停回第一步。
            self._steps[STEP_DISCLAIMER] = StepState(state="ok", message="已确认免责声明")
        self._restore_key_steps()

        self._proc: Optional[subprocess.Popen] = None
        self._proc_thread: Optional[threading.Thread] = None
        self._proc_payload: dict = {}

        self._img_stop = threading.Event()
        self._img_thread: Optional[threading.Thread] = None

        self._verify_thread: Optional[threading.Thread] = None

    def _restore_key_steps(self) -> None:
        """按盘上密钥把已完成的两步标回来。

        步骤状态是内存态，口令与图片密钥才是落盘事实。进程重启后如果不做这层
        推导，current 会退回「取数据库口令」，页面看上去就像钥匙丢了要重取一遍。
        密钥属于别的登录账号时不标——向导停回对应那一步，由页面上「账号不一致」
        的警告解释原因。
        """
        db_info = self.dbkey_status()
        if db_info["present"] and (db_info["matches_current"] or not db_info["current_wxid"]):
            self._steps[STEP_DBKEY] = StepState(
                state="ok",
                message="已取到口令（账号 {}，微信 {}）".format(
                    db_info["wxid"] or "未知", db_info["dll_version"] or "未知版本"
                ),
            )
        img_info = self.imagekey_status()
        if img_info["present"] and (img_info["matches_current"] or not img_info["current_wxid"]):
            xor = img_info.get("xor_key")
            suffix = f"（XOR 0x{int(xor):02x}）" if xor is not None else ""
            self._steps[STEP_IMAGEKEY] = StepState(state="ok", message=f"已取到图片密钥{suffix}")

    # ================================================================
    # 路径
    # ================================================================

    def _disclaimer_path(self) -> str:
        return os.path.join(config.data_dir(), DISCLAIMER_FILE)

    def key_path(self) -> str:
        return config.WX_KEY_FILE or wx_source.default_key_file()

    def image_key_path(self) -> str:
        return config.WX_IMAGE_KEY_FILE or wx_source.default_image_key_file()

    # ================================================================
    # 免责声明
    # ================================================================

    def _load_disclaimer(self) -> bool:
        try:
            with open(self._disclaimer_path(), encoding="utf-8") as fh:
                return bool(json.load(fh).get("disclaimer_accepted"))
        except (OSError, ValueError):
            return False

    def _save_disclaimer(self, accepted: bool) -> None:
        path = self._disclaimer_path()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "disclaimer_accepted": accepted,
                    "accepted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )

    def accept_disclaimer(self) -> dict:
        with self._lock:
            self._save_disclaimer(True)
            self._disclaimer = True
            self._steps[STEP_DISCLAIMER] = StepState(
                state="ok", message="已确认免责声明", finished_at=time.time()
            )
        return self.status()

    # ================================================================
    # 状态
    # ================================================================

    def _current_step(self) -> str:
        for name in STEPS:
            if self._steps[name].state != "ok":
                return name
        return "done"

    def dbkey_status(self) -> dict:
        path = self.key_path()
        info = {
            "present": False,
            "path": path,
            "wxid": "",
            "dll_version": "",
            "matches_current": False,
            "current_wxid": "",
            "error": "",
        }
        try:
            stored = KeyStore(path).load()
        except KeyStoreError as exc:
            info["error"] = str(exc)
            return info
        if stored is None:
            return info
        current = wechat_account.current_account(config.WECHAT_FILES_ROOT or None)
        info.update(
            present=True,
            wxid=stored.wxid,
            dll_version=stored.dll_version,
            current_wxid=current.wxid if current else "",
            matches_current=bool(current and current.wxid == stored.wxid),
        )
        return info

    def imagekey_status(self) -> dict:
        path = self.image_key_path()
        info = {
            "present": False,
            "path": path,
            "wxid": "",
            "xor_key": None,
            "matches_current": False,
            "current_wxid": "",
            "works": None,
            "error": "",
        }
        if not os.path.isfile(path):
            return info
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            info["error"] = f"图片密钥文件读不出来：{exc}"
            return info

        current = wechat_account.current_account(config.WECHAT_FILES_ROOT or None)
        info.update(
            present=True,
            wxid=payload.get("wxid", ""),
            xor_key=payload.get("xor_key"),
            current_wxid=current.wxid if current else "",
            matches_current=bool(current and payload.get("wxid") == current.wxid),
        )

        # 只有密钥属于当前账号时才值得试解——别人的密钥解不开是正常的，不算错。
        if info["matches_current"]:
            try:
                attach = wx_image_key.attach_dir(config.WECHAT_FILES_ROOT or None)
                block, _src = wx_image_key.sample_block(attach)
                key = wx_image_key.aes_key_from_payload(payload)
                if not key:
                    info["error"] = (
                        "密钥解不开：它是在别的 Windows 账户或别的机器上取的，重新取一次"
                    )
                    return info
                plain = wx_image.aes_ecb_decrypt(key, block)
                info["works"] = any(plain.startswith(m) for m, _ in wx_image._MAGICS)
            except Exception as exc:
                info["error"] = f"试解本地图片样本失败：{exc}"
        return info

    def keytool_command(self) -> list:
        """取钥器的命令行：打包后在安装目录的 tools\\ 里，源码里是 tools/keytool.py。"""
        if getattr(sys, "frozen", False):
            here = os.path.dirname(os.path.abspath(sys.executable))
            for folder in ("tools", ""):
                base = os.path.join(here, folder) if folder else here
                for name in ("Flandora-KeyTool.exe", "Flandora-KeyTool"):
                    path = os.path.join(base, name)
                    if os.path.isfile(path):
                        return [path]
            raise WizardError(
                "安装目录的 tools\\ 下找不到 Flandora-KeyTool.exe。请确认发布包已完整解压，"
                "或改用下面的「手动填入口令」。"
            )

        script = os.path.join(_repo_root(), "tools", "keytool.py")
        if not os.path.isfile(script):
            raise WizardError(f"找不到取钥器脚本 {script}")
        return [sys.executable, script]

    def status(self) -> dict:
        with self._lock:
            steps = {name: st.as_dict() for name, st in self._steps.items()}
            current = self._current_step()
            proc_alive = self._proc is not None and self._proc.poll() is None
            scanning = self._img_thread is not None and self._img_thread.is_alive()
        return {
            "order": list(STEPS),
            "steps": steps,
            "current": current,
            "disclaimer_accepted": self._disclaimer,
            "db_key": self.dbkey_status(),
            "image_key": self.imagekey_status(),
            "manual_rva": config.WXKEY_MANUAL_RVA,
            "keytool_running": proc_alive,
            "imagekey_running": scanning,
        }

    def reset(self) -> dict:
        """把各步状态清回起点，免责声明的确认保留。"""
        self.cancel_dbkey()
        self.cancel_imagekey()
        with self._lock:
            self._steps = {name: StepState() for name in STEPS}
            if self._disclaimer:
                self._steps[STEP_DISCLAIMER] = StepState(
                    state="ok", message="已确认免责声明", finished_at=time.time()
                )
        return self.status()

    # ================================================================
    # 第二步：数据库口令
    # ================================================================

    def start_dbkey(self) -> dict:
        """调起取钥器。它会在后台等用户在微信里完成一次登录。"""
        with self._lock:
            if not self._disclaimer:
                raise WizardError("请先勾选并确认免责声明")
            if self._proc is not None and self._proc.poll() is None:
                raise WizardError("取钥器已经在运行了")

            cmd = self.keytool_command() + [
                "--out",
                self.key_path(),
                "--json",
                "--login-wait",
                str(DBKEY_LOGIN_WAIT),
            ]
            if config.WXKEY_MANUAL_RVA:
                cmd += ["--rva", hex(config.WXKEY_MANUAL_RVA)]

            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    # 取钥器把 stdout 钉成 UTF-8（见 tools/keytool.py），这里按同一约定解码
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                self._proc = None
                raise WizardError(f"取钥器拉不起来：{exc}") from exc

            self._proc_payload = {}
            started = time.time()
            self._steps[STEP_DBKEY] = StepState(
                state="running",
                message="取钥器已启动，正在定位设置点……",
                log=[f"命令：{' '.join(cmd)}"],
                started_at=started,
            )
            self._proc_thread = threading.Thread(
                target=self._read_keytool, args=(self._proc,), name="wizard-keytool", daemon=True
            )
            self._proc_thread.start()
        return self.status()

    def _read_keytool(self, proc: subprocess.Popen) -> None:
        """逐行读取钥器的 JSON 输出，把进度搬进状态里。"""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                self._dbkey_log(line)
                continue
            if event.get("event") == "status":
                self._dbkey_log(event.get("text", ""))
            else:
                with self._lock:
                    self._proc_payload = event

        code = proc.wait()
        with self._lock:
            payload = dict(self._proc_payload)
            step = self._steps[STEP_DBKEY]
            step.finished_at = time.time()
            if code == 0 and payload.get("ok"):
                step.state = "ok"
                step.message = (
                    f"已取到口令（账号 {payload.get('wxid') or '未知'}，"
                    f"微信 {payload.get('dll_version') or '未知版本'}）"
                )
                step.detail = payload.get("message", "")
                self._dbkey_log("取钥完成，口令已用 DPAPI 加密存好")
            else:
                step.state = "failed"
                step.message = self._dbkey_failure_text(code, payload)
                step.detail = payload.get("detail", "")
                self._dbkey_log(step.message)
            self._proc = None

    @staticmethod
    def _dbkey_failure_text(code: int, payload: dict) -> str:
        reason = payload.get("reason", "")
        if reason == "locate-failed":
            return "定位失败：这次微信版本的结构对不上，可在下面手动填写地址后重试"
        if reason == "wechat-not-running":
            return "微信没有在运行：请先让微信跑起来，再重新登录一次"
        if reason == "no-key-event":
            return "没等到登录事件：口令只在登录那一刻出现，需要真的退出再登录一次"
        if reason == "capture-failed":
            return "截获到了内容但解不开口令，请把微信完全退出后重新登录再试"
        if reason == "save-failed":
            return "口令取到了但存不下（DPAPI 或磁盘问题），请查看日志"
        if code == 2:
            return "没取到口令：多半是没有真正退出并重新登录"
        if code == 3:
            return "口令取到了但存不下（DPAPI 或磁盘问题）"
        return f"取钥器异常退出（退出码 {code}）"

    def _dbkey_log(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            step = self._steps[STEP_DBKEY]
            step.log.append(text)
            if step.state == "running" and not text.startswith("命令："):
                step.message = text

    def cancel_dbkey(self) -> dict:
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._dbkey_log("已取消")
            with self._lock:
                step = self._steps[STEP_DBKEY]
                if step.state == "running":
                    step.state = "failed"
                    step.message = "已取消"
                    step.finished_at = time.time()
                self._proc = None
        return self.status()

    def save_manual_passphrase(self, text: str) -> dict:
        """手动填入口令：先拿真库验一遍，验过了才落盘。"""
        candidates = parse_passphrase(text)
        current = wechat_account.current_account(config.WECHAT_FILES_ROOT or None)
        if current is None or not current.data_dir:
            raise WizardError("找不到已登录的微信账号，请先登录微信")
        db_path = os.path.join(current.data_dir, "db_storage", "message", "message_0.db")
        if not os.path.isfile(db_path):
            raise WizardError(f"找不到用于校验的数据库 {db_path}")

        with open(db_path, "rb") as fh:
            page1 = fh.read(wx_crypto.PAGE)

        for candidate in candidates:
            if wx_crypto.check_page1(candidate, page1):
                KeyStore(self.key_path()).save(candidate, wxid=current.wxid, dll_version="手动填入")
                with self._lock:
                    self._steps[STEP_DBKEY] = StepState(
                        state="ok",
                        message=f"已用手填口令通过校验（账号 {current.wxid}）",
                        log=["口令是你自己填的，未经过取钥器"],
                        finished_at=time.time(),
                    )
                return self.status()
        raise WizardError("这个口令解不开当前账号的数据库，请核对后重填")

    # ================================================================
    # 第三步：图片密钥
    # ================================================================

    def start_imagekey(self) -> dict:
        with self._lock:
            if not self._disclaimer:
                raise WizardError("请先勾选并确认免责声明")
            if not self.dbkey_status()["present"]:
                raise WizardError("请先取到数据库口令，再取图片密钥")
            if self._img_thread is not None and self._img_thread.is_alive():
                raise WizardError("图片密钥扫描已经在跑了")
            self._img_stop = threading.Event()
            self._steps[STEP_IMAGEKEY] = StepState(
                state="running",
                message="扫描已开始——请在「微信画面」里点开一张没看过的图片",
                started_at=time.time(),
            )
            self._img_thread = threading.Thread(
                target=self._scan_imagekey, name="wizard-imagekey", daemon=True
            )
            self._img_thread.start()
        return self.status()

    def _scan_imagekey(self) -> None:
        try:
            payload = wx_image_key.grab_key(
                out_path=self.image_key_path(),
                seconds=IMAGEKEY_SECONDS,
                root=config.WECHAT_FILES_ROOT or None,
                stop=self._img_stop,
                on_status=self._imagekey_log,
                on_round=lambda n: self._imagekey_progress(n),
            )
        except wx_image_key.ImageKeyError as exc:
            self._finish_imagekey("failed", str(exc))
        except Exception as exc:  # noqa: BLE001 兜住线程里的意外
            log.exception("取图片密钥时出错")
            self._finish_imagekey("failed", f"取图片密钥时出错：{exc}")
        else:
            self._finish_imagekey("ok", f"已取到图片密钥（XOR 0x{payload.get('xor_key', 0):02x}）")

    def _imagekey_log(self, text: str) -> None:
        with self._lock:
            step = self._steps[STEP_IMAGEKEY]
            step.log.append(text)
            if step.state == "running":
                step.message = text

    def _imagekey_progress(self, rounds: int) -> None:
        with self._lock:
            step = self._steps[STEP_IMAGEKEY]
            if step.state == "running":
                step.message = f"已扫 {rounds} 轮——请在「微信画面」里点开一张没看过的图片"

    def _finish_imagekey(self, state: str, message: str) -> None:
        with self._lock:
            step = self._steps[STEP_IMAGEKEY]
            if step.state == "running" or state == "ok":
                step.state = state
                step.message = message
                step.finished_at = time.time()

    def cancel_imagekey(self) -> dict:
        with self._lock:
            alive = self._img_thread is not None and self._img_thread.is_alive()
        if alive:
            self._img_stop.set()
            self._imagekey_log("已请求停止")
        return self.status()

    # ================================================================
    # 第四步：校验
    # ================================================================

    def account_db_paths(self) -> list:
        """当前账号的全部库。"""
        current = wechat_account.current_account(config.WECHAT_FILES_ROOT or None)
        if current is None or not current.data_dir:
            return []
        return sorted(
            glob.glob(os.path.join(current.data_dir, "db_storage", "**", "*.db"), recursive=True)
        )

    def verify(self) -> dict:
        """用当前口令逐个库过页 1 HMAC。跑在后台线程里，回来问状态即可。"""
        with self._lock:
            if self._verify_thread is not None and self._verify_thread.is_alive():
                raise WizardError("校验已经在跑了")
            self._steps[STEP_VERIFY] = StepState(
                state="running", message="正在逐个库校验……", started_at=time.time()
            )
            self._verify_thread = threading.Thread(
                target=self._run_verify, name="wizard-verify", daemon=True
            )
            self._verify_thread.start()
        return self.status()

    def _run_verify(self) -> None:
        if not self.dbkey_status()["present"]:
            self._finish_verify("failed", "还没有可用口令，请先完成第二步")
            return
        try:
            stored = KeyStore(self.key_path()).load()
        except KeyStoreError as exc:
            self._finish_verify("failed", str(exc))
            return
        if stored is None:
            self._finish_verify("failed", "口令文件为空")
            return

        dbs = self.account_db_paths()
        if not dbs:
            self._finish_verify("failed", "找不到当前账号的数据库——请先登录微信，再回来校验")
            return

        passed, failed = 0, []
        for index, path in enumerate(dbs, 1):
            name = os.path.relpath(path, os.path.dirname(dbs[0]))
            ok = False
            try:
                with open(path, "rb") as fh:
                    page1 = fh.read(wx_crypto.PAGE)
                if len(page1) < wx_crypto.PAGE:
                    raise ValueError(f"库太小（{len(page1)} 字节）")
                ok = wx_crypto.check_page1(stored.passphrase, page1)
            except Exception as exc:  # noqa: BLE001
                log.debug("校验 %s 时出错：%s", path, exc)
            if ok:
                passed += 1
            else:
                failed.append(name)
            self._verify_log(f"[{index}/{len(dbs)}] {name} {'通过' if ok else '未通过'}")

        total = len(dbs)
        state = "ok" if passed == total else "failed"
        self._finish_verify(state, verify_summary(passed, total, failed))

    def _verify_log(self, text: str) -> None:
        with self._lock:
            step = self._steps[STEP_VERIFY]
            step.log.append(text)
            if step.state == "running":
                step.message = text

    def _finish_verify(self, state: str, message: str) -> None:
        with self._lock:
            step = self._steps[STEP_VERIFY]
            step.state = state
            step.message = message
            step.finished_at = time.time()


_wizard: Optional[Wizard] = None
_wizard_lock = threading.Lock()


def wizard() -> Wizard:
    """取当前进程的向导单例。"""
    global _wizard
    with _wizard_lock:
        if _wizard is None:
            _wizard = Wizard()
        return _wizard
