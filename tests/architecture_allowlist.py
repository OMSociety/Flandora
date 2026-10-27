"""架构守卫的棘轮名单：``flandora/`` 下已存在的模块级可变全局。

守卫规则见 ``tests/test_runtime_lifecycle.py``：

- 模块级 ``Assign`` / ``AnnAssign`` 的目标是「全大写常量」「双下划线魔术名」或
  「类型别名」时不计入；其余简单名字都算可变全局，必须在这里登记。
- 模块里有、名单里没有的名字让守卫变红：新增进程级状态请放进
  ``flandora/runtime/`` 的 ``LIFECYCLE`` / ``RUNTIME``（身份与名字索引放
  ``flandora/ob_ids.py``），确实需要长期存在的，才在这里显式登记。
- 名单里登记了、模块里已经没有的名字同样让守卫变红：删代码时同步删条目，
  名单不许变成垃圾桶。

现存条目都是非桥接生命周期的绑定：各模块的 ``log``；导入失败时的兜底
``config = None``；各模块自管的惰性单例（配置 store、ID 层句柄、网络管理器、
隔离桌面管理器、向导状态等）；面板的 ``app``、鉴权对象与各业务组的 ``router``；
核心侧事件总线及其日志转发开关。
"""

MODULE_STATE: dict[str, frozenset[str]] = {
    "flandora/bridge_core.py": frozenset({"log"}),
    "flandora/config/__init__.py": frozenset({"_listeners", "_lock", "_store", "log"}),
    "flandora/config/logging_setup.py": frozenset({"_handler", "_stream"}),
    "flandora/config/store.py": frozenset({"log"}),
    "flandora/domain/send.py": frozenset({"log"}),
    "flandora/main.py": frozenset({"_account_watcher", "log"}),
    "flandora/ob_ids.py": frozenset(
        {"_contact_ids", "_message_ids", "_names", "_names_lock", "_singleton_lock", "log"}
    ),
    "flandora/ob_network.py": frozenset({"_manager", "_manager_lock", "log"}),
    "flandora/ob_protocol.py": frozenset({"log"}),
    "flandora/ob_server.py": frozenset({"log"}),
    "flandora/ob_store.py": frozenset({"_store", "_store_lock", "log"}),
    "flandora/runtime/events.py": frozenset({"_relay_attached", "log"}),
    "flandora/senders.py": frozenset({"log"}),
    "flandora/single_instance.py": frozenset({"log"}),
    "flandora/stop_request.py": frozenset({"_handle_cache", "_kernel32"}),
    "flandora/storage.py": frozenset({"log"}),
    "flandora/uia_sender.py": frozenset({"log", "_com_ready"}),
    "flandora/update_check.py": frozenset({"log"}),
    "flandora/updater.py": frozenset({"log"}),
    "flandora/wechat_account.py": frozenset({"log"}),
    "flandora/wx_emoticon.py": frozenset({"log"}),
    "flandora/webui/api/auth.py": frozenset({"log", "router"}),
    "flandora/webui/api/autostart.py": frozenset({"log", "router"}),
    "flandora/webui/api/config.py": frozenset({"log", "router"}),
    "flandora/webui/api/contacts.py": frozenset({"log", "router"}),
    "flandora/webui/api/control.py": frozenset({"log", "router"}),
    "flandora/webui/api/debug.py": frozenset({"log", "router"}),
    "flandora/webui/api/desktop.py": frozenset({"log", "router"}),
    "flandora/webui/api/network.py": frozenset({"router"}),
    "flandora/webui/api/storage.py": frozenset({"log", "router"}),
    "flandora/webui/api/update.py": frozenset({"log", "router"}),
    "flandora/webui/api/windows.py": frozenset({"log", "router"}),
    "flandora/webui/api/wizard.py": frozenset({"log", "router"}),
    "flandora/webui/app.py": frozenset({"app", "log"}),
    "flandora/webui/auth.py": frozenset({"sessions", "throttle"}),
    "flandora/webui/deps.py": frozenset({"log"}),
    "flandora/wizard.py": frozenset({"_wizard", "_wizard_lock", "log"}),
    "flandora/wx_crypto.py": frozenset({"log"}),
    "flandora/wx_desktop.py": frozenset(
        {"_gdi32", "_kernel32", "_manager", "_manager_lock", "_user32", "log"}
    ),
    "flandora/wx_image.py": frozenset({"log"}),
    "flandora/wx_image_key.py": frozenset({"log"}),
    "flandora/wx_source.py": frozenset({"log"}),
    "flandora/wx_store.py": frozenset({"log"}),
    "flandora/wxkey/capture.py": frozenset({"log"}),
    "flandora/wxkey/locator.py": frozenset({"log"}),
    "flandora/wxkey/recover.py": frozenset({"log"}),
    "flandora/wxkey/store.py": frozenset({"log"}),
}
