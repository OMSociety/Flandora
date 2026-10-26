"""进程级状态：生命周期与运行期对象。

架构边界：桥接状态只有三个落点——``LIFECYCLE``（起停与暂停）、``RUNTIME``
（sender/bridge/入口占用/更新提示）与 ``ob_ids.SELF``（身份与名字索引）。别的模块
不许再存一份；确实需要模块级可变状态时，登记到 ``tests/architecture_allowlist.py``，
未登记会被架构用例拦下。
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
