"""进程级状态的归属：生命周期状态机、运行期对象、身份与名字索引、架构守卫。

这里既验行为，也验边界：``flandora/runtime/`` 是进程级状态的唯一落点，
``ob_ids`` 管身份与名字索引；其它模块再长出一个模块级可变全局，守卫就变红。
"""

import ast
import os
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from architecture_allowlist import MODULE_STATE

from flandora import ob_ids
from flandora.runtime import LIFECYCLE, RUNTIME, RunState

FLANDORA_DIR = Path(REPO_ROOT) / "flandora"


# ===== 生命周期状态机 =====


def test_state_machine_full_path():
    """STOPPED → STARTING → RUNNING → STOPPING → STOPPED，每步的可见状态都对。"""
    assert LIFECYCLE.snapshot().state is RunState.STOPPED

    assert LIFECYCLE.begin_start() is True
    starting = LIFECYCLE.snapshot()
    assert starting.state is RunState.STARTING
    assert starting.is_active is True
    assert starting.is_online is True
    assert starting.started_at is not None

    LIFECYCLE.mark_running()
    assert LIFECYCLE.snapshot().state is RunState.RUNNING

    assert LIFECYCLE.begin_stop() is True
    assert LIFECYCLE.snapshot().state is RunState.STOPPING
    assert LIFECYCLE.snapshot().is_active is False
    assert LIFECYCLE.should_continue() is False

    LIFECYCLE.mark_stopped()
    stopped = LIFECYCLE.snapshot()
    assert stopped.state is RunState.STOPPED
    assert stopped.started_at is None
    assert stopped.is_active is False
    assert stopped.is_online is False


def test_duplicate_start_is_rejected():
    """已起或在起时再 start 返回 False；停过之后才能重新起。"""
    assert LIFECYCLE.begin_start() is True
    assert LIFECYCLE.begin_start() is False
    LIFECYCLE.mark_running()
    assert LIFECYCLE.begin_start() is False

    assert LIFECYCLE.begin_stop() is True
    assert LIFECYCLE.begin_stop() is False
    LIFECYCLE.mark_stopped()

    assert LIFECYCLE.begin_start() is True


def test_should_continue_is_false_before_start_and_after_stop():
    assert LIFECYCLE.should_continue() is False
    LIFECYCLE.begin_start()
    assert LIFECYCLE.should_continue() is True
    LIFECYCLE.begin_stop()
    assert LIFECYCLE.should_continue() is False


def test_pause_and_resume_only_when_active():
    """暂停只在已起时成立；重复切换返回 False，不改变现状。"""
    assert LIFECYCLE.pause() is False
    assert LIFECYCLE.resume() is False

    LIFECYCLE.begin_start()
    assert LIFECYCLE.snapshot().is_online is True
    assert LIFECYCLE.pause() is True
    paused = LIFECYCLE.snapshot()
    assert paused.paused is True
    assert paused.is_active is True  # 暂停不影响「已起」
    assert paused.is_online is False
    assert LIFECYCLE.pause() is False

    assert LIFECYCLE.resume() is True
    assert LIFECYCLE.snapshot().is_online is True
    assert LIFECYCLE.resume() is False

    LIFECYCLE.begin_stop()
    LIFECYCLE.mark_stopped()
    assert LIFECYCLE.pause() is False


def test_join_reports_timeout_honestly():
    """线程还没退就返回 False；退出了才返回 True。"""
    assert LIFECYCLE.join(0.01) is False  # 没登记线程

    LIFECYCLE.begin_start()
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    LIFECYCLE.attach_thread(thread)
    thread.start()
    try:
        assert LIFECYCLE.join(0.05) is False
    finally:
        release.set()
    assert LIFECYCLE.join(2.0) is True


# ===== 运行期对象 =====


def test_claim_ob_server_is_atomic():
    assert RUNTIME.claim_ob_server() is True
    assert RUNTIME.claim_ob_server() is False

    RUNTIME.release_ob_server()
    assert RUNTIME.claim_ob_server() is True


def test_claim_ob_server_only_one_thread_wins():
    """并发抢入口时只能有一个线程拿到所有权。"""
    results = []
    barrier = threading.Barrier(8)

    def take():
        barrier.wait()
        results.append(RUNTIME.claim_ob_server())

    threads = [threading.Thread(target=take) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert results.count(True) == 1
    assert results.count(False) == 7


def test_online_flags_are_one_definition():
    """心跳与 get_status 共用同一份 online/good 判定：暂停与未挂实例都要如实反映。"""
    assert RUNTIME.online_flags() == {"online": False, "good": False}

    LIFECYCLE.begin_start()
    LIFECYCLE.mark_running()
    # 起来但收件侧实例还没挂上：不算在线
    assert RUNTIME.online_flags() == {"online": False, "good": False}

    RUNTIME.bridge = object()
    # 桥接在跑但收件源还没接上：仍不算在线——微信没登录时 online 不能报 True
    assert RUNTIME.online_flags() == {"online": False, "good": False}

    RUNTIME.store = object()
    assert RUNTIME.online_flags() == {"online": True, "good": False}

    RUNTIME.sender = object()
    assert RUNTIME.online_flags() == {"online": True, "good": True}

    assert LIFECYCLE.pause() is True
    assert RUNTIME.online_flags() == {"online": False, "good": False}

    LIFECYCLE.resume()
    assert RUNTIME.online_flags() == {"online": True, "good": True}

    LIFECYCLE.begin_stop()
    assert RUNTIME.online_flags() == {"online": False, "good": False}


def test_self_identity_switches_id_and_nickname_together(isolated_ids):
    ob_ids.SELF.set_wxid("wxid_alice")
    first = ob_ids.SELF.user_id()
    assert first == isolated_ids.contact_id("wxid_alice")
    assert ob_ids.SELF.wxid() == "wxid_alice"

    ob_ids.SELF.set_nickname("Alice")
    assert ob_ids.SELF.nickname() == "Alice"

    # 换账号：user_id 重新分配，昵称缓存一起作废
    ob_ids.SELF.set_wxid("wxid_bob")
    assert ob_ids.SELF.user_id() == isolated_ids.contact_id("wxid_bob")
    assert ob_ids.SELF.user_id() != first
    assert ob_ids.SELF.nickname() == ""
    assert ob_ids.SELF.wxid() == "wxid_bob"


def test_name_snapshot_is_a_copy(isolated_ids):
    ob_ids.remember_name(7, "王五")
    snapshot = ob_ids.name_snapshot()
    assert snapshot == {7: "王五"}

    snapshot[7] = "改掉"
    snapshot[8] = "新增"
    assert ob_ids.lookup_name(7) == "王五"
    assert ob_ids.lookup_name(8) == ""

    ob_ids.remember_name(7, "王五五")
    assert ob_ids.name_snapshot() == {7: "王五五"}
    ob_ids.clear_names()
    assert ob_ids.name_snapshot() == {}


# ===== 架构守卫 =====


def _flandora_modules() -> list[Path]:
    return sorted(FLANDORA_DIR.rglob("*.py"))


def _is_constant_name(name: str) -> bool:
    """全大写常量（含 ``_UPPER_CASE``）与双下划线魔术名不算可变全局。"""
    if name.startswith("__") and name.endswith("__"):
        return True
    return name == name.upper() and any(ch.isalpha() for ch in name)


def _is_type_alias(value) -> bool:
    """右值是类型表达式时按类型别名处理。"""
    return isinstance(value, (ast.Subscript, ast.BinOp, ast.Tuple, ast.Attribute))


def _module_state_names(path: Path) -> set[str]:
    """模块层的可变全局名字；函数与类体内部不算。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()

    def visit(node) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Tuple):
                        names.add("<解包赋值>")
                    elif isinstance(target, ast.Name) and not _is_constant_name(target.id):
                        if not _is_type_alias(child.value):
                            names.add(target.id)
            elif (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and not _is_constant_name(child.target.id)
            ):
                names.add(child.target.id)
            visit(child)

    visit(tree)
    return names


def _state_imports(path: Path) -> list[str]:
    """模块里对包内 ``state`` 模块的导入。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "state" or alias.name.endswith(".state"):
                    hits.append(f"以 {alias.name} 为名导入")
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "state" or node.module.endswith(".state")):
                hits.append(f"从 {node.module} 导入")
            for alias in node.names:
                if alias.name == "state":
                    hits.append(f"从 {node.module or '.'} 导入了包内 state 模块")
    return hits


def test_no_module_imports_state():
    """进程级状态只从 flandora.runtime 取；包里没有 state 模块。"""
    offenders = {}
    for path in _flandora_modules():
        hits = _state_imports(path)
        if hits:
            offenders[path.relative_to(FLANDORA_DIR).as_posix()] = hits
    assert offenders == {}, f"进程级状态请用 flandora.runtime 的 LIFECYCLE/RUNTIME：{offenders}"


def test_no_unregistered_module_level_state():
    """模块级可变全局必须逐条登记；登记过的名字删掉后也要销账。"""
    registered = set(MODULE_STATE)
    seen = set()
    unregistered = {}
    stale = {}

    for path in _flandora_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        seen.add(rel)
        actual = _module_state_names(path)
        allowed = MODULE_STATE.get(rel, frozenset())
        extra = sorted(actual - allowed)
        missing = sorted(allowed - actual)
        if extra:
            unregistered[rel] = extra
        if missing:
            stale[rel] = missing

    for rel in sorted(registered - seen):
        stale[rel] = ["<模块已不存在>"]

    assert unregistered == {}, (
        "新增进程级状态请放进 flandora/runtime/ 的 LIFECYCLE/RUNTIME，"
        f"或在 tests/architecture_allowlist.py 显式登记：{unregistered}"
    )
    assert stale == {}, f"名单里登记的名字已不存在，请同步删条目：{stale}"


def main() -> int:
    """脚本直跑：用例依赖 fixture，委托给 pytest，保证两条入口同一套断言。"""
    return pytest.main([os.path.abspath(__file__), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
