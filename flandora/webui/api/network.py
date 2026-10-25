"""网络组：OneBot 入口运行态、实时日志缓冲、消息统计。

三个端点都是只读视图：入口状态问 ``ob_network``，日志与计数读核心侧事件总线的
缓冲（``runtime.events``），不触发任何采集或清理。
"""

import time

from fastapi import APIRouter

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
    now = time.time()
    msgs, counters = events.EVENTS.messages_and_counters()

    # 最近 24 小时按小时分桶
    buckets = {}
    for m in msgs:
        if now - m.get("ts", 0) > 86400:
            continue
        h = time.strftime("%H", time.localtime(m["ts"]))
        b = buckets.setdefault(h, {"in": 0, "out": 0})
        key = "in" if m.get("dir") == "in" else "out"
        b[key] += 1

    hourly = []
    for i in range(23, -1, -1):
        h = time.strftime("%H", time.localtime(now - i * 3600))
        b = buckets.get(h, {"in": 0, "out": 0})
        hourly.append({"hour": h, "in": b["in"], "out": b["out"]})

    # 热门联系人（按接收消息数）
    contact_counts = {}
    for m in msgs:
        if m.get("dir") == "in" and m.get("contact"):
            c = m["contact"]
            contact_counts[c] = contact_counts.get(c, 0) + 1
    top = sorted(contact_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]

    return {
        "counters": counters,
        "hourly": hourly,
        "top_contacts": [{"contact": c, "count": n} for c, n in top],
    }
