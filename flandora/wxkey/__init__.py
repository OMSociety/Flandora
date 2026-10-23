"""微信数据库取钥。

子模块：
    locator  从磁盘上的 Weixin.dll 结构化定位密钥设置函数（不依赖版本表）
    capture  以 Frida 注入微信进程，在登录时截获口令材料
    recover  把截获到的材料还原成口令，并用页 1 的 HMAC 验证
"""
