"""WeBot-Bridge 入口。

源码运行时跑它；打包时 PyInstaller 也以它为入口脚本——入口脚本放在仓库根，
`webot_bridge` 包才会被完整收集。直接把包内模块当入口脚本会让相对导入失效。

    python run_bridge.py
"""
from webot_bridge.main import main

if __name__ == "__main__":
    raise SystemExit(main())
