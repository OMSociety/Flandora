"""SDK 的动作快照必须和服务端目录一致。

快照是 `tools/gen_sdk_catalog.py` 生成的；改了 `flandora/action_catalog.py` 却忘了重新
生成，这个测试会直接失败并把差异指出来。
"""

from flandora_sdk import actions as sdk_actions
from flandora_sdk import get_action, implemented_actions, list_actions

from flandora import action_catalog


def test_snapshot_matches_server_catalog():
    expected = action_catalog.catalog_dict()
    assert sdk_actions.categories() == expected["categories"]
    assert sdk_actions.catalog()["actions"] == expected["actions"], (
        "动作目录变了：跑 tools/gen_sdk_catalog.py 重新生成 _catalog.json"
    )


def test_list_actions_can_filter_by_category():
    category = sdk_actions.categories()[0]
    filtered = list_actions(category)
    assert filtered
    assert all(a["category"] == category for a in filtered)


def test_get_action_returns_the_spec():
    action = get_action("get_login_info")
    assert action["implemented"] is True


def test_get_action_rejects_unknown_name():
    from flandora_sdk import ActionNotFound

    try:
        get_action("definitely_not_an_action")
    except ActionNotFound as err:
        assert "definitely_not_an_action" in str(err)
    else:
        raise AssertionError("未知动作应当抛 ActionNotFound")


def test_implemented_actions_are_a_subset():
    done = implemented_actions()
    names = {a["name"] for a in done}
    assert "send_group_msg" in names
    assert all(a["implemented"] for a in done)
    assert len(done) < len(list_actions())
