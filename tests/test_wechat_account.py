"""微信数据根目录的定位：系统「文档」与微信自己记的存储位置两个来源。"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import wechat_account  # noqa: E402


def _make_root(base: str) -> str:
    """在 base 下造一个像样的微信数据根目录，返回它的路径。"""
    root = os.path.join(base, "xwechat_files")
    login = os.path.join(root, "all_users", "login", "wxid_abcd1234")
    os.makedirs(login)
    with open(os.path.join(login, "key_info.db"), "wb") as handle:
        handle.write(b"marker")
    os.makedirs(os.path.join(root, "wxid_abcd1234_4055"))
    return root


def _write_wechat_setting(
    appdata: str, value: str, name: str = "51a1fffea11325a1e4104c6b3de47af7.ini"
):
    directory = os.path.join(appdata, "Tencent", "xwechat", "config")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(value + "\n")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把「文档」与 %APPDATA% 都指向临时目录，互不干扰。"""
    documents = tmp_path / "documents"
    documents.mkdir()
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(wechat_account, "_documents_dir", lambda: str(documents))
    monkeypatch.setenv("APPDATA", str(appdata))
    return documents, appdata


def test_default_root_prefers_documents(sandbox):
    documents, appdata = sandbox
    _make_root(str(documents))
    _write_wechat_setting(str(appdata), str(appdata / "elsewhere"))

    assert wechat_account.default_root() == os.path.join(str(documents), "xwechat_files")


def test_default_root_uses_wechat_setting_when_documents_is_empty(sandbox):
    documents, appdata = sandbox
    elsewhere = appdata / "Tencent Files"
    elsewhere.mkdir()
    _make_root(str(elsewhere))
    _write_wechat_setting(str(appdata), str(elsewhere))

    assert wechat_account.default_root() == os.path.join(str(elsewhere), "xwechat_files")


def test_default_root_ignores_settings_that_point_nowhere(sandbox):
    documents, appdata = sandbox
    _write_wechat_setting(str(appdata), str(appdata / "gone"))

    assert wechat_account.default_root() == os.path.join(str(documents), "xwechat_files")


def test_default_root_handles_missing_or_empty_settings(sandbox):
    documents, appdata = sandbox
    _write_wechat_setting(str(appdata), "", name="empty.ini")

    assert wechat_account.default_root() == os.path.join(str(documents), "xwechat_files")


def test_accounts_are_listed_from_the_configured_root(sandbox):
    documents, appdata = sandbox
    elsewhere = appdata / "Tencent Files"
    elsewhere.mkdir()
    _make_root(str(elsewhere))
    _write_wechat_setting(str(appdata), str(elsewhere))

    accounts = wechat_account.list_accounts()

    assert [account.wxid for account in accounts] == ["wxid_abcd1234"]
    assert accounts[0].data_dir == os.path.join(
        str(elsewhere), "xwechat_files", "wxid_abcd1234_4055"
    )


def main() -> int:
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()
