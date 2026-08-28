"""
配置模块：加载 config.json，提供全局配置常量。

查找顺序（让打包后的 exe 能正确加载配置）：
  1. exe 同目录 / 源码目录（config.json）
  2. 当前工作目录（config.json）
  3. PyInstaller 临时目录（仅 config.example.json，作为最后兜底）
"""
import json
import os
import logging
import threading
import sys


def _find_config_path() -> str:
    """按优先级查找 config.json"""
    candidates = []
    if getattr(sys, "frozen", False):
        # PyInstaller 打包：exe 同目录
        candidates.append(os.path.dirname(sys.executable))
    # 源码：__file__ 所在目录
    candidates.append(os.path.dirname(os.path.abspath(__file__)))
    # 当前工作目录
    candidates.append(os.getcwd())
    for d in candidates:
        p = os.path.join(d, "config.json")
        if os.path.isfile(p):
            return p
    # 兜底：临时目录里的 example（仅用于提示用户）
    if getattr(sys, "_MEIPASS", None):
        candidate = os.path.join(sys._MEIPASS, "config.example.json")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "找不到 config.json。请把 config.example.json 复制为 config.json 并填好 token，"
        "放到 bridge.exe 同目录。"
    )


# ============ 配置 ============

CONFIG_FILE = _find_config_path()


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


config = load_config()

WE_FLOW_BASE_URL = config["weflow_base_url"]
ACCESS_TOKEN = config["access_token"]
ASTRBOT_ATTACHMENTS = config.get("astrbot_attachments", "")
# MaiBot 表情库目录（bridge 把微信收到的表情收纳到这里，MaiBot 定时维护自动注册）
MAIBOT_EMOJI_DIR = config.get("maibot_emoji_dir", "")
BOT_NICKNAMES = config["bot_nicknames"]
BOT_WXID = config.get("bot_wxid", "")
# 多开微信时指定机器人所在窗口句柄（0 = 自动取第一个微信窗口）
# 注意：微信重启后窗口句柄会变化，需在 WebUI 重新绑定
WECHAT_HWND = int(config.get("wechat_hwnd", 0) or 0)
# 发送方式已固定为 UIA 纯键盘模拟
BUFFER_SECONDS = config.get("buffer_seconds", 5)
WEB_PORT = config.get("web_port", 8766)
# 启动 bridge 时自动用默认浏览器打开 WebUI（false 关闭）
WEBUI_AUTO_OPEN = bool(config.get("webui_auto_open", True))
GROUP_REPLY_MODE = config.get("group_reply_mode", "mention")  # "mention" / "all"
# 上下文附带：mention 模式下，@ 触发时把最近 N 条群聊记录一并推给麦麦（0 = 关闭）
CONTEXT_MESSAGES = int(config.get("context_messages", 5) or 0)

# ====== 表情学习（观察群友使用微信自带表情 → 蒸馏成表达方式喂给麦麦） ======
EMOJI_LEARN_ENABLED = bool(config.get("emoji_learn_enabled", True))
EMOJI_LEARN_THRESHOLD = int(config.get("emoji_learn_threshold", 15) or 15)  # 积累多少条样本触发一次蒸馏
MAIBOT_WEBUI_URL = config.get("maibot_webui_url", "http://127.0.0.1:8001")  # 麦麦 WebUI 地址（投喂表达方式用）

# AstrBot OneBot 连接配置（bridge 作为 WebSocket 客户端连 AstrBot 的 aiocqhttp 服务端）
ASTRBOT_OB_URL = config.get("astrbot_ob_url", "ws://127.0.0.1:19777")

# ====== 对接 MaiBot napcat-adapter：bridge 作为 OneBot v11 WS 服务端 ======
# MaiBot napcat-adapter 是 WS 客户端，会连接到这里（模拟 NapCat 服务端）
OB_SERVER_HOST = config.get("ob_server_host", "127.0.0.1")
OB_SERVER_PORT = config.get("ob_server_port", 7998)
OB_SERVER_TOKEN = config.get("ob_server_token", "")
SELF_NAME = config.get("self_name", "wechat-bot")

# ====== 群 ID → 群名 映射 ======
# 微信搜索框无法搜 "@chatroom" 结尾的群 ID，需要填真实群名才能搜到。
# 格式: {"群ID": "微信里显示的群名"}
# 例: {"47622228067@chatroom": "我的测试群"}
GROUP_NAME_MAP = config.get("group_name_map", {})

# 图片描述配置（支持 ollama 或 openai 兼容 API）
IMAGE_CAPTION_PROVIDER = config.get("image_caption_provider", "ollama")  # "ollama" / "openai"
IMAGE_CAPTION_MODEL = config.get("image_caption_model", "llava:7b")
IMAGE_CAPTION_API_KEY = config.get("image_caption_api_key", "")
IMAGE_CAPTION_API_BASE = config.get("image_caption_api_base", "")
IMAGE_CAPTION_PROMPT = config.get("image_caption_prompt", "请用中文简短描述这张图片的内容")

# 语音转写配置（DashScope Fun-ASR）
# workspace_id 是阿里云百炼业务空间 ID；region: cn (北京) / intl (新加坡)
FUN_ASR_WORKSPACE_ID = config.get("fun_asr_workspace_id", "")
FUN_ASR_REGION = config.get("fun_asr_region", "cn")
FUN_ASR_API_KEY = config.get("fun_asr_api_key", "")
FUN_ASR_MODEL = config.get("fun_asr_model", "fun-asr-flash-2026-06-15")

# Ollama 图片描述配置（provider=ollama 时使用）
OLLAMA_BASE_URL = config.get("ollama_base_url", "http://127.0.0.1:61000")
OLLAMA_TIMEOUT = config.get("ollama_timeout", 60)

# ============ 日志 ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("bridge.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ob11-bridge")