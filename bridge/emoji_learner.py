# -*- coding: utf-8 -*-
"""微信自带表情学习器。

链路：
1. 采集：扫描聊天消息里的微信表情代码（[捂脸][旺柴] 这类，微信渲染为小黄豆），
   记录「代码 + 出现场景 + 发送者」样本，持久化到 emoji_usage.json。
2. 蒸馏：样本积累到阈值（emoji_learn_threshold）后，用 LLM 把样本总结成
   「表达方式」条目 {situation, style}（style 里带表情代码）。
3. 投喂：通过 MaiBot WebUI 官方 API（/api/webui/expression/import）写入麦麦的
   表达方式库（checked=true 直接可用），麦麦回复时就会按学到的场景使用表情代码。

投喂鉴权：MaiBot WebUI 用 cookie 鉴权（maibot_session=access_token），
token 从 MaiBot 的 data/webui.json 自动读取（路径由 maibot_emoji_dir 推导）。
"""

import json
import logging
import os
import re
import threading
import time
from collections import Counter

import requests

import config

log = logging.getLogger("ob11-bridge")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emoji_usage.json")

# 微信自带表情代码（文本形式）。不在此表内的 [xxx] 一律不当作表情统计。
WECHAT_EMOJI_CODES = {
    "微笑", "撇嘴", "色", "发呆", "得意", "流泪", "害羞", "闭嘴", "睡", "大哭",
    "尴尬", "发怒", "调皮", "呲牙", "惊讶", "难过", "囧", "抓狂", "吐", "偷笑",
    "愉快", "白眼", "傲慢", "困", "惊恐", "憨笑", "悠闲", "咒骂", "疑问", "嘘",
    "晕", "衰", "骷髅", "敲打", "再见", "擦汗", "抠鼻", "鼓掌", "坏笑", "左哼哼",
    "右哼哼", "鄙视", "委屈", "快哭了", "阴险", "亲亲", "可怜", "笑脸", "生病",
    "脸红", "破涕为笑", "恐惧", "失望", "无语", "嘿哈", "捂脸", "奸笑", "机智",
    "皱眉", "耶", "吃瓜", "加油", "汗", "天啊", "Emm", "社会社会", "旺柴",
    "好的", "打脸", "哇", "翻白眼", "666", "让我看看", "叹气", "苦涩", "裂开",
    "嘴唇", "爱心", "心碎", "拥抱", "强", "弱", "握手", "胜利", "抱拳", "勾引",
    "拳头", "OK", "合十", "啤酒", "咖啡", "蛋糕", "玫瑰", "凋谢", "菜刀",
    "炸弹", "便便", "月亮", "太阳", "礼物", "红包", "發", "福", "烟花", "爆竹",
    "猪头", "跳跳", "发抖", "转圈",
}

CODE_RE = re.compile(r"\[([^\[\]\s]{1,7})\]")

MAX_SAMPLES = 500          # 样本容量上限
MAX_DISTILL_CODES = 8      # 每次蒸馏最多覆盖的表情代码数
MAX_EXAMPLES_PER_CODE = 6  # 每个代码交给 LLM 的例句上限

_lock = threading.Lock()
_state: dict | None = None
_distill_running = threading.Event()


# ============ 持久化 ============


def _load() -> dict:
    global _state
    if _state is None:
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                _state = json.load(f)
        except Exception:
            _state = {}
        _state.setdefault("samples", [])
        _state.setdefault("guide", [])          # 最近蒸馏出的表达方式
        _state.setdefault("pushed_keys", [])    # 已投喂的 situation|style 指纹
        _state.setdefault("distilled_total", 0)  # 已蒸馏消费的样本数
        _state.setdefault("last_distill_ts", 0)
        _state.setdefault("last_push_msg", "")
    return _state


def _save():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning(f"[表情学习] 保存样本失败: {e}")


# ============ 采集 ============


def record_message(text: str, sender: str, session_id: str):
    """从一条聊天消息中采集表情代码样本（线程安全，异常吞没）。"""
    try:
        if not getattr(config, "EMOJI_LEARN_ENABLED", True):
            return
        if not text:
            return
        codes = [c for c in CODE_RE.findall(text) if c in WECHAT_EMOJI_CODES]
        if not codes:
            return

        with _lock:
            st = _load()
            st["samples"].append({
                "codes": codes,
                "text": text[:80],
                "sender": sender or "",
                "session": session_id or "",
                "ts": time.time(),
            })
            if len(st["samples"]) > MAX_SAMPLES:
                st["samples"] = st["samples"][-MAX_SAMPLES:]
            pending = len(st["samples"]) - st["distilled_total"]
            _save()

        log.info(f"[表情学习] 采集样本: {' '.join('[' + c + ']' for c in codes)} (待蒸馏 {pending})")

        # 达到阈值自动蒸馏（后台线程，不阻塞消息流）
        threshold = int(getattr(config, "EMOJI_LEARN_THRESHOLD", 15) or 15)
        if pending >= threshold and not _distill_running.is_set():
            threading.Thread(target=distill_and_push, kwargs={"trigger": "auto"},
                             daemon=True, name="emoji-distill").start()
    except Exception:
        pass


# ============ 蒸馏（LLM 总结） ============


def _build_distill_prompt(samples: list) -> str:
    """把样本按代码分组，构造给 LLM 的蒸馏提示词。"""
    by_code: dict[str, list] = {}
    for s in samples:
        for c in s["codes"]:
            by_code.setdefault(c, []).append(s)

    # 按出现频次取 Top N 代码
    top = sorted(by_code.items(), key=lambda kv: len(kv[1]), reverse=True)[:MAX_DISTILL_CODES]

    parts = []
    for code, items in top:
        examples = []
        for it in items[-MAX_EXAMPLES_PER_CODE:]:
            who = f"{it['sender']}" if it.get("sender") else "某人"
            examples.append(f"  - {who}：{it['text']}")
        parts.append(f"表情 [{code}]（出现 {len(items)} 次）：\n" + "\n".join(examples))

    sample_text = "\n\n".join(parts)
    return (
        "你是聊天习惯分析助手。下面是微信群里大家使用自带表情（方括号代码形式）的真实记录。\n\n"
        f"{sample_text}\n\n"
        "请总结每种表情的使用场景，输出一个 JSON 数组，每个元素格式：\n"
        '{"situation": "什么时候用（10字以内，如：觉得好笑又无语时）", '
        '"style": "怎么写进回复（要包含表情代码本身，如：句尾加[捂脸]）", '
        '"code": "表情代码（不带方括号）"}\n\n'
        "要求：\n"
        "1. 只输出 JSON 数组，不要任何其他文字\n"
        "2. 每个表情最多 2 条，只挑用法明确的；用法不明的跳过\n"
        "3. situation 和 style 用简体中文，口语化\n"
    )


def _call_llm(prompt: str) -> list | None:
    """调用 OpenAI 兼容 LLM 做蒸馏。返回解析出的条目列表或 None。"""
    import state
    api_base = state.image_caption_api_base
    if not api_base:
        log.warning("[表情学习] 未配置 image_caption_api_base，无法蒸馏")
        return None
    try:
        resp = requests.post(
            f"{api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {state.image_caption_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": state.image_caption_model,
                "messages": [{"role": "user", "content": prompt}],
                # 推理模型会把 reasoning 计入 completion tokens，预算要给足
                "max_tokens": 4000,
                "temperature": 0.3,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            log.warning(f"[表情学习] LLM 返回 HTTP {resp.status_code}: {resp.text[:150]}")
            return None
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # 逐对象提取（容错：整体 JSON 截断/多余文字时，单个 {...} 仍可用）
        out = []
        for m in re.finditer(r"\{[^{}]*\}", content, re.DOTALL):
            try:
                it = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
            situation = str(it.get("situation", "")).strip()
            style = str(it.get("style", "")).strip()
            code = str(it.get("code", "")).strip()
            # 校验：代码必须在已知表内，style 必须真的带这个代码
            if situation and style and code in WECHAT_EMOJI_CODES and f"[{code}]" in style:
                out.append({"situation": situation, "style": style, "code": code})
        if not out:
            log.warning(f"[表情学习] LLM 输出无法解析: {content[:200]}")
            return None
        return out
    except Exception as e:
        log.warning(f"[表情学习] LLM 蒸馏失败: {e}")
        return None


# ============ 投喂 MaiBot ============


def _maibot_token() -> str:
    """从 MaiBot data/webui.json 读 WebUI token（路径由 maibot_emoji_dir 推导）。"""
    data_dir = os.path.dirname(config.MAIBOT_EMOJI_DIR.rstrip("\\/"))
    token_file = os.path.join(data_dir, "webui.json")
    with open(token_file, "r", encoding="utf-8") as f:
        return json.load(f)["access_token"]


def _find_target_chat(cookies: dict) -> dict | None:
    """在麦麦的聊天流里找目标群。

    匹配优先级：
    1. 样本最多的会话 ID（如 47795211464@chatroom）作为子串匹配 chat_name
       （麦麦的群聊天流名是 group_<原始群ID> 格式，这是最可靠的锚点）
    2. 样本会话学到的群显示名精确匹配 chat_name
    3. 兜底：第一个群聊 → 第一个聊天流
    """
    base = config.MAIBOT_WEBUI_URL.rstrip("/")
    r = requests.get(f"{base}/api/webui/expression/chat-targets",
                     cookies=cookies, timeout=10)
    if r.status_code != 200:
        log.warning(f"[表情学习] 获取麦麦聊天列表失败: HTTP {r.status_code}")
        return None
    chats = r.json().get("data", [])

    st = _load()
    # 样本会话按出现次数排序
    session_counts = Counter(
        s.get("session", "") for s in st["samples"] if s.get("session")
    ).most_common()

    # 1. 原始会话 ID 子串匹配（group_47795211464@chatroom 这种命名）
    for sid, _n in session_counts:
        for c in chats:
            if sid in (c.get("chat_name") or ""):
                return c

    # 2. 学到的显示名匹配
    import state as _st
    for sid, _n in session_counts:
        name = _st._ob_id_to_contact.get(sid, "")
        if name:
            for c in chats:
                if c.get("chat_name") == name:
                    return c

    # 3. 兜底：第一个群聊 → 第一个聊天流
    for c in chats:
        if c.get("is_group"):
            return c
    return chats[0] if chats else None


def push_to_maibot(items: list) -> str:
    """把蒸馏出的表达方式导入麦麦（checked=true 直接可用）。返回结果描述。"""
    if not items:
        return "没有可投喂的条目"
    try:
        token = _maibot_token()
    except Exception as e:
        msg = f"读取麦麦 WebUI token 失败: {e}"
        log.warning(f"[表情学习] {msg}")
        return msg

    cookies = {"maibot_session": token}
    base = config.MAIBOT_WEBUI_URL.rstrip("/")

    chat = _find_target_chat(cookies)
    if not chat:
        msg = "麦麦里找不到目标聊天流（可能还没有任何聊天记录）"
        log.warning(f"[表情学习] {msg}")
        return msg

    with _lock:
        st = _load()
        pushed = set(st["pushed_keys"])
    new_items = []
    for it in items:
        key = f"{it['situation']}|{it['style']}"
        if key not in pushed:
            new_items.append(it)
    if not new_items:
        return "全部为重复条目，跳过投喂"

    payload = {
        "chat_id": chat["chat_id"],
        "expressions": [
            {
                "situation": it["situation"],
                "style": it["style"],
                "checked": True,
                "modified_by": "emoji-learner",
            }
            for it in new_items
        ],
    }
    try:
        r = requests.post(f"{base}/api/webui/expression/import",
                          json=payload, cookies=cookies, timeout=15)
        if r.status_code == 200:
            data = r.json()
            msg = (f"已投喂 {data.get('imported_count', 0)} 条到「{chat.get('chat_name')}」"
                   f"（重复跳过 {data.get('skipped_count', 0)}）")
            log.info(f"[表情学习] {msg}")
            with _lock:
                st = _load()
                for it in new_items:
                    st["pushed_keys"].append(f"{it['situation']}|{it['style']}")
                st["pushed_keys"] = st["pushed_keys"][-300:]
                _save()
            return msg
        msg = f"投喂失败: HTTP {r.status_code} {r.text[:120]}"
        log.warning(f"[表情学习] {msg}")
        return msg
    except Exception as e:
        msg = f"投喂异常: {e}"
        log.warning(f"[表情学习] {msg}")
        return msg


# ============ 蒸馏 + 投喂 主流程 ============


def distill_and_push(trigger: str = "auto") -> dict:
    """蒸馏最近样本并投喂麦麦。返回结果详情（供 WebUI 展示）。"""
    if _distill_running.is_set():
        return {"ok": False, "msg": "已有学习任务在运行"}
    _distill_running.set()
    try:
        with _lock:
            st = _load()
            start = st["distilled_total"]
            samples = st["samples"][start:]
        if not samples:
            return {"ok": False, "msg": "没有待学习的样本"}

        log.info(f"[表情学习] 开始蒸馏 {len(samples)} 条样本 (trigger={trigger})")
        items = _call_llm(_build_distill_prompt(samples))
        if items is None:
            return {"ok": False, "msg": "LLM 蒸馏失败（查看日志）"}

        push_msg = push_to_maibot(items)

        with _lock:
            st = _load()
            st["distilled_total"] = len(st["samples"])
            st["last_distill_ts"] = time.time()
            st["guide"] = items
            st["last_push_msg"] = push_msg
            _save()

        log.info(f"[表情学习] 蒸馏完成: {len(items)} 条 | {push_msg}")
        return {"ok": True, "learned": items, "push_msg": push_msg,
                "sample_count": len(samples)}
    finally:
        _distill_running.clear()


# ============ WebUI 统计 ============


def get_stats() -> dict:
    with _lock:
        st = _load()
        samples = list(st["samples"])
        guide = list(st["guide"])
        pending = len(samples) - st["distilled_total"]
        last_ts = st["last_distill_ts"]
        push_msg = st["last_push_msg"]

    counter = Counter()
    for s in samples:
        for c in s["codes"]:
            counter[c] += 1
    top = [{"code": c, "count": n} for c, n in counter.most_common(10)]

    return {
        "enabled": bool(getattr(config, "EMOJI_LEARN_ENABLED", True)),
        "threshold": int(getattr(config, "EMOJI_LEARN_THRESHOLD", 15) or 15),
        "samples_total": len(samples),
        "pending": pending,
        "running": _distill_running.is_set(),
        "top_codes": top,
        "guide": guide,
        "last_distill_ts": last_ts,
        "last_push_msg": push_msg,
        "recent_samples": [
            {"codes": s["codes"], "text": s["text"], "sender": s["sender"], "ts": s["ts"]}
            for s in samples[-10:][::-1]
        ],
    }
