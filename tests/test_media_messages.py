"""语音、文件、视频、卡片的收取与发送。"""

from __future__ import annotations

import io
import os
import sqlite3
import time

import pytest

from flandora import bridge_core, config, ob_protocol, wx_source, wx_store, wx_voice
from flandora.bridge_core import Flandora
from flandora.domain.content import ContentKind

TALKER = "wxid_friend"


def _data(kind, **extra):
    base = {
        "rawid": "r1",
        "timestamp": 1_795_446_531,
        "content": "[语音] 2 秒",
        "sourceName": "朋友",
        "talkerName": "朋友",
        "talkerId": TALKER,
        "sessionId": TALKER,
        "sessionType": "private",
        "sender": TALKER,
        "senderName": "朋友",
        "msgType": kind,
        "localId": 7,
        "serverId": "99",
    }
    base.update(extra)
    return base


def _silk_blob() -> bytes:
    import pysilk

    pcm = b"\x00\x01" * 24000  # 1 秒
    out = io.BytesIO()
    pysilk.encode(io.BytesIO(pcm), out, 24000, 24000)
    return out.getvalue()  # pysilk 编码出的流自带微信那个 0x02 前缀


# ---------- 存储层取件 ----------


def _store(tmp_path) -> wx_store.WxStore:
    data = tmp_path / "data"
    work = tmp_path / "work"
    data.mkdir()
    work.mkdir()
    store = wx_store.WxStore(str(data), b"", str(work), my_wxid="wxid_me")
    store._decrypted.update({"media_0.db", "message_resource.db"})
    return store


def test_silk_blob_decodes_to_wav():
    wav = wx_voice.silk_to_wav(_silk_blob())
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


def test_non_silk_blob_is_an_error():
    with pytest.raises(wx_voice.VoiceDecodeError):
        wx_voice.silk_to_wav(b"\x00\x01\x02")


def test_voice_wav_reads_media_db_and_caches(tmp_path):
    store = _store(tmp_path)
    con = sqlite3.connect(os.path.join(store.work_dir, "media_0.db"))
    con.execute("CREATE TABLE Name2Id(user_name TEXT PRIMARY KEY)")
    con.execute(
        "CREATE TABLE VoiceInfo(chat_name_id INTEGER, create_time INTEGER, local_id INTEGER,"
        " svr_id INTEGER, voice_data BLOB, data_index TEXT DEFAULT '0')"
    )
    con.execute("INSERT INTO Name2Id VALUES (?)", (TALKER,))
    con.execute("INSERT INTO VoiceInfo VALUES (1, 1, 7, 99, ?, '0')", (_silk_blob(),))
    con.commit()
    con.close()

    path = store.voice_wav(TALKER, 7)
    assert path and path.endswith(".wav") and os.path.getsize(path) > 44
    assert store.voice_wav(TALKER, 7) == path
    assert store.voice_wav(TALKER, 8) is None
    # 本地编号对不上时按服务端号兜底
    assert store.voice_wav(TALKER, 5000, server_id="99")


def test_file_path_checks_size_and_prefers_message_month(tmp_path):
    store = _store(tmp_path)
    month = time.strftime("%Y-%m", time.localtime(1_795_446_531))
    folder = tmp_path / "data" / "msg" / "file" / month
    folder.mkdir(parents=True)
    (folder / "论文.docx").write_bytes(b"x" * 10)
    (folder / "论文(1).docx").write_bytes(b"y" * 20)

    assert store.file_path("论文.docx", 1_795_446_531, 10) == str(folder / "论文.docx")
    assert store.file_path("论文.docx", 1_795_446_531, 20) == str(folder / "论文(1).docx")
    assert store.file_path("论文.docx", 1_795_446_531, 30) is None
    assert store.file_path("不存在.docx", 1_795_446_531, 0) is None


def test_video_path_follows_resource_packed_info(tmp_path):
    store = _store(tmp_path)
    stem = "0fc4caec6f6ca5e1a0ed5cc42f881e60"
    vdir = tmp_path / "data" / "msg" / "video" / "2026-11"
    vdir.mkdir(parents=True)
    (vdir / f"{stem}.mp4").write_bytes(b"mp4")
    con = sqlite3.connect(os.path.join(store.work_dir, "message_resource.db"))
    con.execute("CREATE TABLE ChatName2Id(user_name TEXT PRIMARY KEY)")
    con.execute(
        "CREATE TABLE MessageResourceInfo(message_id INTEGER, chat_id INTEGER,"
        " message_local_id INTEGER, packed_info BLOB)"
    )
    con.execute("INSERT INTO ChatName2Id VALUES (?)", (TALKER,))
    con.execute(
        "INSERT INTO MessageResourceInfo VALUES (1, 1, 7, ?)",
        (b'\x12"\n ' + stem.encode(),),
    )
    con.commit()
    con.close()
    assert store.video_path(TALKER, 7) == str(vdir / f"{stem}.mp4")
    assert store.video_path(TALKER, 8) is None


def test_resolve_attachment_dispatch():
    class Stub:
        def voice_wav(self, talker, local_id, server_id=""):
            return "/w/a.wav"

        def video_path(self, talker, local_id):
            return None

        def file_path(self, name, create_time, size):
            return f"/f/{name}"

    voice = wx_source.resolve_attachment(Stub(), _data("voice"))
    assert voice == {"kind": "voice", "path": "/w/a.wav", "name": "a.wav"}
    assert wx_source.resolve_attachment(Stub(), _data("video")) == {}
    doc = wx_source.resolve_attachment(Stub(), _data("file", card={"title": "t.pdf", "size": "5"}))
    assert doc["path"] == "/f/t.pdf" and doc["name"] == "t.pdf"
    assert wx_source.resolve_attachment(Stub(), _data("text")) == {}


# ---------- 桥接层 ----------


class _NoStore:
    def display_names(self):
        return []

    def group_members(self, group):
        return []


@pytest.fixture
def pushed(monkeypatch):
    events = []
    monkeypatch.setattr(bridge_core, "push_event", lambda e: events.append(e) or 1)
    monkeypatch.setattr(config, "OB_MESSAGE_FORMAT", "array")
    return events


def _types(event):
    return [seg["type"] for seg in event["message"]]


def test_voice_with_body_pushes_only_a_record_segment(pushed):
    bridge = Flandora(sender=None)
    msg = bridge._entry_for(
        _data("voice", attachment={"kind": "voice", "path": "/w/a.wav", "name": "a.wav"})
    )
    assert msg.kind is ContentKind.TEXT and len(msg.attachments) == 1
    bridge._push_entry(msg)
    assert _types(pushed[0]) == ["record"]
    assert pushed[0]["message"][0]["data"]["file"] == "/w/a.wav"


def test_file_keeps_text_and_adds_file_segment_and_card(pushed):
    bridge = Flandora(sender=None)
    card = {"type": "文件", "title": "论文.docx"}
    msg = bridge._entry_for(
        _data(
            "file",
            content="[文件] 论文.docx\n191.4 KB",
            card=card,
            attachment={"kind": "file", "path": "/f/论文.docx", "name": "论文.docx"},
        )
    )
    bridge._push_entry(msg)
    assert _types(pushed[0]) == ["text", "file"]
    assert pushed[0]["card"] == card


def test_media_message_waits_for_the_body_then_pushes(monkeypatch, pushed):
    monkeypatch.setattr(bridge_core, "MEDIA_POLL_SECONDS", 0.01)
    monkeypatch.setattr(bridge_core.RUNTIME, "store", _NoStore(), raising=False)
    monkeypatch.setattr(bridge_core.LIFECYCLE, "should_continue", lambda: True)
    calls = {"n": 0}

    def fake_resolve(store, data):
        calls["n"] += 1
        if calls["n"] < 3:
            return {}
        return {"kind": "video", "path": "/v/a.mp4", "name": "a.mp4"}

    monkeypatch.setattr(bridge_core, "resolve_attachment", fake_resolve)
    bridge = Flandora(sender=None)
    bridge.process_media_message(bridge._entry_for(_data("video", content="[视频] 20 秒")))
    assert calls["n"] == 3
    assert _types(pushed[0]) == ["video"]


def test_media_message_times_out_to_text(monkeypatch, pushed):
    monkeypatch.setattr(bridge_core, "MEDIA_POLL_SECONDS", 0.01)
    monkeypatch.setattr(bridge_core, "MEDIA_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(bridge_core.RUNTIME, "store", _NoStore(), raising=False)
    monkeypatch.setattr(bridge_core.LIFECYCLE, "should_continue", lambda: True)
    monkeypatch.setattr(bridge_core, "resolve_attachment", lambda store, data: {})
    bridge = Flandora(sender=None)
    bridge.process_media_message(bridge._entry_for(_data("video", content="[视频] 20 秒")))
    assert _types(pushed[0]) == ["text"]
    assert pushed[0]["message"][0]["data"]["text"] == "[视频] 20 秒"


def test_card_text_event_has_card_field(pushed):
    bridge = Flandora(sender=None)
    bridge._push_entry(
        bridge._entry_for(
            _data("link", content="[链接] t\nhttps://x", card={"type": "链接", "url": "https://x"})
        )
    )
    assert pushed[0]["card"]["url"] == "https://x"


# ---------- 出站卡片降级 ----------


def test_share_segment_becomes_text():
    prep = ob_protocol._prepare_message(
        [{"type": "share", "data": {"url": "https://x.y/z", "title": "标题", "content": "摘要"}}]
    )
    assert [s.text for s in prep.segments] == ["标题\n摘要\nhttps://x.y/z"]
    assert not prep.failures


def test_json_card_takes_title_and_jump_url():
    doc = '{"meta":{"news":{"title":"某视频","jumpUrl":"https://b23.tv/a"}}}'
    prep = ob_protocol._prepare_message([{"type": "json", "data": {"data": doc}}])
    assert prep.segments[0].text == "某视频\nhttps://b23.tv/a"


def test_json_card_without_a_link_is_reported_as_failure():
    prep = ob_protocol._prepare_message([{"type": "json", "data": {"data": '{"a":1}'}}])
    assert not prep.segments and prep.failures
