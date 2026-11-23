"""入站文件下载：令牌登记、地址、路由只服务登记过的路径。"""

import pytest
from fastapi.testclient import TestClient

from flandora import config, file_share
from flandora.domain.message import File
from flandora.ob_protocol import to_segment_dict
from flandora.webui import app as webui_app


@pytest.fixture(autouse=True)
def _clean():
    file_share._entries.clear()
    file_share._by_path.clear()
    yield
    file_share._entries.clear()
    file_share._by_path.clear()


def test_same_path_gets_same_token(tmp_path):
    p = tmp_path / "a.docx"
    p.write_bytes(b"x")
    assert file_share.register(str(p), "a.docx") == file_share.register(str(p), "a.docx")


def test_registry_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(file_share, "MAX_ENTRIES", 3)
    tokens = []
    for i in range(5):
        p = tmp_path / f"{i}.txt"
        p.write_bytes(b"x")
        tokens.append(file_share.register(str(p)))
    assert file_share.lookup(tokens[0]) is None
    assert file_share.lookup(tokens[-1]) is not None


def test_file_segment_carries_url_and_file_id(tmp_path):
    p = tmp_path / "论文.docx"
    p.write_bytes(b"doc")
    data = to_segment_dict(File(path=str(p), name="论文.docx"))["data"]
    assert data["file"] == str(p)
    assert data["url"].startswith(f"http://127.0.0.1:{config.WEB_PORT}/files/{data['file_id']}/")
    assert file_share.lookup(data["file_id"])[1] == "论文.docx"


def test_download_serves_only_registered_files(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"hello")
    token = file_share.register(str(p), "a.txt")
    client = TestClient(webui_app.app)
    ok = client.get(f"/files/{token}/a.txt")
    assert ok.status_code == 200 and ok.content == b"hello"
    assert client.get("/files/nope/a.txt").status_code == 404
    p.unlink()
    assert client.get(f"/files/{token}/a.txt").status_code == 404
