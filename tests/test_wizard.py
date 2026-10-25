"""验证：取钥向导的状态机与口令文本解析。

不连真微信、不跑 Frida——这里要验的是那些能静态判定的部分：四步的顺序约束、
免责声明确认的持久化、手填口令的候选解析、以及打包/源码两种形态下取钥器的
命令行长什么样。

可写数据目录被临时指到空目录，所以不会碰真实环境里的口令与向导记录。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import config  # noqa: E402
from flandora import wizard as wizard_mod


@contextlib.contextmanager
def temp_data_dir():
    """把 data_dir() 临时指到一个空目录。"""
    original = config.CONFIG_FILE
    with tempfile.TemporaryDirectory(prefix="wbb-wizard-") as tmp:
        config.CONFIG_FILE = os.path.join(tmp, "config.json")
        try:
            yield tmp
        finally:
            config.CONFIG_FILE = original


def main() -> int:
    checks = 0

    # ===== 手填口令的文本解析 =====
    hex64 = "62d08ee9343a4fc5a2ad910e0ec7aa221dd7df66231f46c89ef4c7f90771f4e7"
    got = wizard_mod.parse_passphrase(hex64)
    assert got == [bytes.fromhex(hex64)], got
    assert len(got[0]) == 32, len(got[0])

    got = wizard_mod.parse_passphrase("0123456789abcdef0123456789abcdef")
    assert len(got) == 2, got
    assert got[0] == bytes.fromhex("0123456789abcdef0123456789abcdef")
    assert got[1] == b"0123456789abcdef0123456789abcdef"
    assert len(got[0]) == 16 and len(got[1]) == 32, (len(got[0]), len(got[1]))

    got = wizard_mod.parse_passphrase("  我的口令  ")
    assert got == ["我的口令".encode("utf-8")], got

    try:
        wizard_mod.parse_passphrase("   ")
    except wizard_mod.WizardError:
        pass
    else:
        raise AssertionError("空口令没有被拒")
    print("  ok   口令解析：64 位十六进制 / 32 位两可 / 普通文本 / 空值被拒")
    checks += 1

    with temp_data_dir() as tmp:
        # ===== 初始状态 =====
        wiz = wizard_mod.Wizard()
        st = wiz.status()
        assert st["current"] == wizard_mod.STEP_DISCLAIMER, st["current"]
        assert st["disclaimer_accepted"] is False
        assert [s["state"] for s in st["steps"].values()] == ["idle"] * 4, st["steps"]
        assert st["order"] == list(wizard_mod.STEPS)
        for key in ("db_key", "image_key", "manual_rva", "keytool_running", "imagekey_running"):
            assert key in st, key
        print("  ok   初始状态：四步全 idle，当前步骤停在免责声明")
        checks += 1

        # ===== 没勾免责声明就不能往下走 =====
        for label, call in (("取数据库口令", wiz.start_dbkey), ("取图片密钥", wiz.start_imagekey)):
            try:
                call()
            except wizard_mod.WizardError:
                pass
            else:
                raise AssertionError(f"没勾免责声明就允许{label}")
        print("  ok   未确认免责声明时，取数据库口令与取图片密钥都被拒")
        checks += 1

        # ===== 确认免责声明 =====
        st = wiz.accept_disclaimer()
        assert st["disclaimer_accepted"] is True
        assert st["steps"][wizard_mod.STEP_DISCLAIMER]["state"] == "ok"
        assert st["current"] == wizard_mod.STEP_DBKEY, st["current"]
        path = os.path.join(tmp, "wizard.json")
        assert os.path.isfile(path), path
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert saved["disclaimer_accepted"] is True, saved
        assert "accepted_at" in saved, saved
        print("  ok   确认免责声明：落到 data/wizard.json，当前步骤前进到第二步")
        checks += 1

        # ===== 确认过就记住（新实例直接越过第一步） =====
        assert wizard_mod.Wizard().status()["current"] == wizard_mod.STEP_DBKEY
        print("  ok   免责声明确认是持久的：新实例不再要求重勾")
        checks += 1

        # ===== 没有数据库口令就不能取图片密钥 =====
        assert wiz.status()["db_key"]["present"] is False
        try:
            wiz.start_imagekey()
        except wizard_mod.WizardError:
            pass
        else:
            raise AssertionError("没有数据库口令就允许取图片密钥")
        print("  ok   第三步有前置条件：数据库口令没到手时被拒")
        checks += 1

        # ===== reset 保留免责声明确认 =====
        st = wiz.reset()
        assert st["disclaimer_accepted"] is True
        assert st["steps"][wizard_mod.STEP_DISCLAIMER]["state"] == "ok"
        assert st["steps"][wizard_mod.STEP_DBKEY]["state"] == "idle"
        assert st["current"] == wizard_mod.STEP_DBKEY
        print("  ok   reset 清空后四步重来，免责声明确认保留")
        checks += 1

        # ===== 取钥器的命令行 =====
        cmd = wiz.keytool_command()
        assert len(cmd) == 2 and cmd[0] == sys.executable, cmd
        assert cmd[1].endswith(os.path.join("tools", "keytool.py")), cmd
        assert os.path.isfile(cmd[1]), cmd
        print("  ok   源码形态下取钥器命令行指向 tools/keytool.py")
        checks += 1

        # ===== 口令文件与图片密钥文件的落点 =====
        assert wiz.key_path() == os.path.join(tmp, "wx_key.dat"), wiz.key_path()
        assert wiz.image_key_path() == os.path.join(tmp, "wx_image_key.json"), wiz.image_key_path()
        print("  ok   口令与图片密钥默认落在配置同目录（data/）")
        checks += 1

        # ===== 库清单 =====
        dbs = wiz.account_db_paths()
        assert isinstance(dbs, list), type(dbs)
        assert all(p.endswith(".db") for p in dbs), dbs
        print(f"  ok   库清单返回 {len(dbs)} 个 .db（未登录微信时为 0，不算失败）")
        checks += 1

        # ===== 没有口令时校验直接判失败，不去瞎算 =====
        wiz.verify()
        for _ in range(50):
            step = wiz.status()["steps"][wizard_mod.STEP_VERIFY]
            if step["state"] != "running":
                break
            import time

            time.sleep(0.1)
        assert step["state"] == "failed", step
        assert "口令" in step["message"], step
        print("  ok   未取到口令时，校验步骤明确判失败并说清原因")
        checks += 1

        # ===== 重启推导：密钥在盘上，新实例直接把已完成的两步标回来 =====
        import types

        from flandora.wxkey.store import KeyStore

        original_current = wizard_mod.wechat_account.current_account
        KeyStore(wiz.key_path()).save(bytes(range(32)), wxid="wxid_restart", dll_version="9.9.9")
        with open(wiz.image_key_path(), "w", encoding="utf-8") as fh:
            json.dump({"wxid": "wxid_restart", "xor_key": 17, "aes_key": "00" * 32}, fh)
        try:
            # 密钥账号与当前登录的一致：前两步直接标回已完成，停在校验
            wizard_mod.wechat_account.current_account = lambda root=None: types.SimpleNamespace(
                wxid="wxid_restart", data_dir=""
            )
            st = wizard_mod.Wizard().status()
            assert st["steps"][wizard_mod.STEP_DBKEY]["state"] == "ok", st["steps"]
            assert st["steps"][wizard_mod.STEP_IMAGEKEY]["state"] == "ok", st["steps"]
            assert st["current"] == wizard_mod.STEP_VERIFY, st["current"]
            print("  ok   重启后：盘上密钥把前两步标回已完成，当前步骤停在校验")
            checks += 1

            # 微信未登录、判不了账号归属时同样标回，由页面账号行展示密钥属于谁
            wizard_mod.wechat_account.current_account = lambda root=None: None
            assert wizard_mod.Wizard().status()["current"] == wizard_mod.STEP_VERIFY
            print("  ok   未登录微信时无法判归属，也按就绪处理")
            checks += 1

            # 只有口令时停在第三步
            os.remove(wiz.image_key_path())
            st = wizard_mod.Wizard().status()
            assert st["steps"][wizard_mod.STEP_DBKEY]["state"] == "ok", st["steps"]
            assert st["steps"][wizard_mod.STEP_IMAGEKEY]["state"] == "idle", st["steps"]
            assert st["current"] == wizard_mod.STEP_IMAGEKEY, st["current"]
            print("  ok   只有数据库口令时，当前步骤停在图片密钥")
            checks += 1

            # 密钥属于别的账号时不标完成，向导停回取口令那一步
            wizard_mod.wechat_account.current_account = lambda root=None: types.SimpleNamespace(
                wxid="wxid_other", data_dir=""
            )
            st = wizard_mod.Wizard().status()
            assert st["db_key"]["present"] is True and st["db_key"]["matches_current"] is False, st[
                "db_key"
            ]
            assert st["steps"][wizard_mod.STEP_DBKEY]["state"] == "idle", st["steps"]
            assert st["current"] == wizard_mod.STEP_DBKEY, st["current"]
            print("  ok   密钥与当前登录账号不一致时不标完成，停回取数据库口令")
            checks += 1
        finally:
            wizard_mod.wechat_account.current_account = original_current

    print(f"\n{checks}/{checks} 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
