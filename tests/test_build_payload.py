"""安装脚本、载荷布局与交付物身份。

安装脚本用 ``{#BridgeExe}`` 这类 Inno 预处理常量拼路径。这里核对常量能展开成真实
文件名、展开后的每一项都在载荷清单里且清单里没有多余项，三个 exe 的落点与发布说明
一致——打包自检与安装器因此看的是同一份事实，常量写错或漏放文件都会在这里先红。

``build-info.json`` 是交付物的身份证：这里同时钉住它的字段与 hash 覆盖范围。
"""

from __future__ import annotations

import json

import build


def test_installer_sources_expand_to_real_filenames():
    sources = build.installer_sources()
    assert sources
    assert all("{" not in source for source in sources)


def test_installer_sources_match_the_payload_list():
    sources = build.installer_sources()
    files = {source for source in sources if not source.endswith("*")}
    dirs = {source[:-2] for source in sources if source.endswith("/*")}
    for name in build.REQUIRED_PAYLOAD:
        if name == "docs":
            assert name in dirs
        else:
            assert name in files


def test_manifest_is_exactly_the_required_payload():
    assert build.manifest_problems() == []
    assert build.installer_manifest() == set(build.REQUIRED_PAYLOAD)


def test_entry_exe_and_bridge_sit_in_the_root_and_keytool_in_tools():
    sources = build.installer_sources()
    assert "Flandora.exe" in sources
    assert "Flandora-Bridge.exe" in sources
    assert "tools/Flandora-KeyTool.exe" in sources


def test_build_info_covers_every_payload_file(tmp_path, monkeypatch):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "Flandora-KeyTool.exe").write_bytes(b"MZ-keytool")
    (tmp_path / "Flandora.exe").write_bytes(b"MZ-manager")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "wip.md").write_text("# 计划内", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(build, "git_state", lambda: ("a" * 40, False))

    document = build.build_info_document(tmp_path)

    assert document["version"] == build.VERSION
    assert document["commit"] == "a" * 40
    assert document["dirty"] is False
    assert set(document["files"]) == {"Flandora.exe", "tools/Flandora-KeyTool.exe", "docs/wip.md"}


def test_write_build_info_is_json_with_the_same_files(tmp_path, monkeypatch):
    (tmp_path / "Flandora.exe").write_bytes(b"MZ")
    monkeypatch.setattr(build, "git_state", lambda: ("b" * 40, True))

    path, document = build.write_build_info(tmp_path)

    assert path.name == build.BUILD_INFO_NAME
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == document
    assert saved["dirty"] is True


def test_dirty_tree_is_refused_until_explicitly_allowed():
    assert build.dirty_build_problem("abc", False, False) is None
    assert build.dirty_build_problem("abc", True, False) is not None
    assert build.dirty_build_problem("abc", True, True) is None


def test_payload_rebuild_carries_the_user_data_over(tmp_path, monkeypatch):
    """打包前先挪走 data/，打包中 dist 被清空也不会丢配置与口令。"""
    monkeypatch.setattr(build, "DIST", tmp_path / "dist")
    monkeypatch.setattr(build, "payload_data_stash", lambda: tmp_path / "stash")
    payload = build.DIST / build.PKG_NAME
    (payload / "data").mkdir(parents=True)
    (payload / "data" / "wx_key.dat").write_bytes(b"secret-key")
    (payload / "data" / "messages.db").write_bytes(b"db")

    assert build._stash_payload_data() is True
    assert not (payload / "data").exists()
    assert build.payload_data_stash().is_dir()

    # 打包过程相当于 clean() 删掉 dist 再从零搭一份载荷
    import shutil

    shutil.rmtree(payload)
    (payload / "data").mkdir(parents=True)
    (payload / "Flandora.exe").write_bytes(b"MZ")

    assert build._restore_payload_data() is True
    assert (payload / "data" / "wx_key.dat").read_bytes() == b"secret-key"
    assert (payload / "data" / "messages.db").read_bytes() == b"db"
    assert (payload / "Flandora.exe").exists()
    assert not build.payload_data_stash().exists()


def test_restore_reports_failure_when_the_stash_is_gone(tmp_path, monkeypatch):
    """暂存没了要说「没放回」，不能静默当成成功。"""
    monkeypatch.setattr(build, "DIST", tmp_path / "dist")
    monkeypatch.setattr(build, "payload_data_stash", lambda: tmp_path / "stash")
    (build.DIST / build.PKG_NAME / "data").mkdir(parents=True)

    assert build._restore_payload_data() is False


def test_zip_leaves_the_user_data_out(tmp_path, monkeypatch):
    rel = tmp_path / "payload"
    (rel / "data").mkdir(parents=True)
    (rel / "data" / "wx_key.dat").write_bytes(b"secret-key")
    (rel / "Flandora.exe").write_bytes(b"MZ")
    monkeypatch.setattr(build, "DIST", tmp_path)
    zip_path = tmp_path / "payload.zip"

    build._zip_payload(rel, zip_path)

    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "payload/Flandora.exe" in names
    assert not any(name.startswith("payload/data/") for name in names), names
