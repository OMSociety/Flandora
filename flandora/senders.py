"""
消息发送器模块：UIA 发送器与工厂函数。

始终使用 UiaSender（把输入作为窗口消息投递给微信线程）。
是否允许抢前台由 config.json 的 allow_foreground_steal 决定；
隔离桌面模式下微信跑在独立桌面上，发送不会影响用户的桌面，因此该开关不再是前提。
"""

import logging
from .uia_sender import UiaSender

try:
    from . import config
except Exception:
    config = None

log = logging.getLogger("ob11-bridge")


def create_sender():
    """创建 UIA 消息发送器"""
    allow_steal = bool(getattr(config, "ALLOW_FOREGROUND_STEAL", False))
    desktop = ""
    if bool(getattr(config, "ISOLATED_DESKTOP", False)):
        desktop = getattr(config, "ISOLATED_DESKTOP_NAME", "WeBotSandbox")
        log.info(
            "使用 UIA 发送消息（隔离桌面 %s：微信在前台切换只发生在该桌面，"
            "不会打扰用户的桌面）", desktop
        )
    elif allow_steal:
        log.info("使用 UIA 发送消息（已允许发送时把微信窗口提到前台）")
    else:
        log.info(
            "使用 UIA 发送消息，但发送通道未启用"
            "（config.json 的 allow_foreground_steal 为 false，发送会失败并记日志）"
        )
    return UiaSender(allow_foreground_steal=allow_steal,
                     isolated_desktop=desktop)
