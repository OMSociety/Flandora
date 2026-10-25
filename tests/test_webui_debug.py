import asyncio
import json

from flandora import action_catalog, ob_store
from flandora.webui.api import debug as debug_api


def test_actions_endpoint_exposes_catalog_shape():
    data = debug_api.api_debug_actions()
    assert set(data) == {"categories", "actions"}
    assert len(data["actions"]) == len(action_catalog.ACTIONS)
    first = data["actions"][0]
    for key in ("name", "category", "summary", "implemented", "params", "readOnly"):
        assert key in first
    assert data["categories"]


def test_invoke_returns_catalog_failure_for_unknown_action():
    result = asyncio.run(debug_api.api_debug_invoke({"action": "no_such_action", "params": {}}))
    assert result["ok"] is False
    assert result["response"]["retcode"] == action_catalog.RETCODE_UNSUPPORTED
    assert result["elapsed_ms"] >= 0


def test_invoke_rejects_empty_action():
    response = asyncio.run(debug_api.api_debug_invoke({"action": "", "params": {}}))
    assert response.status_code == 400
    assert b"action" in response.body


def test_invoke_rejects_non_object_params():
    response = asyncio.run(debug_api.api_debug_invoke({"action": "get_status", "params": ["x"]}))
    assert response.status_code == 400
    assert b"params" in response.body


def test_invoke_passes_echo_and_reports_success(isolated_ids):
    result = asyncio.run(debug_api.api_debug_invoke({"action": "get_login_info", "params": {}}))
    assert result["ok"] is True
    assert result["response"]["echo"] == "webui"
    assert "user_id" in result["response"]["data"]


def test_events_endpoint_reads_the_archive(isolated_store):
    event = {
        "time": 1770000000,
        "self_id": 1,
        "post_type": "message",
        "message_type": "group",
        "message_id": 7,
        "group_id": 1001,
        "user_id": 2002,
        "nickname": "林同学",
        "raw_message": "今晚八点开始",
        "message": [{"type": "text", "data": {"text": "今晚八点开始"}}],
    }
    ob_store.message_store().save_event(event)

    data = debug_api.api_debug_events(limit=10)
    assert [e["message_id"] for e in data["events"]] == [7]
    assert json.loads(json.dumps(data["events"][0]))["raw_message"] == "今晚八点开始"


def test_events_endpoint_clamps_limit(isolated_store):
    for index in range(1, 6):
        ob_store.message_store().save_event(
            {
                "time": 1770000000 + index,
                "self_id": 1,
                "post_type": "message",
                "message_type": "private",
                "message_id": index,
                "user_id": 2002,
                "raw_message": f"第 {index} 条",
                "message": [{"type": "text", "data": {"text": f"第 {index} 条"}}],
            }
        )
    data = debug_api.api_debug_events(limit=2)
    assert [e["message_id"] for e in data["events"]] == [5, 4]
