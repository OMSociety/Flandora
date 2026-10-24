"""验证：图片密钥的账号归属检查。

场景——用户从账号 A 切到 B，密钥文件仍是 A 的。此时必须按「无密钥」处理，
而不是拿 A 的密钥去解 B 的图。
"""

import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO_ROOT, "flandora")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def with_cfg(fn):
    """在临时目录里造 config.json，让 config 只认这一份，跑完还原。

    用 ``FLANDORA_DATA_DIR`` 把默认数据目录钉到临时目录：否则源码目录里一旦
    真的存在 ``data/config.json``（从源码跑过一次就会有），它会被优先选中。
    """
    d = tempfile.mkdtemp(prefix="wwb-idx-")
    import shutil

    shutil.copy(os.path.join(PKG, "config.example.json"), os.path.join(d, "config.json"))
    old_env = os.environ.get("FLANDORA_DATA_DIR")
    os.environ["FLANDORA_DATA_DIR"] = d
    try:
        return fn(d)
    finally:
        if old_env is None:
            os.environ.pop("FLANDORA_DATA_DIR", None)
        else:
            os.environ["FLANDORA_DATA_DIR"] = old_env


def main() -> int:
    os.environ.pop("WX_IMAGE_KEY_FILE", None)

    def run(d):
        from flandora import (
            config,  # noqa: F401  触发配置加载
            wx_source,
        )

        # 写一份属于账号 other 的密钥
        with open(os.path.join(d, "wx_image_key.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {"wxid": "wxid_someone_else", "aes_key": "0123456789abcdef", "xor_key": 135}, fh
            )
        return wx_source

    wx_source = with_cfg(run)

    # 当前账号与密钥归属不一致 → 必须返回空
    key, xor = wx_source.load_image_key("wxid_current_user")
    assert key == b"", f"账号不符时不应返回密钥，实际 {key!r}"
    assert xor == -1, f"账号不符时 XOR 也应为 -1，实际 {xor}"
    print("  ok   账号不符 → 按无密钥处理")

    # 账号一致 → 正常返回
    key, xor = wx_source.load_image_key("wxid_someone_else")
    assert key == b"0123456789abcdef", key
    assert xor == 135, xor
    print("  ok   账号一致 → 正常加载")

    # 未指定当前账号（老调用方）→ 不阻断
    key, xor = wx_source.load_image_key("")
    assert key == b"0123456789abcdef", key
    print("  ok   未指定当前账号 → 不阻断")

    print("3/3 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
