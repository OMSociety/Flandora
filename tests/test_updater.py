"""updater 的离线测试：网络与文件系统都用假的，不触网。"""

import json
import os
import zipfile

import pytest

from flandora import updater


class FakeResponse:
    """够 urllib 上下文管理器用法的假响应。"""

    def __init__(self, body: bytes, length: int | None = None):
        self._body = body
        self.headers = {"Content-Length": str(length if length is not None else len(body))}
        self._pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._body[self._pos :]
        else:
            chunk = self._body[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def _make_zip(path, entries: dict, root: str = "Flandora-v1.2.0"):
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in entries.items():
            archive.writestr(f"{root}/{name}" if root else name, body)
    return str(path)


def test_install_mode_and_app_dir_point_at_repo():
    assert updater.install_mode() == "source"
    assert os.path.isfile(os.path.join(updater.app_dir(), "run_bridge.py"))


def test_latest_release_reads_tag_and_asset(monkeypatch):
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Release notes from Flandora</title>"
        "<entry><title>v1.3.0</title></entry>"
        "<entry><title>v1.2.0</title></entry></feed>"
    ).encode("utf-8")
    page = b'<a href="/OMSociety/Flandora/releases/download/v1.3.0/Flandora-v1.3.0.zip">x</a>'

    def fake_fetch(url, timeout):
        return page if "expanded_assets" in url else atom

    monkeypatch.setattr(updater, "_fetch", fake_fetch)
    info = updater.latest_release()
    assert info["version"] == "1.3.0"
    assert info["tag"] == "v1.3.0"
    assert info["asset"].endswith("/releases/download/v1.3.0/Flandora-v1.3.0.zip")
    assert info["asset"].startswith("https://github.com/OMSociety/Flandora")


def test_latest_release_without_versions_raises(monkeypatch):
    monkeypatch.setattr(updater, "_fetch", lambda url, timeout: b"<feed><title>x</title></feed>")
    with pytest.raises(updater.UpdateError):
        updater.latest_release()


def test_asset_falls_back_to_build_naming(monkeypatch):
    monkeypatch.setattr(updater, "_fetch", lambda url, timeout: b"<html>nothing</html>")
    asset = updater._asset_for("v9.9.9")
    assert asset.endswith("/releases/download/v9.9.9/Flandora-v9.9.9.zip")


def test_download_writes_file_and_reports_progress(monkeypatch, tmp_path):
    body = b"x" * 1000
    monkeypatch.setattr(updater, "_opener", lambda request, timeout: FakeResponse(body))
    seen = []
    dest = str(tmp_path / "pkg.zip")
    updater.download(
        "https://example.invalid/pkg.zip",
        dest,
        progress=lambda got, total: seen.append((got, total)),
    )
    assert os.path.isfile(dest)
    assert not os.path.exists(dest + ".part")
    with open(dest, "rb") as fh:
        assert fh.read() == body
    assert seen and seen[-1] == (1000, 1000)


def test_download_rejects_empty_body(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_opener", lambda request, timeout: FakeResponse(b""))
    with pytest.raises(updater.UpdateError, match="0 字节"):
        updater.download("https://example.invalid/pkg.zip", str(tmp_path / "pkg.zip"))


def test_download_wraps_network_errors(monkeypatch, tmp_path):
    def boom(request, timeout):
        raise OSError("connection reset")

    monkeypatch.setattr(updater, "_opener", boom)
    with pytest.raises(updater.UpdateError, match="下载失败"):
        updater.download("https://example.invalid/pkg.zip", str(tmp_path / "pkg.zip"))
    assert not os.path.exists(str(tmp_path / "pkg.zip.part"))


def test_safe_members_rejects_escaping_paths(tmp_path):
    path = _make_zip(tmp_path / "bad.zip", {"../evil.exe": b"x"})
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(updater.UpdateError, match="越界路径"):
            updater.safe_members(archive)


def test_extract_finds_payload_root_and_rejects_junk(tmp_path):
    good = _make_zip(tmp_path / "good.zip", {"Flandora.exe": b"MZ"})
    root = updater.extract(good, str(tmp_path / "out"))
    assert os.path.isfile(os.path.join(root, "Flandora.exe"))

    junk = _make_zip(tmp_path / "junk.zip", {"readme.txt": b"hello"})
    with pytest.raises(updater.UpdateError, match="没有找到 Flandora.exe"):
        updater.extract(junk, str(tmp_path / "junk-out"))

    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip at all")
    with pytest.raises(updater.UpdateError, match="不是有效的 zip"):
        updater.extract(str(broken), str(tmp_path / "broken-out"))


def test_stage_pending_and_clear(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.config, "data_dir", lambda: str(tmp_path))
    archive = _make_zip(tmp_path / "pkg.zip", {"Flandora.exe": b"MZ"})
    info = updater.stage(archive, "1.4.0")
    assert os.path.isdir(info["staging"])
    loaded = updater.pending()
    assert loaded is not None and loaded["version"] == "1.4.0"
    updater.clear_pending()
    assert updater.pending() is None


def test_pending_ignores_missing_staging(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.config, "data_dir", lambda: str(tmp_path))
    path = os.path.join(updater.updates_dir(), "pending.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": "1.0.0", "staging": str(tmp_path / "gone")}, fh)
    assert updater.pending() is None


def test_pending_survives_broken_json(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.config, "data_dir", lambda: str(tmp_path))
    path = os.path.join(updater.updates_dir(), "pending.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert updater.pending() is None


def test_swap_script_contains_paths_and_rollback(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.config, "data_dir", lambda: str(tmp_path))
    info = {
        "version": "1.4.0",
        "staging": str(tmp_path / "staging"),
        "app_dir": str(tmp_path / "app"),
    }
    script = updater.swap_script(info)
    assert ("$target = '%s'" % (tmp_path / "app")) in script
    assert ("$staging = '%s'" % (tmp_path / "staging")) in script
    assert "Wait-Process" in script and "Start-Process" in script
    assert "回滚" in script
    path = updater.write_swap_script(info)
    assert os.path.isfile(path) and path.endswith("apply-1.4.0.ps1")


def test_can_write_app_dir_is_true_for_repo():
    assert updater.can_write_app_dir() is True


def test_max_download_size_guard(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "MAX_DOWNLOAD_BYTES", 10)
    monkeypatch.setattr(updater, "_opener", lambda request, timeout: FakeResponse(b"y" * 4096))
    with pytest.raises(updater.UpdateError, match="512 MB"):
        updater.download("https://example.invalid/pkg.zip", str(tmp_path / "pkg.zip"))
    assert not os.path.exists(str(tmp_path / "pkg.zip"))
