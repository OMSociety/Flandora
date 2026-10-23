"""
微信 ↔ MaiBot 桥接（OneBot v11 版）
=====================================
消息接收：直接读微信本地库（微信自己加密的 SQLite 副本，只读）
AI 服务：MaiBot 通过微信适配器（OneBot v11 WS 客户端）接入
消息发送：bridge 接收 MaiBot 的 API 调用 → 隔离桌面上的微信窗口（UIA）

架构：
  微信本地库 ──轮询──→ webot_bridge/main.py ──WS 服务端(7999)──→ MaiBot 微信适配器
                    ↑ 监听端口，等待客户端连入
                    发送 OneBot 事件            返回 API 响应
"""

# 共享状态：所有模块通过 import state 访问这些变量
import threading
from typing import Optional

# ============ 状态控制 ============

running = False
paused = threading.Event()
paused.clear()
run_lock = threading.Lock()
bridge_thread = None
bridge_started_at = None  # 桥接启动时间戳（WebUI 计算运行时长用）

# ============ OneBot WebSocket 客户端管理 ============

_ob_ws = None          # WebSocket 连接实例（兼容旧引用）
_ob_ws_loop = None     # 事件循环
_ob_ws_ready = threading.Event()
_self_id_int = 0       # 启动时从 config 初始化
_self_name = "wechat-bot"  # 机器人昵称（给 get_login_info 用）


def _wxid_demouser10001(wxid: str) -> int:
    """将微信 wxid 映射为稳定的整数 ID。"""
    return abs(hash(wxid)) % (2**31)


# ============ 桥接实例 / 发送器 ============

# 语音转写配置（运行时可变，Web 面板保存后即时生效）
fun_asr_workspace_id = ""
fun_asr_region = "cn"
fun_asr_api_key = ""
fun_asr_model = "fun-asr-flash-2026-06-15"

bridge_instance = None
bridge_lock = threading.Lock()
sender_instance = None
_ob_id_to_contact: dict[int, str] = {}  # OneBot user_id/group_id → 微信联系名
ob_server_started = False

# 群聊回复模式（运行时可变，启动时从 config 初始化）
group_reply_mode = "mention"

# 新版本提示：update_check 查到有新版时填 {"latest","current","url"}，否则 None
update_available: Optional[dict] = None

# 图片描述配置（运行时可变，Web 面板保存后即时生效）
image_caption_provider = "ollama"
image_caption_model = "llava:7b"
image_caption_api_key = ""
image_caption_api_base = ""
image_caption_prompt = "请用中文简短描述这张图片的内容"
ollama_base_url = "http://127.0.0.1:61000"
ollama_timeout = 60
