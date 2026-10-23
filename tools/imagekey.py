"""取图片解码密钥：独立命令行入口。

真正的扫描逻辑在 ``flandora/wx_image_key.py``——取钥向导要在 bridge 进程里
后台跑同一套代码，所以这里只负责命令行参数与人看的输出。

用法：
    python tools/imagekey.py                    # 交互，等用户在微信里打开图片
    python tools/imagekey.py --seconds 300      # 限定等待时长

--out 省略时写到程序目录下的 data/（打包 = exe 同级 data/，源码 = 仓库根 data/），
与 bridge 读图片密钥的位置一致。

退出码：
    0  取到并已保存
    2  没取到（图已缓存、微信没运行、或本地没有样本）
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from flandora import wx_image_key  # noqa: E402

EXIT_OK = 0
EXIT_NOT_FOUND = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从微信进程取图片解码密钥")
    parser.add_argument("--out", default=None,
                        help="输出目录，密钥写到该目录下的 wx_image_key.json；"
                             "默认 data/")
    parser.add_argument("--seconds", type=float, default=600.0,
                        help="持续扫描的秒数上限")
    args = parser.parse_args(argv)

    out_dir = args.out or os.path.dirname(wx_image_key.default_key_file())
    path = os.path.join(out_dir, "wx_image_key.json")

    print("=" * 62)
    print("接下来请在微信里打开一个新会话、或双击一张没看过的图片。")
    print("密钥只在微信解码图片的那几秒存在，所以扫描会一直跑着等你。")
    print("=" * 62)

    try:
        payload = wx_image_key.grab_key(
            out_path=path, seconds=args.seconds,
            on_status=lambda text: print(f"  {text}", flush=True),
            on_round=lambda n: print(f"  第 {n} 轮 …", end="\r", flush=True))
    except KeyboardInterrupt:
        print("\n已中断")
        return EXIT_NOT_FOUND
    except wx_image_key.ImageKeyError as exc:
        print(f"\n{exc}")
        return EXIT_NOT_FOUND

    print(f"\n找到 AES 密钥：{payload['aes_key']}")
    print(f"XOR 密钥：0x{payload['xor_key']:02x}")
    print(f"已写入 {path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
