# MaiBot Bridge 管理器

MaiBot 与微信（WeFlow）的桥接进程（Akasha-WeChat bridge）的图形化管理工具。
双击 `启动.bat` 即可运行；关闭窗口会最小化到系统托盘。

## 功能

| 功能 | 说明 |
|---|---|
| ▶ 启动 / ■ 停止 / ↻ 重启 | 一键管理 bridge 进程 |
| 实时状态 | 进程 PID、端口 7999 连接数、WeFlow 健康、微信进程数 |
| 实时日志 | 滚动显示 bridge.log（自动 tail） |
| 打开 WebUI | 一键打开 `http://127.0.0.1:8766` |
| 打开日志目录 | 文件管理器直接定位 |
| 开机自启动 | 写入 Startup 目录（bat 脚本），勾选即生效 |
| 系统托盘 | 关闭主窗口最小化到托盘，右键托盘图标可彻底退出 |

## 使用

1. **首次启动**：双击 `启动.bat`（或 `bridge_manager.py`）
2. **完全退出**：右键托盘图标 → 退出（或主窗口右下角"退出"按钮）

> 关闭主窗口 = 最小化到托盘，bridge 仍在后台运行。

## 文件

| 文件 | 用途 |
|---|---|
| `bridge_manager.py` | 主程序（tkinter GUI） |
| `log_tail.py` | 子进程，tail bridge.log 并通过 stdout 推回主进程 |
| `启动.bat` | 双击启动器（隐藏 cmd 窗口） |
| `README.md` | 本文档 |

## 依赖

- 源码运行需要 Python 3.10+（打包版免安装）
- tkinter（系统自带）
- Pillow + pystray（托盘图标）

```powershell
# 安装（如果缺失）
python -m pip install pystray Pillow
```

> 打包后无需 Python 环境。源码运行时 `bridge_manager.py` 会自动探测系统 Python。

## 故障排查

- **状态显示"未运行"但 7999 端口有人在用** → 通常是别的程序占了端口。打开"打开日志目录"看 `bridge.log`。
- **托盘图标不出现** → 系统托盘被设为"从不显示图标"，去 Windows 设置 → 个性化 → 任务栏 → 其他系统托盘图标打开。
- **开机自启动没生效** → Startup 目录权限不够；以管理员身份运行一次主程序，点"开机自启动"。

## 路径配置

如需修改 bridge 目录，编辑 `bridge_manager.py` 顶部的常量：

```python
BRIDGE_DIR = HERE.parent / "wechat-weflow-bridge-ob11"
```