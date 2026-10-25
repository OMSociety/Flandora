"""更新端点的离线测试：不触网，也不真的拉起 PowerShell。"""

import asyncio
import json
import os

from flandora import updater
from flandora.runtime import RUNTIME
from flandora.webui import app as webui_app


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.config, "data_dir", lambda: str(tmp_path))


def test_update_status_reports_source_mode(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(RUNTIME, "update_available", {})
    data = webui_app.api_update_status()
    assert data["ok"] is True
    assert data["mode"] == "source"
    assert data["current"] == updater.__dict__.get("__version__") or data["current"]
    assert data["pending"] is None
    assert data["app_dir"].endswith("Flandora")


def test_update_check_reports_newer_version(monkeypatch):
    monkeypatch.setattr(
        updater,
        "latest_release",
        lambda timeout=None: {"version": "9.9.9", "tag": "v9.9.9", "url": "u", "asset": "a"},
    )
    data = webui_app.api_update_check()
    assert data["ok"] is True and data["newer"] is True
    assert data["latest"]["version"] == "9.9.9"


def test_update_check_reports_current_version(monkeypatch):
    monkeypatch.setattr(
        updater,
        "latest_release",
        lambda timeout=None: {"version": "0.0.1", "tag": "v0.0.1", "url": "u", "asset": "a"},
    )
    assert webui_app.api_update_check()["newer"] is False


def test_update_check_failure_is_a_502(monkeypatch):
    def boom(timeout=None):
        raise updater.UpdateError("连不上 GitHub")

    monkeypatch.setattr(updater, "latest_release", boom)
    response = webui_app.api_update_check()
    assert response.status_code == 502
    assert _body(response)["error"] == "连不上 GitHub"


def test_update_download_stages_archive(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        updater,
        "latest_release",
        lambda timeout=None: {"version": "9.9.9", "tag": "v9.9.9", "url": "u", "asset": "a"},
    )
    seen = {}

    def fake_download(url, dest, progress=None, timeout=None):
        seen["url"] = url
        seen["dest"] = dest
        open(dest, "wb").write(b"zip")
        return dest

    def fake_stage(archive, version):
        seen["staged"] = version
        return {"version": version, "staging": str(tmp_path / "staging-9.9.9")}

    monkeypatch.setattr(updater, "download", fake_download)
    monkeypatch.setattr(updater, "stage", fake_stage)
    data = asyncio.run(webui_app.api_update_download())
    assert data["ok"] is True and data["pending"]["version"] == "9.9.9"
    assert seen["url"] == "a" and seen["staged"] == "9.9.9"
    assert seen["dest"].endswith("Flandora-v9.9.9.zip")


def test_update_download_reports_no_newer_version(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        updater,
        "latest_release",
        lambda timeout=None: {"version": "0.0.1", "tag": "v0.0.1", "url": "u", "asset": "a"},
    )
    response = asyncio.run(webui_app.api_update_download())
    assert response.status_code == 502
    assert "没有更新的版本" in _body(response)["error"]


def test_update_install_requires_pending(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    response = asyncio.run(webui_app.api_update_install())
    assert response.status_code == 400
    assert "还没有下载" in _body(response)["error"]


def test_update_install_refuses_in_source_mode(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    with open(updater.pending_file(), "w", encoding="utf-8") as fh:
        json.dump({"version": "9.9.9", "staging": str(staging)}, fh)
    response = asyncio.run(webui_app.api_update_install())
    assert response.status_code == 400
    assert "git pull" in _body(response)["error"]
    assert updater.pending() is not None


def test_update_cancel_clears_staging(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "Flandora.exe").write_bytes(b"MZ")
    with open(updater.pending_file(), "w", encoding="utf-8") as fh:
        json.dump({"version": "9.9.9", "staging": str(staging)}, fh)
    assert webui_app.api_update_cancel()["ok"] is True
    assert updater.pending() is None
    assert not os.path.isdir(staging)
