"""验证：取钥向导的状态机与口令文本解析。

不连真微信、不跑 Frida——这里要验的是那些能静态判定的部分：四步的顺序约束、
免责声明确认的持久化、手填口令的候选解析、以及打包/源码两种形态下取钥器的
命令行长什么样。

可写数据目录被临时指到空目录，所以不会碰真实环境里的口令与向导记录。

每个用例各自新建临时目录与 Wizard 实例——状态机是渐进的，但用例之间不共享
状态，失败一条不会连带后面。

两种跑法等价：

    python -m pytest tests/test_wizard.py -q
    python tests/test_wizard.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import config  # noqa: E402
from flandora import wizard as wizard_mod

HEX64 = "62d08ee9343a4fc5a2ad910e0ec7aa221dd7df66231f46c89ef4c7f90771f4e7"


@pytest.fixture
def wiz(tmp_path, monkeypatch):
    """在空目录里新建一个 Wizard：config.CONFIG_FILE 只在本用例内被指过去。"""
    monkeypatch.setattr(config, "CONFIG_FILE", str(tmp_path / "config.json"))
    return wizard_mod.Wizard()


@pytest.fixture
def accepted(wiz):
    """已经勾过免责声明的向导（多数深层用例的前置条件）。"""
    wiz.accept_disclaimer()
    return wiz


# ===== 手填口令的文本解析 =====


def test_parse_passphrase_hex64():
    """64 位十六进制只产出一种候选：32 字节。"""
    got = wizard_mod.parse_passphrase(HEX64)
    assert got == [bytes.fromhex(HEX64)], got
    assert len(got[0]) == 32, len(got[0])


def test_parse_passphrase_32chars_is_ambiguous():
    """32 位串两种解法都给出：16 字节二进制，或 32 字节 ASCII。"""
    got = wizard_mod.parse_passphrase("0123456789abcdef0123456789abcdef")
    assert len(got) == 2, got
    assert got[0] == bytes.fromhex("0123456789abcdef0123456789abcdef")
    assert got[1] == b"0123456789abcdef0123456789abcdef"
    assert len(got[0]) == 16 and len(got[1]) == 32, (len(got[0]), len(got[1]))


def test_parse_passphrase_plain_text():
    """普通文本按 UTF-8 编码，前后空白被去掉。"""
    assert wizard_mod.parse_passphrase("  我的口令  ") == ["我的口令".encode("utf-8")]


def test_parse_passphrase_rejects_blank():
    """空口令必须被拒，不能生成空密钥去试。"""
    with pytest.raises(wizard_mod.WizardError):
        wizard_mod.parse_passphrase("   ")


# ===== 初始状态 =====


def test_initial_state(wiz):
    """四步全 idle，当前步骤停在免责声明。"""
    st = wiz.status()
    assert st["current"] == wizard_mod.STEP_DISCLAIMER, st["current"]
    assert st["disclaimer_accepted"] is False
    assert [s["state"] for s in st["steps"].values()] == ["idle"] * 4, st["steps"]
    assert st["order"] == list(wizard_mod.STEPS)
    for key in ("db_key", "image_key", "manual_rva", "keytool_running", "imagekey_running"):
        assert key in st, key


# ===== 免责声明是硬门槛 =====


def test_dbkey_blocked_without_disclaimer(wiz):
    """没勾免责声明就允许取数据库口令，等于把风险提示当摆设。"""
    with pytest.raises(wizard_mod.WizardError):
        wiz.start_dbkey()


def test_imagekey_blocked_without_disclaimer(wiz):
    """取图片密钥同样要先过免责声明。"""
    with pytest.raises(wizard_mod.WizardError):
        wiz.start_imagekey()


def test_accept_disclaimer_persists(wiz, tmp_path):
    """确认后落到 data/wizard.json，并带 accepted_at，当前步骤前进到第二步。"""
    st = wiz.accept_disclaimer()
    assert st["disclaimer_accepted"] is True
    assert st["steps"][wizard_mod.STEP_DISCLAIMER]["state"] == "ok"
    assert st["current"] == wizard_mod.STEP_DBKEY, st["current"]
    path = tmp_path / "wizard.json"
    assert path.is_file(), str(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["disclaimer_accepted"] is True, saved
    assert "accepted_at" in saved, saved


def test_disclaimer_acceptance_is_remembered(wiz):
    """确认是持久的：新实例不再要求重勾。"""
    wiz.accept_disclaimer()
    assert wizard_mod.Wizard().status()["current"] == wizard_mod.STEP_DBKEY


def test_imagekey_requires_dbkey(accepted):
    """第三步有前置条件：数据库口令没到手时被拒。"""
    assert accepted.status()["db_key"]["present"] is False
    with pytest.raises(wizard_mod.WizardError):
        accepted.start_imagekey()


def test_reset_keeps_disclaimer(accepted):
    """reset 清空后四步重来，但免责声明确认保留（不必让人重看一遍风险提示）。"""
    st = accepted.reset()
    assert st["disclaimer_accepted"] is True
    assert st["steps"][wizard_mod.STEP_DISCLAIMER]["state"] == "ok"
    assert st["steps"][wizard_mod.STEP_DBKEY]["state"] == "idle"
    assert st["current"] == wizard_mod.STEP_DBKEY


# ===== 取钥器的命令行与落点 =====


def test_keytool_command_points_to_tools(wiz):
    """源码形态下取钥器命令行是 <解释器> tools/keytool.py，且文件确实存在。"""
    cmd = wiz.keytool_command()
    assert len(cmd) == 2 and cmd[0] == sys.executable, cmd
    assert cmd[1].endswith(os.path.join("tools", "keytool.py")), cmd
    assert os.path.isfile(cmd[1]), cmd


def test_key_paths_follow_config_dir(wiz, tmp_path):
    """口令与图片密钥默认落在配置文件同目录。"""
    assert wiz.key_path() == str(tmp_path / "wx_key.dat"), wiz.key_path()
    assert wiz.image_key_path() == str(tmp_path / "wx_image_key.json"), wiz.image_key_path()


def test_account_db_paths_is_list(wiz):
    """库清单返回 .db 路径列表；未登录微信时为 0 个，不算失败。"""
    dbs = wiz.account_db_paths()
    assert isinstance(dbs, list), type(dbs)
    assert all(p.endswith(".db") for p in dbs), dbs


def test_verify_fails_without_key(accepted):
    """没有口令时校验直接判失败并说清原因，不去瞎算。"""
    accepted.verify()
    step = {}
    for _ in range(50):
        step = accepted.status()["steps"][wizard_mod.STEP_VERIFY]
        if step["state"] != "running":
            break
        time.sleep(0.1)
    assert step["state"] == "failed", step
    assert "口令" in step["message"], step


# ===== 重启推导：盘上密钥把已完成步骤标回来 =====


def _seed_keys(wiz, wxid="wxid_restart"):
    """在盘上放一份口令与一份图片密钥，模拟「上次已经取过钥」。"""
    from flandora.wxkey.store import KeyStore

    KeyStore(wiz.key_path()).save(bytes(range(32)), wxid=wxid, dll_version="9.9.9")
    with open(wiz.image_key_path(), "w", encoding="utf-8") as fh:
        json.dump({"wxid": wxid, "xor_key": 17, "aes_key": "00" * 32}, fh)


@pytest.fixture
def account_as(monkeypatch):
    """把当前登录账号换掉，用例结束自动还原。"""

    def set_account(wxid):
        if wxid is None:
            monkeypatch.setattr(
                wizard_mod.wechat_account, "current_account", lambda root=None: None
            )
        else:
            monkeypatch.setattr(
                wizard_mod.wechat_account,
                "current_account",
                lambda root=None: types.SimpleNamespace(wxid=wxid, data_dir=""),
            )

    return set_account


def test_restart_restores_both_steps(accepted, account_as):
    """密钥账号与当前登录一致：前两步直接标回已完成，停在校验。"""
    _seed_keys(accepted)
    account_as("wxid_restart")
    st = wizard_mod.Wizard().status()
    assert st["steps"][wizard_mod.STEP_DBKEY]["state"] == "ok", st["steps"]
    assert st["steps"][wizard_mod.STEP_IMAGEKEY]["state"] == "ok", st["steps"]
    assert st["current"] == wizard_mod.STEP_VERIFY, st["current"]


def test_restart_without_login_still_ready(accepted, account_as):
    """微信未登录、判不了账号归属时同样按就绪处理，由页面展示密钥属于谁。"""
    _seed_keys(accepted)
    account_as(None)
    assert wizard_mod.Wizard().status()["current"] == wizard_mod.STEP_VERIFY


def test_restart_with_only_dbkey_stops_at_imagekey(accepted, account_as):
    """只有口令时停在第三步（图片密钥）。"""
    _seed_keys(accepted)
    os.remove(accepted.image_key_path())
    account_as("wxid_restart")
    st = wizard_mod.Wizard().status()
    assert st["steps"][wizard_mod.STEP_DBKEY]["state"] == "ok", st["steps"]
    assert st["steps"][wizard_mod.STEP_IMAGEKEY]["state"] == "idle", st["steps"]
    assert st["current"] == wizard_mod.STEP_IMAGEKEY, st["current"]


def test_restart_with_foreign_key_stops_at_dbkey(accepted, account_as):
    """密钥属于别的账号时不标完成，向导停回取口令那一步。"""
    _seed_keys(accepted)
    account_as("wxid_other")
    st = wizard_mod.Wizard().status()
    assert st["db_key"]["present"] is True and st["db_key"]["matches_current"] is False, st[
        "db_key"
    ]
    assert st["steps"][wizard_mod.STEP_DBKEY]["state"] == "idle", st["steps"]
    assert st["current"] == wizard_mod.STEP_DBKEY, st["current"]


def main() -> int:
    """脚本直跑：本文件的用例依赖 fixture，直接调会缺参数，改委托给 pytest。"""
    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
