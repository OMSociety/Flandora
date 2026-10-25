"""微信本地库的访问面。

桥接只读这个视图；唯一的写操作是 ``refresh(force=True)``——清缓存之后必须
让库立刻重新解密落盘，否则下一次查询会报「尚未解密」，消息通道会一直断到
重启。实现方是 ``wx_store.WxStore``。
"""

from __future__ import annotations

from typing import Any, Protocol


class Store(Protocol):
    """本地库的可观察面（只读查询 + 一个复位动作）。"""

    my_wxid: str
    """当前登录账号的 wxid。防自回环的第二道判定用它，不随改名漂移。"""

    _decrypted: set[str]
    """已解密出来的库文件名。为空表示还没解出任何库，查询会报「尚未解密」。"""

    def self_nickname(self) -> str:
        """本地通讯录里自己那行的昵称；读不到返回空串。"""

    def display_names(self) -> dict[str, str]:
        """wxid → 显示名（备注名优先）。"""

    def sessions(self) -> list[dict[str, Any]]:
        """会话列表（回填与增量轮询的起点）。"""

    def messages(
        self, username: str, after_local_id: int = 0, limit: int = 50
    ) -> list[dict[str, Any]]:
        """某个会话里 ``local_id`` 之后的消息；``after_local_id=0`` 取最新一批。"""

    def recent_messages(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        """某个会话最近的消息（历史查询用，与增量轮询无关）。"""

    def contacts(self) -> list[dict[str, Any]]:
        """联系人名册（好友、公众号、已删好友都过滤掉）。"""

    def groups(self) -> list[dict[str, Any]]:
        """群名册。"""

    def group_members(self, group: str) -> list[dict[str, Any]]:
        """群成员名册；群不存在返回空表。"""

    def image_ready(self) -> bool:
        """图片密钥是否就绪（没就绪时图片只有占位符）。"""

    def extract_image(self, msg_md5: str, local_id: int = 0) -> str | None:
        """把图片的 .dat 解成本地文件，返回路径；解不出返回 None。"""

    def refresh(self, force: bool = False) -> bool:
        """重新解密并载入本地库。

        写操作：``force=True`` 会重写解密副本。调用方是
        ``storage.clean_wx_cache``（清掉缓存后必须立刻重解）。
        """
