"""测试公共设置。

两件事：
1. pytest 的临时目录钉在仓库内。系统临时目录在受限沙箱或 ACL 被改过的机器上
   可能连列目录都 PermissionError，于是所有用 ``tmp_path`` 的测试在 setup 阶段
   就集体失败——和被测代码毫无关系。根目录固定在 ``.pytest-tmp/``（已 gitignore），
   需要换位置时用 ``FLANDORA_TEST_TMP``。
2. ``isolated_ids`` 夹具：把 ID 层的单例换成临时目录里的实例，测试不会往真实的
   ``data/`` 里写 id_map.json 与 msg_ids.db。
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if not os.environ.get("PYTEST_DEBUG_TEMPROOT"):
    _root = os.environ.get("FLANDORA_TEST_TMP", os.path.join(REPO_ROOT, ".pytest-tmp"))
    # pytest 只会在根目录里开编号子目录，不会自己造根目录
    os.makedirs(_root, exist_ok=True)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = _root


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
