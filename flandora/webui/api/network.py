"""网络组：OneBot 入口运行态、实时日志缓冲、消息统计。

三个端点都是只读视图：入口状态问 ``ob_network``，日志读核心侧事件总线的缓冲
（``runtime.events``），统计读消息存档（``ob_store``）——重启不清零，也不受内存
缓冲条数上限的影响。都不触发任何采集或清理。
"""

import time

from fastapi import APIRouter

from ... import ob_ids, ob_store
from ...ob_network import manager as ob_networks
from ...runtime import events

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/networks": {"GET": "call"},
    "/api/logs": {"GET": "call"},
    "/api/stats": {"GET": "call"},
}


@router.get("/api/networks")
def api_networks():
    """各个 OneBot 网络入口的运行态。"""
    return {"networks": ob_networks().status()}


@router.get("/api/logs")
def api_logs(limit: int = 200):
    limit = max(1, min(limit, 500))
    return {"logs": events.EVENTS.logs_copy()[-limit:]}


@router.get("/api/stats")
def api_stats():
    """仪表盘统计：今日条数、最近 24 小时曲线、活跃会话。

    「今日」与曲线的接收侧读消息存档——重启不清零，也不受内存缓冲 300 条上限的
    影响。发送方向不入存档（它是客户端发来的动作，不是推送事件），只有本次运行的
    计数可用，所以曲线里的发送柱只覆盖本次运行。
    """
    now = time.time()
    local = time.localtime(now)
    today_start = int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))
    store = ob_store.message_store()

    counts = store.counts_since(today_start)

    # 最近 24 小时按小时分桶：接收来自存档，发送来自本次运行的消息缓冲
    inbound = {}
    for start, n in store.hourly_messages_since(now - 86400).items():
        inbound[time.strftime("%H", time.localtime(start))] = n
    outbound = {}
    for m in events.EVENTS.messages_copy():
        if m.get("dir") != "out" or now - m.get("ts", 0) > 86400:
            continue
        label = time.strftime("%H", time.localtime(m["ts"]))
        outbound[label] = outbound.get(label, 0) + 1

    hourly = []
    for i in range(23, -1, -1):
        h = time.strftime("%H", time.localtime(now - i * 3600))
        hourly.append({"hour": h, "in": inbound.get(h, 0), "out": outbound.get(h, 0)})

    top_contacts = []
    for item in store.active_chats_since(now - 86400, limit=10):
        oid = int(item.get("id") or 0)
        name = str(item.get("name") or "") or ob_ids.lookup_name(oid) or str(oid)
        top_contacts.append({"contact": name, "count": int(item.get("count") or 0)})

    return {
        "today": {"recv": int(counts["messages"]), "pushed": int(counts["events"])},
        "hourly": hourly,
        "top_contacts": top_contacts,
    }
