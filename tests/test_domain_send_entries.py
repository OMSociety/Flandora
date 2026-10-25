"""两条发送入口收敛到同一个执行函数。

send_msg 与面板的 /api/send 曾经各拼一套发送逻辑：一处改了去重或错误文案，
另一处不会跟着变。这里打桩 :func:`flandora.domain.send.execute`，证明两条入口
都真的走到它，并且给出的动作是同一种形状。
"""

import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import ob_ids, ob_protocol  # noqa: E402
from flandora.domain import send as domain_send  # noqa: E402
from flandora.domain.send import SendOutcome, SendStatus, SendText  # noqa: E402
from flandora.runtime import LIFECYCLE, RUNTIME  # noqa: E402
from flandora.webui import app as webui_app  # noqa: E402


class _Sender:
    """只满足协议里被面板与桥接用到的那部分。"""

    def send_text(self, contact: str, text: str) -> bool:
        raise AssertionError("execute 已被打桩，不该再调用发送器")

    def last_error(self) -> str:
        return ""


def _spy(monkeypatch) -> list[tuple[list, str]]:
    calls: list[tuple[list, str]] = []

    def fake_execute(actions, sender, resolve_mention, contact):
        calls.append((list(actions), contact))
        text = actions[0].text if actions else ""
        return [SendOutcome(SendStatus.SENT, "文字", text)]

    monkeypatch.setattr(domain_send, "execute", fake_execute)
    return calls


def test_onebot_send_msg_goes_through_execute(isolated_ids, monkeypatch):
    calls = _spy(monkeypatch)
    RUNTIME.sender = _Sender()
    # 客户端只认整数 ID：先按收到的会话登记，发送时才能反查出会话名
    target = ob_ids.contact_id("wxid_lisi")
    ob_ids.remember_name(target, "李四")

    resp = asyncio.run(
        ob_protocol._handle_ob_api(
            {
                "action": "send_private_msg",
                "params": {
                    "user_id": target,
                    "message": [{"type": "text", "data": {"text": "hi"}}],
                },
            }
        )
    )

    assert [actions for actions, _contact in calls] == [[SendText(text="hi")]]
    assert [contact for _actions, contact in calls] == ["李四"]
    assert resp["status"] == "ok"


def test_panel_send_goes_through_execute(monkeypatch):
    calls = _spy(monkeypatch)
    RUNTIME.sender = _Sender()
    LIFECYCLE.begin_start()

    with TestClient(webui_app.app) as client:
        resp = client.post("/api/send", json={"contact": "张三", "text": "hi"})

    assert [actions for actions, _contact in calls] == [[SendText(text="hi")]]
    assert [contact for _actions, contact in calls] == ["张三"]
    assert resp.json() == {"ok": True}


def main() -> int:
    """脚本直跑：本文件用到 fixture，直接调用会 TypeError，委托给 pytest。"""
    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
