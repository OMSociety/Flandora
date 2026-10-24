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
    resp = _call({"action": "send_group_msg", "params": {"group_id": "abc", "message": []}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM
    assert "group_id" in resp["message"]


def test_bool_is_not_accepted_as_int():
    resp = _call({"action": "get_group_info", "params": {"group_id": True}})
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_BAD_PARAM


def test_cq_string_message_is_refused_instead_of_silently_sent_empty():
    """字符串消息此前会被逐字符遍历，结果零段发送却回 ok。"""
    resp = _call(
        {"action": "send_group_msg", "params": {"group_id": 1, "message": "[CQ:face,id=1]"}}
    )
    assert resp["status"] == "failed"
    assert resp["retcode"] == action_catalog.RETCODE_NOT_IMPLEMENTED
    assert "CQ" in resp["message"]


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
