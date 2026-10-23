"""
消息发送器模块：UIA 发送器与工厂函数。

始终使用 UiaSender（把输入作为窗口消息投递给微信线程）。
是否允许抢前台由 config.json 的 allow_foreground_steal 决定。
"""

import logging
from uia_sender import UiaSender

try:
    import config
except Exception:
    config = None

log = logging.getLogger("ob11-bridge")


def create_sender():
    """创建 UIA 消息发送器"""
    allow_steal = bool(getattr(config, "ALLOW_FOREGROUND_STEAL", False))
    if allow_steal:
        log.info("使用 UIA 发送消息（已允许发送时把微信窗口提到前台）")
    else:
        log.info(
            "使用 UIA 发送消息，但发送通道未启用"
            "（config.json 的 allow_foreground_steal 为 false，发送会失败并记日志）"
        )
    return UiaSender(allow_foreground_steal=allow_steal)
