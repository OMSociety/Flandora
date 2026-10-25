"""面板「安全与服务」：监听地址、HTTPS 证书与配置备份/恢复的校验。

下面的证书与私钥是一对临时自签的测试材料，只用来跑通「解析 → 落盘 → 再读回」
这条路径，没有任何真实用途，也不对应任何真实域名。
"""

import json
import os
import ssl
import stat

import pytest
from fastapi.responses import JSONResponse

from flandora import config
from flandora import main as bridge_main
from flandora.webui import app as webui_app
from flandora.webui import auth

TEST_CERT = """-----BEGIN CERTIFICATE-----
MIIDJzCCAg+gAwIBAgIUTU/SGZhWzCNVEVnFBuFgbiNXR2gwDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MCAXDTI2MTAyNDE0MTYyNFoYDzIxMjYw
OTMwMTQxNjI0WjAUMRIwEAYDVQQDDAlsb2NhbGhvc3QwggEiMA0GCSqGSIb3DQEB
AQUAA4IBDwAwggEKAoIBAQC5ffkNTuShEUxeU7VXUS9ciAJfMyLd4nCWFpQyXHTk
tgmLnAJy8k9qYseL0zlXu2T+ld3MYGuKUGhzpFU3sLVg4h5qtVd4bgGcLTQw36Up
4IRAP2Kb0pr8Lz7g41GWATzRJbACscaJlgnubiOtb1ByOOcmGukNlIZeG27NLIWc
En6YIc7NMFHY3JRxV2egpgXr9kna7iNNjP0q11HpWgdgvIti7auGI1T2QVqHfzjY
Smq/TkmOweETeHPtlPykchn208ifkrsjxY53Rcez8E0vYT5fpdUscrW3fQL9ztex
lhPZKNgQhxSOBGKiLZs+VO3MUnLcxyiXJLG8UgrXyzp1AgMBAAGjbzBtMB0GA1Ud
DgQWBBS4+jUiKt4eHm0jCBUIrHhvcrjlSDAfBgNVHSMEGDAWgBS4+jUiKt4eHm0j
CBUIrHhvcrjlSDAPBgNVHRMBAf8EBTADAQH/MBoGA1UdEQQTMBGCCWxvY2FsaG9z
dIcEfwAAATANBgkqhkiG9w0BAQsFAAOCAQEAkSpM/NFNZ0dTrEmY+Uby4QXKVOam
gP21O9g4GlAzIrjoFpCRpzCvTom1M9rQub+8PxOVwk88WKu2Un4UOe83ja75Vlfs
tSQcPKRgNtK1lL27sWUjRgCC5brZaKjuoUeEYmcAfZ0NIvbdRwad/UeZvR4ipTHX
2ukmgeC61f4AeDyL92k0BSwQqNKfEdJJSLTBQZRou010DxN9bzMIRL2NOl0b1g4M
XYZPsYIQj9O2u1PCLM9FVBCNZubc1/9RAkJHY8XSNp5guRsx6q/LuvlWGUrFg9KH
PFmSEJwPmdh5clW5IINtmRrk7cIKuV9kB3BwrE/U1i5PimMGp5ef+gs89g==
-----END CERTIFICATE-----
"""

TEST_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC5ffkNTuShEUxe
U7VXUS9ciAJfMyLd4nCWFpQyXHTktgmLnAJy8k9qYseL0zlXu2T+ld3MYGuKUGhz
pFU3sLVg4h5qtVd4bgGcLTQw36Up4IRAP2Kb0pr8Lz7g41GWATzRJbACscaJlgnu
biOtb1ByOOcmGukNlIZeG27NLIWcEn6YIc7NMFHY3JRxV2egpgXr9kna7iNNjP0q
11HpWgdgvIti7auGI1T2QVqHfzjYSmq/TkmOweETeHPtlPykchn208ifkrsjxY53
Rcez8E0vYT5fpdUscrW3fQL9ztexlhPZKNgQhxSOBGKiLZs+VO3MUnLcxyiXJLG8
UgrXyzp1AgMBAAECggEALBXck2UkwYMy+1g5yT7qWruYlLe6mbGvwHwtTKrcvV7m
dd/mVRtx9H71ni3dexR2tGs6A9EGviDOMm3kMxA0i4oyYHopB95dU5kKxnLg9kr+
I6bs8MvlOfqgnyJnyZcRtUOXyYwm+5xFT/2tTRMfs4YCqfPLYAbkyIkMlIb4WldD
ThUtLJyqnT4WPRX2xUbji7rnHOQhN44mhiQ1IkLxX23ngH0lPYShpR46UHMTLHpx
S+wysdACkH0c9r1KyeoGnSfkV8+XlDa+TrreNRWJ4LdpBz5V4iMTSPeJV3yeaMUT
4IOUMsxuSJ+T7cS0iwxKE55O/L/hBDKlRORFvvrrIQKBgQDgh5s8W7PlNk9KtrnU
q25GmprBrRFf3F8ltQ8/Ilw9caYBFjqGuzSFgjmqXewuNmu0zZkkc+Q4vO1mZegq
VjKVN4+rmgRRrZpfMqxjeW+qgJNU5EC4NAFaOekgD5589ZGE+sEgymNQKHfi7LFT
s1yRZCLomxKqd/l0qJqdcY5FXwKBgQDTfaZWdQF/zODa1eUrbSuBeiSoAHJseGvN
ZZBm0iqQswL3dUCG1hQIwGytbEekNp4wQGBcOsWnzc4FLuLWIbovS7dE3ciJvoM8
2jWDhcgMbMcte0YhTTwEYagY2uTqwLGlhg59+TYGlOsXv5luVbSP80owRWe+Fm5F
f+cc4KOcqwKBgCIHy5p5V6m88oWjbkTyerNOyXfjUEfUMcOYTqhF/+78I6n0gYhc
JVi65RySQbK7YtWzd6uTuRp0YBLuDSMlj2MYWbkrjc4HXudg2cn15NvVwv7R3ZFN
qIrRTiki1OWhnp2mF9Fte2Hx0EHfl/zUqYoLwFK69jhKGM/6ofyM8oBHAoGBAKMB
JUNdy1oFPwlr8sYVzoJs8fPK5GZWKkCVF0colUYRsyenZ6SFO8vDBABmhJE8x4/J
DnwHkHBVX4T/LjZ8qFPrt8WMaxQ/kkdEqBpG8nkhHiMf8F2aebemzTja/bUx77n0
KRdPDSTlgo07T7aKuDB7A6NQ2zTXdQZ8HlVTjschAoGAdME2j9fFjAPlkU79nuYy
ZtCJI14S+qQ8lHiOdFCIabew3SqWBYQ2dKuQXAsHRG8kprILQguxTrWG7iYgc6gt
1qc/VNWrGptGHkTSVBFGfY0ezBEshad37sBFKoKkLaeDHCe2fssEihB5u6NiLLpU
OYVshiUs3yIMeTgylfBL/38=
-----END PRIVATE KEY-----
"""


@pytest.fixture()
def saved(monkeypatch, tmp_path):
    """拦住磁盘读写，只留内存里的那一份配置；证书也写进临时目录。"""
    box = {"cfg": {}}
    monkeypatch.setattr(webui_app, "_read_config_file", lambda: dict(box["cfg"]))
    monkeypatch.setattr(webui_app, "_write_config_file", lambda cfg: box.update(cfg=dict(cfg)))
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)
    monkeypatch.setattr(config, "WEB_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "WEBUI_TLS", False)
    monkeypatch.setattr(config, "WEBUI_PASSWORD", "")
    monkeypatch.setattr(config, "WEBUI_TLS_CERT_FILE", str(tmp_path / "webui_cert.pem"))
    monkeypatch.setattr(config, "WEBUI_TLS_KEY_FILE", str(tmp_path / "webui_key.pem"))
    auth.sessions.clear()
    return box


# ---------- 监听地址 ----------


def test_bind_host_whitelist(saved):
    rest = webui_app.api_put_config({"web_bind_host": "192.168.1.5"}, webui_app.Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert saved["cfg"] == {}


def test_open_bind_needs_a_password_first(saved):
    rest = webui_app.api_put_config({"web_bind_host": "0.0.0.0"}, webui_app.Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert saved["cfg"] == {}


def test_open_bind_allowed_once_password_is_set(saved):
    webui_app.api_put_config({"webui_password": "secret123"}, webui_app.Response())

    rest = webui_app.api_put_config({"web_bind_host": "0.0.0.0"}, webui_app.Response())

    assert rest["ok"] is True
    assert "web_bind_host" in rest["restart_required"]
    assert saved["cfg"]["web_bind_host"] == "0.0.0.0"
    assert config.WEB_BIND_HOST == "0.0.0.0"


# ---------- HTTPS 证书 ----------


def test_cert_pair_is_validated_and_written(saved):
    rest = webui_app.api_put_config(
        {"webui_tls_cert": TEST_CERT, "webui_tls_key": TEST_KEY}, webui_app.Response()
    )

    assert rest["ok"] is True
    cert_path, key_path = config.WEBUI_TLS_CERT_FILE, config.WEBUI_TLS_KEY_FILE
    assert os.path.isfile(cert_path) and os.path.isfile(key_path)
    # 写下去的那一对必须真的能被 SSL 用起来
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert_path, key_path)
    # PEM 内容不进 config.json
    assert "webui_tls_cert" not in saved["cfg"]
    assert "webui_tls_key" not in saved["cfg"]
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600


def test_broken_pem_is_refused_before_writing(saved):
    rest = webui_app.api_put_config(
        {"webui_tls_cert": "看起来像证书", "webui_tls_key": "看起来像私钥"}, webui_app.Response()
    )

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert not os.path.exists(config.WEBUI_TLS_CERT_FILE)
    assert saved["cfg"] == {}


def test_half_a_pair_is_refused(saved):
    rest = webui_app.api_put_config({"webui_tls_cert": TEST_CERT}, webui_app.Response())

    assert isinstance(rest, JSONResponse)
    assert "一起装" in rest.body.decode("utf-8")
    assert not os.path.exists(config.WEBUI_TLS_CERT_FILE)


def test_enabling_tls_without_certificate_is_refused(saved):
    rest = webui_app.api_put_config({"webui_tls": True}, webui_app.Response())

    assert isinstance(rest, JSONResponse)
    assert rest.status_code == 400
    assert saved["cfg"] == {}


def test_enabling_tls_after_install(saved):
    webui_app.api_put_config(
        {"webui_tls_cert": TEST_CERT, "webui_tls_key": TEST_KEY}, webui_app.Response()
    )

    rest = webui_app.api_put_config({"webui_tls": True}, webui_app.Response())

    assert rest["ok"] is True
    assert "webui_tls" in rest["restart_required"]
    assert config.WEBUI_TLS is True


def test_tls_problem_follows_the_files_on_disk(saved, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WEBUI_TLS", True)

    assert config.webui_tls_problem() != ""

    webui_app.api_put_config(
        {"webui_tls_cert": TEST_CERT, "webui_tls_key": TEST_KEY}, webui_app.Response()
    )

    assert config.webui_tls_problem() == ""


# ---------- 备份 / 恢复 ----------


def test_backup_leaves_credentials_out_by_default(saved, monkeypatch):
    monkeypatch.setattr(
        webui_app,
        "_read_config_file",
        lambda: {"web_port": 4950, "webui_password": "hash", "ob_server_token": "tok"},
    )

    dump = webui_app.api_config_backup()

    assert "webui_password" not in dump["config"]
    assert "ob_server_token" not in dump["config"]
    assert sorted(dump["excluded_sensitive"]) == ["ob_server_token", "webui_password"]
    assert dump["include_sensitive"] is False


def test_backup_can_include_credentials(saved, monkeypatch):
    monkeypatch.setattr(
        webui_app, "_read_config_file", lambda: {"web_port": 4950, "ob_server_token": "tok"}
    )

    dump = webui_app.api_config_backup(include_sensitive=1)

    assert dump["config"]["ob_server_token"] == "tok"
    assert dump["excluded_sensitive"] == []
    assert dump["include_sensitive"] is True


def test_restore_refuses_files_that_are_not_backups(saved):
    assert webui_app.api_config_restore({"hello": "world"}).status_code == 400
    assert webui_app.api_config_restore({"config": {}}).status_code == 400


def test_restore_merges_and_backs_up_the_old_file(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_file))
    cfg_file.write_text(
        json.dumps({"web_port": 4950, "group_name_map": {"a@chatroom": "老群名"}}), encoding="utf-8"
    )
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)

    rest = webui_app.api_config_restore(
        {
            "config": {"web_port": 9000, "group_name_map": {"a@chatroom": "新群名"}},
            "include_sensitive": False,
        }
    )

    assert rest["ok"] is True
    assert sorted(rest["restored"]) == ["group_name_map", "web_port"]
    # 即时生效的那项不该再让人去重启
    assert sorted(rest["restart_required"]) == ["web_port"]
    written = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert written["web_port"] == 9000
    # 能即时生效的那一项当场生效
    assert config.GROUP_NAME_MAP == {"a@chatroom": "新群名"}
    old = json.loads(open(rest["backup_path"], encoding="utf-8").read())
    assert old == {"web_port": 4950, "group_name_map": {"a@chatroom": "老群名"}}


def test_restore_keeps_local_credentials_unless_asked(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_file))
    cfg_file.write_text(json.dumps({"webui_password": "local-hash"}), encoding="utf-8")
    monkeypatch.setattr(config, "WEBUI_PASSWORD", "local-hash")
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)

    rest = webui_app.api_config_restore(
        {"config": {"webui_password": "from-backup", "web_port": 9100}, "include_sensitive": False}
    )

    assert rest["password_restored"] is False
    assert config.WEBUI_PASSWORD == "local-hash"
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["webui_password"] == "local-hash"


def test_restore_with_credentials_replaces_password_and_sessions(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(cfg_file))
    cfg_file.write_text(json.dumps({"webui_password": "local-hash"}), encoding="utf-8")
    monkeypatch.setattr(config, "WEBUI_PASSWORD", "local-hash")
    monkeypatch.setattr(webui_app.monitor, "notify_status", lambda: None)
    token = auth.sessions.issue()

    rest = webui_app.api_config_restore(
        {"config": {"webui_password": "restored-hash"}, "include_sensitive": True}
    )

    assert rest["password_restored"] is True
    assert config.WEBUI_PASSWORD == "restored-hash"
    assert auth.sessions.valid(token) is False


# ---------- 配置接口 ----------


def test_config_response_reports_effective_listen_settings(monkeypatch):
    monkeypatch.setattr(webui_app, "_read_config_file", lambda: {"web_port": 4950})
    monkeypatch.setattr(config, "WEB_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(config, "WEBUI_TLS", True)

    cfg = webui_app.api_get_config()

    assert cfg["web_bind_host"] == "0.0.0.0"
    assert cfg["webui_tls"] is True


def test_config_response_keeps_what_the_file_says(monkeypatch):
    monkeypatch.setattr(
        webui_app, "_read_config_file", lambda: {"web_bind_host": "127.0.0.1", "webui_tls": False}
    )
    monkeypatch.setattr(config, "WEB_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(config, "WEBUI_TLS", True)

    cfg = webui_app.api_get_config()

    assert cfg["web_bind_host"] == "127.0.0.1"
    assert cfg["webui_tls"] is False


# ---------- 启动时的两道闸 ----------


def test_start_web_refuses_open_bind_without_password(monkeypatch):
    started = []
    monkeypatch.setattr(webui_app, "run", lambda host, port: started.append((host, port)))
    monkeypatch.setattr(config, "WEB_BIND_HOST", "0.0.0.0")
    monkeypatch.setattr(config, "WEBUI_PASSWORD", "")

    assert bridge_main.start_web() is False
    assert started == []


def test_start_web_refuses_broken_https(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(webui_app, "run", lambda host, port: started.append((host, port)))
    monkeypatch.setattr(config, "WEB_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "WEBUI_TLS", True)
    monkeypatch.setattr(config, "WEBUI_TLS_CERT_FILE", str(tmp_path / "missing_cert.pem"))
    monkeypatch.setattr(config, "WEBUI_TLS_KEY_FILE", str(tmp_path / "missing_key.pem"))

    assert bridge_main.start_web() is False
    assert started == []


def test_start_web_passes_the_configured_bind(monkeypatch):
    started = []
    monkeypatch.setattr(webui_app, "run", lambda host, port: started.append((host, port)))
    monkeypatch.setattr(config, "WEB_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "WEBUI_TLS", False)
    monkeypatch.setattr(config, "WEB_PORT", 4950)

    assert bridge_main.start_web() is True
    assert started == [("127.0.0.1", 4950)]
