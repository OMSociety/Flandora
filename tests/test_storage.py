"""面板「存储」：占用统计与各类清理的行为。

每个用例都只动临时目录里的假数据——真实的 ``data/``、真实的日志文件与缓存
一次都不碰。这里最要紧的两条：

* 清理解密副本之后必须立刻强制重解，否则 ``_con()`` 会因为文件不在而报「尚未解密」；
* 消息存档的清理只由人显式触发，而且删完文件不会自己变小（要再 VACUUM）。
"""

import os
import time

import pytest
from fastapi.responses import JSONResponse

from flandora import config, ob_store, storage
from flandora.runtime import RUNTIME
from flandora.webui import app as webui_app


@pytest.fixture()
def workspace(monkeypatch, tmp_path):
    """数据目录、日志、缓存根全部钉进临时目录。"""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(RUNTIME, "bridge", None, raising=False)
    ob_store.reset_singletons()
    monkeypatch.setattr(ob_store, "_store", ob_store.MessageStore(str(data / "messages.db")))
    with config.temporary(
        str(data / "config.json"),
        overrides={
            "log_max_mb": 1,
            "log_backup_count": 2,
            "wx_work_dir": str(data / "wx_cache"),
        },
    ):
        try:
            yield data
        finally:
            ob_store.reset_singletons()


@pytest.fixture()
def put_ready(monkeypatch, workspace):
    """PUT 只动临时目录里的那一份配置。"""
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)
    return config.get_store()


def _write(path, text: str = "x" * 32) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _account_dir(data) -> str:
    """认一个账号的缓存目录出来，并按真实布局放好图片缓存与解密副本。"""
    root = os.path.join(str(data), "wx_cache", "wxid_test")
    _write(os.path.join(root, "session.db"), "d" * 64)
    _write(os.path.join(root, "session.db-shm"), "s" * 8)
    _write(os.path.join(root, "images", "a.dat.png"), "i" * 16)
    _write(os.path.join(root, "images", "b.wxgf"), "j" * 8)
    return root


class _FakeStore:
    """只记下 refresh 是怎么被调的。"""

    def __init__(self):
        self.calls = []
        self._decrypted = set()

    def refresh(self, force=False):
        self.calls.append(force)
        if force:
            self._decrypted = {"session.db"}
        return True


# ---------- 统计 ----------


def test_overview_counts_each_bucket(workspace):
    root = _account_dir(workspace)
    _write(os.path.join(str(workspace), "flandora.log"), "l" * 40)
    _write(os.path.join(str(workspace), "updates", "Flandora.zip"), "u" * 24)
    _write(os.path.join(str(workspace), "config.json"), "c" * 12)
    ob_store.message_store().save_event({"message_id": 1, "time": 1000, "post_type": "message"})

    got = storage.overview()

    assert got["data_dir"] == str(workspace)
    assert got["logs"]["bytes"] == 40
    assert got["messages"]["rows"] == 1
    assert got["messages"]["oldest"] == 1000
    assert got["images"] == {"bytes": 24, "files": 2}
    assert got["wx_cache"]["bytes"] == 72 and got["wx_cache"]["files"] == 2
    assert got["updates"]["bytes"] == 24
    assert got["total_files"] == 8  # 含存档库与配置文件
    # 分项之和 + 剩下那一小撮（配置、口令之类没单独列出来的）必须等于总量
    parts = (
        got["logs"]["bytes"]
        + got["messages"]["bytes"]
        + got["images"]["bytes"]
        + got["wx_cache"]["bytes"]
        + got["updates"]["bytes"]
    )
    assert got["total_bytes"] == parts + 12
    assert got["other_bytes"] == 12
    assert os.path.isdir(root)  # 统计是只读的，一个文件都不该动


def test_overview_survives_without_any_cache(workspace):
    got = storage.overview()

    assert got["images"] == {"bytes": 0, "files": 0}
    assert got["wx_cache"]["bytes"] == 0
    assert got["logs"]["bytes"] == 0


# ---------- 图片缓存 ----------


def test_clean_images_only_touches_images(workspace):
    root = _account_dir(workspace)

    out = storage.clean_images()

    assert out["removed_files"] == 2
    assert out["freed_bytes"] == 24
    assert not os.path.exists(os.path.join(root, "images", "a.dat.png"))
    # 解密副本还在：清图片缓存不该影响收件
    assert os.path.isfile(os.path.join(root, "session.db"))


# ---------- 微信数据缓存 ----------


def test_clean_wx_cache_redecrypts_at_once(workspace):
    root = _account_dir(workspace)
    fake = _FakeStore()
    RUNTIME.store = fake

    out = storage.clean_wx_cache()

    assert out["removed_files"] == 2
    assert out["freed_bytes"] == 72
    assert fake.calls == [True]  # 必须强制重解，否则消息通道会一直报「尚未解密」
    assert out["redecrypt_error"] == ""
    assert not os.path.exists(os.path.join(root, "session.db"))
    # 图片缓存不属于这一类，别顺手删了
    assert os.path.isfile(os.path.join(root, "images", "a.dat.png"))


def test_clean_wx_cache_without_a_running_source(workspace):
    _account_dir(workspace)

    out = storage.clean_wx_cache()

    assert out["removed_files"] == 2
    assert out["note"]  # 桥接没在跑：说清下次启动会重新解密，而不是假装成功
    assert out["redecrypt_error"] == ""


def test_clean_wx_cache_reports_a_failed_redecrypt(workspace):
    _account_dir(workspace)

    class _Broken:
        _decrypted = set()

        def refresh(self, force=False):
            raise RuntimeError("口令与账号对不上")

    RUNTIME.store = _Broken()

    out = storage.clean_wx_cache()

    assert "口令与账号对不上" in out["redecrypt_error"]


# ---------- 更新暂存 ----------


def test_clean_updates_removes_staging_and_registry(monkeypatch, workspace):
    _write(os.path.join(str(workspace), "updates", "Flandora-v1.2.3.zip"), "z" * 48)
    _write(os.path.join(str(workspace), "updates", "pending.json"), "{}")

    out = storage.clean_updates()

    assert out["freed_bytes"] == 50
    assert not os.path.exists(os.path.join(str(workspace), "updates", "pending.json"))
    assert os.path.isdir(os.path.join(str(workspace), "updates"))


# ---------- 日志 ----------


def _rotate_files(data) -> list[str]:
    return sorted(
        (n for n in os.listdir(str(data)) if n.startswith("flandora.log.")),
        key=lambda n: int(n.rsplit(".", 1)[-1]) if n.rsplit(".", 1)[-1].isdigit() else 0,
    )


def test_rotate_log_moves_current_aside(workspace):
    _write(config.LOG_FILE, "旧" * 10)

    out = storage.rotate_log()

    assert out["redecrypt_error"] == ""
    assert _rotate_files(workspace) == ["flandora.log.1"]
    assert os.path.getsize(config.LOG_FILE) == 0


def test_clean_old_logs_keeps_configured_count(workspace):
    for index in (1, 2, 3, 4):
        _write(f"{config.LOG_FILE}.{index}", "h" * 10)

    out = storage.clean_old_logs()  # LOG_BACKUP_COUNT = 2

    assert out["removed_files"] == 2
    assert out["freed_bytes"] == 20
    assert _rotate_files(workspace) == ["flandora.log.1", "flandora.log.2"]


def test_rotate_log_prunes_beyond_the_limit(workspace):
    for index in (1, 2):
        _write(f"{config.LOG_FILE}.{index}", "h" * 10)
    _write(config.LOG_FILE, "新" * 5)

    storage.rotate_log()

    assert _rotate_files(workspace) == ["flandora.log.1", "flandora.log.2"]
    assert "新" in open(f"{config.LOG_FILE}.1", encoding="utf-8").read()


# ---------- 消息存档 ----------


def _seed(store, times):
    for index, stamp in enumerate(times, start=1):
        store.save_event(
            {
                "message_id": index,
                "time": stamp,
                "post_type": "message",
                "message": [],
                "sender": {},
            }
        )


def test_prune_only_removes_older_rows(workspace):
    store = ob_store.message_store()
    now = int(time.time())
    _seed(store, [now - 30 * 86400, now - 2 * 86400, now - 3600])

    out = storage.messages_prune(7)

    assert out["removed_rows"] == 1
    assert out["rows"] == 2
    assert out["days"] == 7
    assert [row["message_id"] for row in store.recent(10)] == [3, 2]


def test_prune_leaves_the_file_to_vacuum(workspace):
    store = ob_store.message_store()
    now = int(time.time())
    # 每行塞满一点，删完才会明显看出文件没变小
    for index in range(1, 21):
        store.save_event(
            {
                "message_id": index,
                "time": now - 400 * 86400,
                "post_type": "message",
                "message": [],
                "sender": {},
                "raw_message": "x" * 4000,
            }
        )
    path = os.path.join(str(workspace), "messages.db")
    before = os.path.getsize(path)

    storage.messages_prune(1)
    after_prune = os.path.getsize(path)
    out = storage.messages_vacuum()

    assert after_prune == before  # 删行不还空间，这正是要单独 VACUUM 的原因
    assert out["freed_bytes"] > 0
    assert out["bytes_after"] < before


def test_clear_empties_the_archive(workspace):
    store = ob_store.message_store()
    now = int(time.time())
    _seed(store, [now, now - 60, now - 120])

    out = storage.messages_clear()

    assert out["removed_rows"] == 3
    assert out["rows"] == 0
    assert store.recent(10) == []


# ---------- 端点 ----------


def test_storage_endpoint_returns_the_overview(workspace):
    _account_dir(workspace)

    got = webui_app.api_storage()

    assert got["ok"] is True
    assert got["images"]["files"] == 2
    assert "total_bytes" in got


def test_clean_endpoint_rejects_an_unknown_target(workspace):
    rest = webui_app.api_storage_clean({"target": "../etc"})

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400


def test_clean_endpoint_runs_a_known_target(workspace):
    _account_dir(workspace)

    got = webui_app.api_storage_clean({"target": "images"})

    assert got["ok"] is True
    assert got["target"] == "images"
    assert got["removed_files"] == 2


def test_messages_endpoint_validates_days(workspace):
    rest = webui_app.api_storage_messages({"action": "prune", "days": 0})
    assert isinstance(rest, JSONResponse) and rest.status_code == 400

    rest = webui_app.api_storage_messages({"action": "prune", "days": "七天"})
    assert isinstance(rest, JSONResponse) and rest.status_code == 400

    rest = webui_app.api_storage_messages({"action": "压缩"})
    assert isinstance(rest, JSONResponse) and rest.status_code == 400


def test_messages_endpoint_prunes(workspace):
    store = ob_store.message_store()
    now = int(time.time())
    _seed(store, [now - 30 * 86400, now])

    got = webui_app.api_storage_messages({"action": "prune", "days": 1})

    assert got["ok"] is True
    assert got["removed_rows"] == 1
    assert got["rows"] == 1


# ---------- 日志策略 ----------


def test_log_policy_applies_without_a_restart(put_ready):
    got = webui_app.api_put_config({"log_max_mb": 4, "log_backup_count": 3}, webui_app.Response())

    assert got["ok"] is True
    assert got["restart_required"] == []
    assert put_ready.read_raw()["log_max_mb"] == 4
    assert config.LOG_MAX_MB == 4 and config.LOG_BACKUP_COUNT == 3
    assert config.LOG_HANDLER.maxBytes == 4 * 1024 * 1024
    assert config.LOG_HANDLER.backupCount == 3


def test_log_policy_range_is_enforced(put_ready):
    for bad in (
        {"log_max_mb": 0},
        {"log_max_mb": 2048},
        {"log_backup_count": 0},
        {"log_backup_count": 99},
    ):
        rest = webui_app.api_put_config(dict(bad), webui_app.Response())
        assert isinstance(rest, JSONResponse) and rest.status_code == 400

    assert put_ready.read_raw() == {}
    assert config.LOG_MAX_MB == 1  # 还是夹具里那个值，没被越界的请求改掉


def test_log_policy_rejects_non_numbers(put_ready):
    rest = webui_app.api_put_config({"log_max_mb": "八兆"}, webui_app.Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400


def test_config_endpoint_reports_effective_log_policy(workspace):
    got = webui_app.api_get_config()

    assert got["log_max_mb"] == 1
    assert got["log_backup_count"] == 2
