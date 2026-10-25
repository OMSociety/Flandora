"""测试公共设置。

三件事：
1. pytest 的临时目录钉在仓库内。系统临时目录在受限沙箱或 ACL 被改过的机器上
   可能连列目录都 PermissionError，于是所有用 ``tmp_path`` 的测试在 setup 阶段
   就集体失败——和被测代码毫无关系。根目录固定在 ``.pytest-tmp/``（已 gitignore），
   需要换位置时用 ``FLANDORA_TEST_TMP``。
2. ``_config_session`` 夹具：整个会话的配置与日志都指向
   ``.pytest-tmp/config-session/``。不钉的话，第一次读配置属性就会按示例生成
   仓库里的 ``data/config.json``，日志也会写进真实数据目录。
3. ``isolated_ids`` / ``isolated_store`` 夹具：把 ID 层与消息存档的单例换成临时
   目录里的实例，测试不会往真实的 ``data/`` 里写 id_map.json、msg_ids.db。
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# SDK 在 sdk/python/ 下自成一体（它要能被单独装走），测试直接按源码路径导入
SDK_ROOT = os.path.join(REPO_ROOT, "sdk", "python")
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

if not os.environ.get("PYTEST_DEBUG_TEMPROOT"):
    _root = os.environ.get("FLANDORA_TEST_TMP", os.path.join(REPO_ROOT, ".pytest-tmp"))
    # pytest 只会在根目录里开编号子目录，不会自己造根目录
    os.makedirs(_root, exist_ok=True)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = _root


@pytest.fixture(scope="session", autouse=True)
def _config_session():
    """整场测试的配置与日志落在临时目录；store 与 handler 在收尾时复原。"""
    from flandora import config

    root = os.path.join(os.environ["PYTEST_DEBUG_TEMPROOT"], "config-session")
    os.makedirs(root, exist_ok=True)
    cfg_path = os.path.join(root, "config.json")
    if not os.path.exists(cfg_path):
        # 空配置就是全默认：避免首次读属性时按示例在测试目录里生成一份
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({}, f)
    with config.temporary(cfg_path):
        yield


@pytest.fixture
def isolated_ids(tmp_path):
    """把 ID 层单例指到临时目录，避免测试污染真实 data/。"""
    from flandora import ob_ids, state

    ob_ids.reset_singletons()
    ob_ids._contact_ids = ob_ids.ContactIds(str(tmp_path / "id_map.json"))
    ob_ids._message_ids = ob_ids.MessageIds(str(tmp_path / "msg_ids.db"))
    state._ob_id_to_contact.clear()
    try:
        yield ob_ids
    finally:
        ob_ids.reset_singletons()
        state._ob_id_to_contact.clear()


@pytest.fixture
def isolated_store(tmp_path):
    """把消息存档单例指到临时目录，测试不会往真实 data/messages.db 里写。"""
    from flandora import ob_store

    ob_store.reset_singletons()
    ob_store._store = ob_store.MessageStore(str(tmp_path / "messages.db"))
    try:
        yield ob_store
    finally:
        ob_store.reset_singletons()
