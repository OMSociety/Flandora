"""把消息投递进微信窗口的发送器协议。

实现方是 ``senders.create_sender()`` 选出的 ``uia_sender.UiaSender``；面板与
OneBot 发送路径都只通过这个协议调它，不靠 ``hasattr`` / ``getattr`` 试探
能力。窗口句柄走 ``window_handle()``，不直接读 ``_hwnd``——句柄只在发送器
内部有效，旁路读取会把「绑定中」的中间值当成事实。
"""

from __future__ import annotations

from typing import Protocol


class Sender(Protocol):
    """一次发送所需的全部能力。"""

    def send_text(self, contact: str, text: str) -> bool:
        """给 ``contact``（显示名）发一条文本；失败返回 False。"""

    def send_image(self, contact: str, image_path: str) -> bool:
        """发一张图片。"""

    def send_file(self, contact: str, file_path: str) -> bool:
        """以文件形式发一个本地文件（语音/视频走这条）。"""

    def send_mention_text(self, contact: str, mentions: list[str], text: str) -> bool:
        """在群里 @ ``mentions`` 里这些成员并附带文本。"""

    def rebind_window(self, hwnd: int) -> bool:
        """重新绑定微信窗口；``hwnd=0`` 表示恢复自动查找。"""

    def current_window_info(self) -> dict | None:
        """当前绑定窗口的信息；未绑定或句柄失效返回 None。"""

    def window_handle(self) -> int:
        """当前绑定的窗口句柄；未绑定为 0。"""

    def last_error(self) -> str:
        """最近一次发送失败的原因；空串表示没有记录。"""
