"""仪表盘统计：今日条数与接收曲线读消息存档，发送方向只有本次运行。

存档是长期事实（重启不清零、不受内存缓冲 300 条上限影响），所以「今日」与 24 小时
曲线里的接收柱从 ``messages.db`` 聚合；发送不入存档（它是客户端发来的动作，不是
推送事件），曲线里的发送柱只覆盖本进程的事件缓冲——这里把两边的边界都钉住。
"""

import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import ob_ids, ob_store  # noqa: E402
from flandora.runtime import events  # noqa: E402
from flandora.webui.api import network as network_api  # noqa: E402


def _message(mid: int, when: float, *, group: int = 0, group_name: str = "测试群") -> dict:
    return {
        "time": int(when),
        "post_type": "message",
        "message_type": "group" if group else "private",
        "message_id": mid,
        "user_id": 7,
        "group_id": group,
        "group_name": group_name if group else "",
        "raw_message": "你好",
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "sender": {"user_id": 7, "nickname": "张三"},
    }


def _midnight(now: float) -> int:
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


def test_today_and_hourly_come_from_the_archive(isolated_store):
    midnight = _midnight(time.time())
    store = ob_store.message_store()
    store.save_event(_message(1, midnight + 60, group=1001))
    store.save_event(_message(2, midnight + 120))
    store.save_event({"message_id": 3, "time": midnight + 180, "post_type": "notice"})
    store.save_event(_message(4, midnight - 60))  # 昨天那条不算今日

    data = network_api.api_stats()

    assert data["today"] == {"recv": 2, "pushed": 3}
    assert len(data["hourly"]) == 24
    # 曲线是「最近 24 小时」：昨天那条 message 仍在窗口里，而通知不算接收
    assert sum(b["in"] for b in data["hourly"]) == 3
    # 同一个人来了两条，排在群前面
    assert [c["contact"] for c in data["top_contacts"]] == ["张三", "测试群"]


def test_top_contacts_fall_back_to_the_name_index(isolated_store, isolated_ids):
    """存档里没写名字时，用 ID 去名字索引里问一次。"""
    store = ob_store.message_store()
    store.save_event(_message(1, time.time() - 60, group=1001, group_name=""))
    ob_ids.remember_name(1001, "测试群")

    data = network_api.api_stats()

    assert data["top_contacts"][0]["contact"] == "测试群"


def test_hourly_outbound_only_covers_this_run(isolated_store):
    """发送不入存档：曲线里的发送柱来自本次运行的内存缓冲。"""
    events.EVENTS.messages.clear()
    try:
        events.EVENTS.publish(
            {
                "type": "msg",
                "ts": time.time() - 60,
                "dir": "out",
                "kind": "text",
                "contact": "张三",
                "text": "在的",
            }
        )
        data = network_api.api_stats()
        assert sum(b["out"] for b in data["hourly"]) == 1
        assert data["today"] == {"recv": 0, "pushed": 0}
    finally:
        events.EVENTS.messages.clear()
