"""WeBot-Bridge：把微信个人号接入 OneBot v11（MaiBot / AstrBot 等）。

入口是顶层 `run_bridge.py`，不要直接跑包内模块——包内一律用相对导入。

    main             入口与生命周期
    config           配置读取
    state            进程级运行状态
    single_instance  单实例判重（命名互斥量）
    update_check     启动时查一次新版本
    webui/           本地配置界面（FastAPI + 前端产物 dist/）
    wxkey/           数据库口令的取钥与还原
    wx_crypto        SQLCipher 4 页解密
    wx_store         读本地库
    wx_image         图片 .dat 还原
    wx_desktop       隔离桌面
"""

__version__ = "1.0.0"
