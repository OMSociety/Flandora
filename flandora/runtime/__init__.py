"""进程级状态：生命周期与运行期对象。

架构边界：``LIFECYCLE`` 与 ``RUNTIME`` 是进程内唯一允许创建的可变全局对象，
所有桥接状态都必须落在这两者身上（或 ``ob_ids`` 的身份/名字索引上），
其它模块不得再引入模块级可变全局或共享状态模块。
"""

from .lifecycle import LIFECYCLE, Lifecycle, RunState, Snapshot
from .runtime import RUNTIME, AppRuntime

__all__ = [
    "LIFECYCLE",
    "RUNTIME",
    "AppRuntime",
    "Lifecycle",
    "RunState",
    "Snapshot",
]
