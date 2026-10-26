"""口令的本地安全存储（Windows DPAPI）。

口令是 32 字节二进制，泄露即等于拿到整个账号的聊天库。这里不自己发明加密：
交给 Windows DPAPI，密钥绑在**当前用户的登录凭据**上，别的账户、别的机器拿到文件
也解不开。密钥只在本机本账户下解得开：换机器或换用户时重新取一次钥，这是有意为之。

落盘格式：一个小 JSON，含版本号、账号、DPAPI 密文（base64）。用 JSON 而不是裸
二进制，是为了让失败时的诊断能说清是「文件坏了」还是「解不开」。
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("wxkey.store")

#: 口令文件的落盘格式版本。DPAPI 额外熵与字段布局都属于格式的一部分：动过就要抬版本，
#: 既有文件会以「版本不符，请重新取钥」退出，而不是丢出一个读不懂的 DPAPI 错误码。
FORMAT_VERSION = 2
#: DPAPI 的「额外熵」：与密文一起参与派生，文件被换到别的用途会解不开。
#:
#: 这个值属于落盘格式的一部分：改动它会让本机已保存的密文解不开，必须重新取钥。
ENTROPY = b"Flandora/wxkey/v1"


class KeyStoreError(Exception):
    """口令存取失败。消息面向终端用户。"""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _Blob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt32():
    if not hasattr(ctypes, "WinDLL"):
        raise KeyStoreError("口令加密依赖 Windows DPAPI，当前系统不支持")
    return ctypes.WinDLL("crypt32", use_last_error=True)


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _local_free(ptr) -> None:
    try:
        _kernel32().LocalFree(ptr)
    except Exception:  # noqa: BLE001 - 释放失败不影响结果
        pass


def protect(secret: bytes) -> bytes:
    """用当前用户的 DPAPI 凭据加密。"""
    crypt32 = _crypt32()
    data_in = _blob(secret)
    entropy = _blob(ENTROPY)
    out = _Blob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(data_in), "微信口令", ctypes.byref(entropy), None, None, 0, ctypes.byref(out)
    )
    if not ok:
        raise KeyStoreError(f"DPAPI 加密失败（错误码 {ctypes.get_last_error()}）")
    try:
        return _blob_bytes(out)
    finally:
        _local_free(out.pbData)


def unprotect(cipher: bytes) -> bytes:
    """解密 DPAPI 密文。换用户或换机器后这里会失败，属预期。"""
    crypt32 = _crypt32()
    data_in = _blob(cipher)
    entropy = _blob(ENTROPY)
    out = _Blob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(data_in), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out)
    )
    if not ok:
        raise KeyStoreError(
            "解不开口令文件：它是在别的 Windows 账户或别的机器上生成的。请重新取一次密钥。"
        )
    try:
        return _blob_bytes(out)
    finally:
        _local_free(out.pbData)


@dataclass(frozen=True)
class StoredKey:
    """一次性读出的口令及其归属。"""

    passphrase: bytes
    wxid: str
    dll_version: str


class KeyStore:
    """口令文件的读写。默认落在配置目录下的 ``wx_key.dat``。"""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def save(self, passphrase: bytes, *, wxid: str, dll_version: str = "") -> None:
        if len(passphrase) < 16:
            raise KeyStoreError(f"口令长度异常（{len(passphrase)} 字节），拒绝保存")
        payload = {
            "version": FORMAT_VERSION,
            "wxid": wxid,
            "dll_version": dll_version,
            "cipher": base64.b64encode(protect(passphrase)).decode("ascii"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, self.path)
        log.info("口令已保存到 %s（DPAPI 加密，仅当前用户可解）", self.path)

    def load(self) -> Optional[StoredKey]:
        """读口令；文件不存在返回 None，文件坏了或解不开抛 KeyStoreError。"""
        if not self.path.is_file():
            return None
        try:
            with open(self.path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            raise KeyStoreError(f"口令文件读不出来：{self.path}（{exc}）") from exc
        version = payload.get("version")
        if version != FORMAT_VERSION:
            raise KeyStoreError(
                f"口令文件版本是 {version}，本程序只认 {FORMAT_VERSION}，请重新取钥"
            )
        try:
            cipher = base64.b64decode(payload["cipher"], validate=True)
        except (KeyError, ValueError) as exc:
            raise KeyStoreError(f"口令文件内容损坏：{self.path}") from exc
        return StoredKey(
            passphrase=unprotect(cipher),
            wxid=payload.get("wxid", ""),
            dll_version=payload.get("dll_version", ""),
        )

    def remove(self) -> bool:
        """删掉口令文件；返回是否真的删了东西。"""
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise KeyStoreError(f"删不掉口令文件 {self.path}：{exc}") from exc

    def describe(self) -> str:
        if not self.path.is_file():
            return f"尚未取钥（{self.path} 不存在）"
        try:
            key = self.load()
        except KeyStoreError as exc:
            return f"口令文件不可用：{exc}"
        if key is None:
            return "尚未取钥"
        ver = key.dll_version or "未知版本"
        return f"已有口令：账号 {key.wxid or '未知'}，取自微信 {ver}"
