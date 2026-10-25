"""客户端发来的 ``file`` 段：三种本机来源都能落成文件，URL 不下载。

OneBot 生态里 ``file`` 的取值并不统一：AstrBot 的 ``File`` 组件给的是
``file:///D:/…`` URI，``base64://`` 内联也常见，还有直接给路径或光给名字的。
桥接只接受能在本机拿到的这三种（客户端附件目录由配置兜底），http(s) URL 如实
回失败——替客户端去下载属于超出这条通道的职责。
"""

import asyncio
import base64
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import action_catalog, ob_ids, ob_protocol  # noqa: E402
from flandora.runtime import RUNTIME  # noqa: E402

PAYLOAD = "第一行\n第二行\n".encode()


class FakeSender:
    def __init__(self):
        self.files: list[tuple[str, str, bytes]] = []

    def send_text(self, contact: str, text: str) -> bool:
        return True

    def send_file(self, contact: str, path: str) -> bool:
        with open(path, "rb") as fh:
            self.files.append((contact, path, fh.read()))
        return True

    def send_mention_text(self, contact: str, names: list[str], text: str) -> bool:
        return True


def _call(payload: dict) -> dict:
    return asyncio.run(ob_protocol._handle_ob_api(payload))


def _target(sender) -> int:
    RUNTIME.sender = sender
    uid = ob_ids.contact_id("wxid_zhangsan")
    ob_ids.remember_name(uid, "张三")
    return uid


def _send(uid: int, data: dict) -> dict:
    return _call(
        {
            "action": "send_private_msg",
            "params": {"user_id": uid, "message": [{"type": "file", "data": data}]},
        }
    )


def test_base64_file_keeps_the_client_name(isolated_ids):
    sender = FakeSender()
    uid = _target(sender)
    resp = _send(
        uid,
        {"file": "base64://" + base64.b64encode(PAYLOAD).decode(), "name": "报告 2026.txt"},
    )
    assert resp["status"] == "ok", resp
    assert resp["data"]["message_id"] > 0
    assert len(sender.files) == 1
    contact, path, payload = sender.files[0]
    assert contact == "张三"
    assert os.path.basename(path) == "报告 2026.txt"
    assert payload == PAYLOAD
    # 临时文件与它专属的临时目录发完就收掉
    assert not os.path.exists(path)
    assert not os.path.exists(os.path.dirname(path))


def test_file_uri_and_plain_path_are_accepted(isolated_ids, tmp_path):
    sender = FakeSender()
    uid = _target(sender)
    local = tmp_path / "说明.md"
    local.write_text("你好", encoding="utf-8")
    for given in (local.as_uri(), str(local)):
        resp = _send(uid, {"file": given})
        assert resp["status"] == "ok", resp
    assert [payload for _, _, payload in sender.files] == ["你好".encode()] * 2
    # 调用方自己的文件不动
    assert local.exists()


def test_http_url_is_refused(isolated_ids):
    sender = FakeSender()
    uid = _target(sender)
    resp = _send(uid, {"file": "https://example.com/a.zip", "name": "a.zip"})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_INTERNAL
    assert "URL" in resp["message"]
    assert sender.files == []


def test_missing_file_is_reported(isolated_ids, tmp_path):
    sender = FakeSender()
    uid = _target(sender)
    resp = _send(uid, {"file": str(tmp_path / "不存在.bin")})
    assert resp["status"] == "failed"
    assert "文件不存在" in resp["message"]
    assert sender.files == []


def test_bare_name_is_found_in_client_attachments(isolated_ids, tmp_path, monkeypatch):
    sender = FakeSender()
    uid = _target(sender)
    attachments = tmp_path / "attach"
    attachments.mkdir()
    (attachments / "给芙兰的.txt").write_text("附件目录里的文件", encoding="utf-8")
    monkeypatch.setattr(ob_protocol.config, "CLIENT_ATTACHMENTS", str(attachments))
    resp = _send(uid, {"file": "给芙兰的.txt"})
    assert resp["status"] == "ok", resp
    assert sender.files[0][2] == "附件目录里的文件".encode()


def test_base64_name_is_reduced_to_a_bare_filename(isolated_ids):
    sender = FakeSender()
    uid = _target(sender)
    resp = _send(
        uid,
        {"file": "base64://" + base64.b64encode(PAYLOAD).decode(), "name": "../../逃逸.txt"},
    )
    assert resp["status"] == "ok", resp
    # 名字只用来命名临时文件：去掉目录成分，且落在专属临时目录里
    assert os.path.basename(sender.files[0][1]) == "逃逸.txt"
    assert os.path.basename(os.path.dirname(sender.files[0][1])).startswith("bridge_file_")
