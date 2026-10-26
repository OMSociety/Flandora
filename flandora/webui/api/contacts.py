"""联系人组：测试发送的候选名单、面板测试发送。

``/api/send`` 与 OneBot 的 ``send_msg`` 走同一个执行入口
（``send_plan.plan_message()`` → ``domain_send.execute()``）：规划、逐段发送、
失败记账只有一份。面板这边只发一段纯文字，@ 目标一律拒绝。
"""

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ... import config, ob_ids, send_plan
from ...domain import send as domain_send
from ...domain.message import Text
from ...domain.send import SendStatus
from ...runtime import LIFECYCLE, events
from .. import deps

log = logging.getLogger("flandora")

router = APIRouter()

ROUTES: dict[str, dict[str, str]] = {
    "/api/contacts": {"GET": "call"},
    "/api/send": {"POST": "call"},
}


@router.get("/api/contacts")
def api_contacts():
    """测试发送的候选联系人：配置里的群名映射 + 微信库名册 + 已见过的会话。

    名册来自本地库（好友与群），所以新装的环境在还没收到任何消息时也能选到人；
    文件传输助手不在名册里（它不算好友），单独补一条，它是最安全的发送自测对象。
    """
    contacts = []
    seen_ids = set()
    seen_names = set()

    def add(cid, name, kind):
        name = str(name or "").strip()
        cid = str(cid or "").strip()
        if not name or name in seen_names:
            return
        if cid and cid in seen_ids:
            return
        contacts.append({"id": cid, "name": name, "kind": kind})
        seen_names.add(name)
        if cid:
            seen_ids.add(cid)

    for gid, name in (config.GROUP_NAME_MAP or {}).items():
        add(str(gid), name, "group")

    store = deps.store()
    if store is not None:
        try:
            for group in store.groups():
                add(group.get("username"), group.get("name"), "group")
        except Exception as e:  # noqa: BLE001 - 名册读不到不影响配置里的条目
            log.warning(f"[面板] 读取群名册失败: {e}")
        try:
            for person in store.contacts():
                add(person.get("username"), person.get("name"), "private")
        except Exception as e:  # noqa: BLE001
            log.warning(f"[面板] 读取好友名册失败: {e}")
    add("filehelper", "文件传输助手", "private")

    for oid, name in ob_ids.name_snapshot().items():
        add(str(oid), name, "group" if str(oid).endswith("@chatroom") else "private")
    return {"contacts": contacts}


def _refuse_mention(targets) -> tuple[list[str], str]:
    """面板测试发送只发一段纯文字：没有 @ 目标，也不解析群成员名册。"""
    return [], "面板测试发送不支持 @ 成员"


@router.post("/api/send")
async def api_send(payload: dict):
    contact = ((payload or {}).get("contact") or "").strip()
    text = (payload or {}).get("text") or ""
    if not contact or not text:
        return JSONResponse({"ok": False, "error": "联系人和内容不能为空"}, 400)

    sender = deps.sender()
    if not LIFECYCLE.snapshot().is_active or sender is None:
        return JSONResponse({"ok": False, "error": "桥接未运行，请先启动"}, 400)

    log.info(f"[面板] 测试发送 -> {contact}: {text[:50]}")
    # 与 OneBot 的 send_msg 走同一个执行入口：规划、逐段发送、失败记账只有一份
    actions = send_plan.plan_message([Text(text=text)])
    try:
        outcomes = await asyncio.to_thread(
            domain_send.execute, actions, sender, _refuse_mention, contact
        )
    except Exception as e:
        log.error(f"[面板] 测试发送异常: {e}")
        return JSONResponse({"ok": False, "error": "测试发送失败，详细原因见运行日志"}, 500)

    if outcomes and outcomes[0].status is SendStatus.SENT:
        events.record_outbound(contact, "manual", text)
        log.info(f"[面板] 测试发送成功 -> {contact}")
        return {"ok": True}
    log.warning(f"[面板] 测试发送失败 -> {contact}")
    return {"ok": False, "error": sender.last_error() or "发送失败，详见运行日志"}
