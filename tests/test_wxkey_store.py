"""口令文件格式的回归测试（纯离线，不需要真微信）。

重点守住一条：DPAPI 的额外熵属于落盘格式的一部分。它一旦被改名之类的事情改动，
所有用户已保存的密文都会解不开，且现场表现是「口令文件是在别的账户生成的」这种
会误导人的提示。这里把熵值本身钉死，改动它会立刻让测试变红。
"""
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flandora.wxkey import store as store_mod
from flandora.wxkey.store import KeyStore, KeyStoreError


def test_entropy_is_frozen_format_constant():
    """熵值必须保持首发版本的值，改它等于让所有既有密文失效。"""
    assert store_mod.ENTROPY == b"WeBot-Bridge/wxkey/v1", (
        "DPAPI 额外熵被改动，既有用户的口令文件将无法解密："
        f"当前为 {store_mod.ENTROPY!r}"
    )


def test_roundtrip_same_user():
    """同一用户下保存后应立即能读回同样的字节。"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "wx_key.dat")
        ks = KeyStore(path)
        secret = bytes(range(32))
        ks.save(secret, wxid="wxid_demo", dll_version="4.1.15.56")
        loaded = ks.load()
        assert loaded is not None
        assert loaded.passphrase == secret
        assert loaded.wxid == "wxid_demo"
        assert loaded.dll_version == "4.1.15.56"


def test_missing_file_returns_none():
    """没取过钥时 load() 返回 None，而不是抛异常。"""
    with tempfile.TemporaryDirectory() as d:
        assert KeyStore(os.path.join(d, "nope.dat")).load() is None


def test_tampered_cipher_raises():
    """密文被改动后必须报错，不能静默返回错误口令。"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "wx_key.dat")
        ks = KeyStore(path)
        ks.save(bytes(range(32)), wxid="wxid_demo")
        with open(path, encoding="utf-8") as fh:
            import json
            payload = json.load(fh)
        raw = bytearray(__import__("base64").b64decode(payload["cipher"]))
        raw[0] ^= 0xFF
        payload["cipher"] = __import__("base64").b64encode(bytes(raw)).decode()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        try:
            ks.load()
        except KeyStoreError:
            pass
        else:
            raise AssertionError("密文被篡改后应当报 KeyStoreError")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
