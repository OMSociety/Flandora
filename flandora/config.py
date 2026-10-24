"""
配置模块：加载 config.json，提供全局配置常量。

打包后的可写数据（config.json、日志、进程号、微信口令与图片密钥）都放在 config.json
所在目录；打包后是 exe 同级的 data/，源码运行时是仓库根下的 data/。

config.json 查找顺序：
  1. 程序目录/data/config.json
  2. 打包：exe 同目录/config.json；源码：包目录/config.json
  3. 当前工作目录/config.json
  4. 都没有时，把打包进来的 config.example.json 写到 1 的位置，当作首次启动的默认配置
"""

import json
import logging
import os
import sys


def _default_data_dir() -> str:
    """还没确定 config.json 位置时的默认可写目录：程序目录下的 data/。

    环境变量 ``FLANDORA_DATA_DIR`` 可以把这个位置指到别处——数据与程序分开放，
    或者同一台机器上跑多份互不干扰。它只决定「去哪里找、去哪里生成 config.json」；
    找到之后，日志、口令、游标一律以 config.json 所在的目录为准。
    """
    override = os.environ.get("FLANDORA_DATA_DIR", "")
    if override:
        return os.path.abspath(override)
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "data")
    # 源码运行时包目录的上一级就是仓库根
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def data_dir() -> str:
    """可写数据目录：config.json 所在目录。

    日志、进程号、微信口令与图片密钥都落在这里，与配置同处。打包后是 exe 同级的
    data/，源码运行时是仓库根下的 data/。
    """
    if CONFIG_FILE:
        return os.path.dirname(CONFIG_FILE)
    return _default_data_dir()


def _find_config_path() -> tuple:
    """按优先级查找 config.json；都没有时用打包进来的示例生成一份。

    返回 (路径, 是否是这次生成的)。生成的那份落在 data_dir()，也就是可写数据目录
    ——不能把示例本身当配置用，它在打包后位于 PyInstaller 的临时解包目录，
    进程一退就被清掉，日志与微信游标会跟着一起丢。
    """
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "data"))
        candidates.append(exe_dir)
    # 源码：仓库根/data，再退到包目录
    candidates.append(_default_data_dir())
    candidates.append(os.path.dirname(os.path.abspath(__file__)))
    # 当前工作目录
    candidates.append(os.getcwd())
    for d in candidates:
        p = os.path.join(d, "config.json")
        if os.path.isfile(p):
            return p, False

    example = _find_example()
    if example:
        target = os.path.join(_default_data_dir(), "config.json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(example, "r", encoding="utf-8") as src:
            text = src.read()
        with open(target, "w", encoding="utf-8") as dst:
            dst.write(text)
        return target, True

    raise FileNotFoundError(
        "找不到 config.json，也没有可用的 config.example.json。"
        f"请把 config.example.json 复制为 config.json，放到 {_default_data_dir()}。"
    )


def _find_example() -> str:
    """打包进来的配置示例；源码运行时用包目录里那份。"""
    packed = getattr(sys, "_MEIPASS", "")
    if packed:
        candidate = os.path.join(packed, "config.example.json")
        if os.path.isfile(candidate):
            return candidate
    candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.example.json")
    return candidate if os.path.isfile(candidate) else ""


# ============ 配置 ============

CONFIG_FILE, CONFIG_GENERATED = _find_config_path()


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


config = load_config()

ASTRBOT_ATTACHMENTS = config.get("astrbot_attachments", "")
BOT_NICKNAMES = config["bot_nicknames"]
BOT_WXID = config.get("bot_wxid", "")
# 多开微信时指定机器人所在窗口句柄（0 = 自动取第一个微信窗口）
# 注意：微信重启后窗口句柄会变化，需在 WebUI 重新绑定
WECHAT_HWND = int(config.get("wechat_hwnd", 0) or 0)
# 微信数据根目录（用于识别当前登录账号；留空 = 自动解析系统「文档」目录下的 xwechat_files）
WECHAT_FILES_ROOT = config.get("wechat_files_root", "")
# 允许发送消息时把微信窗口提到前台（默认关闭）
# 微信收到投递进来的鼠标/键盘消息就会把自己升到前台，这是它的行为，发送器无法避免；
# 开启后发送器会在发完立刻把前台还给原窗口，但发送期间前台确实会短暂切到微信。
ALLOW_FOREGROUND_STEAL = bool(config.get("allow_foreground_steal", False))

# ============ 收件源 ============
# 收件走微信本地库，不依赖任何第三方插件。
# 微信口令文件。留空 = 配置文件同目录下的 wx_key.dat
WX_KEY_FILE = config.get("wx_key_file", "")
# 解密结果与游标的存放目录。留空 = 配置文件同目录下的 wx_cache
WX_WORK_DIR = config.get("wx_work_dir", "")
# 首次启动时回填多少秒内的历史消息（0 = 只收启动之后的新消息）
WX_BACKFILL_SECONDS = int(config.get("wx_backfill_seconds", 0) or 0)
# 轮询本地库的间隔（秒）
WX_POLL_SECONDS = float(config.get("wx_poll_seconds", 1.0) or 1.0)
# 图片密钥文件（JSON，含 aes_key 与 xor_key）。留空 = 配置文件同目录下的
# wx_image_key.json。缺它时图片消息退回占位符，文本通道不受影响。
WX_IMAGE_KEY_FILE = config.get("wx_image_key_file", "")
# 取数据库口令时手动指定的函数地址（0 = 自动定位）。自动定位依赖微信 DLL 里的调用
# 形态，微信改版后可能失手；那时在这里填一个地址即可继续，不必等本程序适配新版本。
# 形式同 0x6148a0，不必正好是函数开头，落在函数里就行。
WXKEY_MANUAL_RVA = int(config.get("wxkey_manual_rva", 0) or 0)
# 发送方式已固定为 UIA 纯键盘模拟
BUFFER_SECONDS = config.get("buffer_seconds", 5)
WEB_PORT = config.get("web_port", 8766)
# 启动 bridge 时自动用默认浏览器打开 WebUI（false 关闭）
WEBUI_AUTO_OPEN = bool(config.get("webui_auto_open", True))

# ====== OneBot v11 服务端 ======
# bridge 作为 WS 服务端，任意 OneBot v11 客户端（适配器、框架、脚本）都可连接
OB_SERVER_HOST = config.get("ob_server_host", "127.0.0.1")
OB_SERVER_PORT = config.get("ob_server_port", 7999)
OB_SERVER_TOKEN = config.get("ob_server_token", "")
SELF_NAME = config.get("self_name", "wechat-bot")
# 事件里 message 字段的格式：array = 消息段数组（默认），string = CQ 码字符串。
# raw_message 始终是 CQ 码字符串。填其它值时按 array 处理并在启动日志里告警。
_OB_FORMAT_RAW = str(config.get("ob_message_format", "array") or "array").strip().lower()
OB_MESSAGE_FORMAT = _OB_FORMAT_RAW if _OB_FORMAT_RAW in ("array", "string") else "array"

# ====== 隔离桌面模式 ======
# 默认开启：微信由 bridge 启动在一个独立的 Windows 桌面上，向它投递输入时切换的
# 是那张桌面的前台，用户自己所在的桌面完全不受打扰——发送通道因此可以长期开着。
# 前提两条：微信必须由 bridge 启动（微信有单实例互斥，用户桌面上已经开着微信时
# 隔离实例只会停在登录界面）；首次登录要在 WebUI 里看实况画面、用手机确认。
# 设为 false 则回到另一种形态：微信由用户自己启动，发送时它会短暂到前台、发完归还。
ISOLATED_DESKTOP = bool(config.get("isolated_desktop", True))
# 隔离桌面的名称，一般不用改
ISOLATED_DESKTOP_NAME = config.get("isolated_desktop_name", "WeBotSandbox")
# 微信可执行文件路径。留空则自动探测常见安装位置。
WECHAT_EXE = config.get("wechat_exe", "")

# ====== 群 ID → 群名 映射 ======
# 微信搜索框无法搜 "@chatroom" 结尾的群 ID，需要填真实群名才能搜到。
# 格式: {"群ID": "微信里显示的群名"}
# 例: {"47622228067@chatroom": "我的测试群"}
GROUP_NAME_MAP = config.get("group_name_map", {})


# ============ 日志 ============

LOG_FILE = os.path.join(data_dir(), "flandora.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ob11-bridge")

if _OB_FORMAT_RAW != OB_MESSAGE_FORMAT:
    log.warning(
        "ob_message_format=%r 不认识，已按 array 处理（可选：array / string）", _OB_FORMAT_RAW
    )
