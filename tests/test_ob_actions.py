"""动作派发层的错误语义。

一条硬规矩：目录里登记了但没接入的动作、以及目录里根本没有的动作，都必须回
``status=failed`` + 非零 retcode，并且把原因写进 message。假成功会让客户端把没
发生的事记进上下文，比直接报错难查得多。
"""

import asyncio
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora import action_catalog, ob_protocol  # noqa: E402


def _call(payload: dict) -> dict:
    return asyncio.run(ob_protocol._handle_ob_api(payload))


def test_unknown_action_is_rejected():
    resp = _call({"action": "definitely_not_an_action", "echo": "e1"})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_UNSUPPORTED
    assert resp["echo"] == "e1"
    assert "definitely_not_an_action" in resp["message"]


def test_unimplemented_actions_report_failure_not_ok():
    """所有 implemented=False 的动作都必须回失败，且带上目录里写的理由。"""
    checked = 0
    for action in action_catalog.ACTIONS:
        if action.implemented:
            continue
        resp = _call({"action": action.name, "params": {}, "echo": action.name})
        assert resp["status"] == "failed", f"{action.name} 回了成功"
        assert resp["retcode"] == action_catalog.RETCODE_NOT_IMPLEMENTED, action.name
        assert action.wip in resp["message"], action.name
        checked += 1
    assert checked > 0, "目录里没有未实现的动作，测试失去意义"


def test_missing_required_param_is_reported():
    resp = _call({"action": "send_group_msg", "params": {"message": []}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
    assert "group_id" in resp["message"]


def test_wrong_param_type_is_reported():
    resp = _call({"action": "send_group_msg", "params": {"group_id": {"a": 1}, "message": []}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
    assert "group_id" in resp["message"]


def test_handle_is_accepted_where_id_is_expected():
    """user_id / group_id 可以直接填微信标识，校验要放行（解析在派发层做）。"""
    assert (
        action_catalog.validate_params("send_private_msg", {"user_id": "wxid_abc", "message": []})
        == []
    )
    assert (
        action_catalog.validate_params(
            "send_group_msg", {"group_id": "123@chatroom", "message": []}
        )
        == []
    )
    assert action_catalog.validate_params("send_private_msg", {"user_id": "", "message": []}) != []


def test_bool_is_not_accepted_as_int():
    resp = _call({"action": "get_group_info", "params": {"group_id": True}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM


def test_empty_message_is_refused():
    """空消息没有可发送的内容，必须报错而不是回一个空成功的 message_id。"""
    resp = _call({"action": "send_private_msg", "params": {"user_id": 1, "message": []}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM


def test_extra_unknown_params_are_tolerated():
    """客户端多塞字段不该把请求打回。"""
    resp = _call({"action": "get_version_info", "params": {"client_note": "whatever"}})
    assert resp["status"] == "ok"
    assert resp["data"]["app_name"] == "flandora"
    assert resp["data"]["protocol_version"] == "v11"


def test_get_status_reports_receiver_and_sender_readiness():
    resp = _call({"action": "get_status"})
    assert resp["status"] == "ok"
    assert resp["data"]["receiver_ready"] is False  # 没有真实桥接实例
    assert resp["data"]["sender_ready"] is False
    assert resp["data"]["online"] is False


def test_can_send_record_is_honest():
    resp = _call({"action": "can_send_record"})
    assert resp["status"] == "ok"
    assert resp["data"]["yes"] is False


def test_contact_id_actions_round_trip(isolated_ids):
    """标识 ↔ 整数 ID 既能在动作面互查，也能整表导出。"""
    resp = _call({"action": "get_contact_id", "params": {"handle": "wxid_zhangsan"}})
    assert resp["status"] == "ok"
    oid = resp["data"]["id"]
    assert resp["data"] == {"handle": "wxid_zhangsan", "id": oid}

    resp = _call({"action": "get_contact_handle", "params": {"id": oid}})
    assert resp["status"] == "ok"
    assert resp["data"] == {"id": oid, "handle": "wxid_zhangsan"}

    resp = _call({"action": "get_contact_ids"})
    assert resp["status"] == "ok"
    assert resp["data"] == {"count": 1, "items": [{"handle": "wxid_zhangsan", "id": oid}]}


def test_get_contact_handle_reports_unknown_id(isolated_ids):
    free = next(n for n in range(1, 5000) if isolated_ids.handle_of(n) is None)
    resp = _call({"action": "get_contact_handle", "params": {"id": free}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_NOT_FOUND
    assert "未知的 id" in resp["message"]


def test_get_contact_id_needs_a_handle(isolated_ids):
    resp = _call({"action": "get_contact_id", "params": {"handle": "   "}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
